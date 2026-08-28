"""Cloud Run entrypoint for the clean Hefesto rebuild."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from uuid import UUID

from google.cloud import storage

from .contracts import AxisProposalResult, AxisFeedbackDecisionResult, PlanningRequest, PlanningRun, SourceLineage
from .persistence import GcpEditorialPersistence, GcpPersistenceSettings
from .planning import PlanningOrchestrator

MAX_REQUEST_BYTES = 50 * 1024 * 1024


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _read_request(location: str, *, project: str) -> dict[str, Any]:
    if location.startswith("gs://"):
        parsed = urlparse(location)
        object_name = parsed.path.lstrip("/")
        if not parsed.netloc or not object_name:
            raise ValueError("invalid Cloud Storage request URI")
        payload = storage.Client(project=project).bucket(parsed.netloc).blob(object_name).download_as_bytes()
    else:
        payload = Path(location).read_bytes()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("planning request exceeds 50 MiB")
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("planning request must be a JSON object")
    return parsed


def load_planning_request(location: str, *, project: str) -> PlanningRequest:
    return PlanningRequest.model_validate(_read_request(location, project=project))


def _feedback(encoded: str) -> str:
    try:
        value = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8").strip()
    except Exception as exc:
        raise ValueError("invalid feedback encoding") from exc
    if not value:
        raise ValueError("feedback cannot be empty")
    return value


def _planning_persistence() -> GcpEditorialPersistence:
    project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    return GcpEditorialPersistence.from_settings(GcpPersistenceSettings(project=project, bucket=bucket))


def _graph_extraction_runtime():
    from .runtime import VertexRuntimeSettings, build_graph_extraction_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return build_graph_extraction_runtime(VertexRuntimeSettings(project=project, location=location))


def _graph_indexing_runtime():
    from .runtime import VertexRuntimeSettings, build_graph_indexing_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return build_graph_indexing_runtime(VertexRuntimeSettings(project=project, location=location))


def _axis_proposal_runtime():
    from .runtime import VertexRuntimeSettings, build_axis_proposal_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return build_axis_proposal_runtime(VertexRuntimeSettings(project=project, location=location))


def _axis_feedback_runtime():
    from .runtime import VertexRuntimeSettings, build_axis_feedback_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return build_axis_feedback_runtime(VertexRuntimeSettings(project=project, location=location))


def run_extract_graph_command(source_lineage_id: UUID) -> SourceLineage:
    from .graph_extraction import GraphExtractionOrchestrator, LlamaIndexChunkGraphExtractor

    persistence = _planning_persistence()
    runtime = _graph_extraction_runtime()
    extractor = LlamaIndexChunkGraphExtractor(runtime)
    return GraphExtractionOrchestrator(persistence, extractor, model=runtime.model).execute(source_lineage_id)


def run_index_graph_command(source_lineage_id: UUID) -> SourceLineage:
    from .graph_assembly import GraphAssemblyOrchestrator

    persistence = _planning_persistence()
    runtime = _graph_indexing_runtime()
    return GraphAssemblyOrchestrator(persistence, runtime).execute(source_lineage_id)


def run_propose_axis_command(planning_run_id: UUID) -> AxisProposalResult:
    from .axis_proposal import (
        AxisProposalOrchestrator,
        LlamaIndexAxisEvidenceReviewer,
        LlamaIndexAxisProposer,
        LlamaIndexUnderstandingSynthesizer,
    )

    persistence = _planning_persistence()
    runtime = _axis_proposal_runtime()
    return AxisProposalOrchestrator(
        persistence,
        runtime,
        LlamaIndexUnderstandingSynthesizer(runtime),
        LlamaIndexAxisProposer(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
    ).execute(planning_run_id)


def _axis_feedback_orchestrator():
    from .axis_feedback import (
        AxisFeedbackOrchestrator,
        LlamaIndexDecisionReviser,
        LlamaIndexImpactAssessor,
        LlamaIndexRecalibrator,
        LlamaIndexSupportAssessor,
    )
    from .axis_proposal import LlamaIndexAxisEvidenceReviewer

    persistence = _planning_persistence()
    runtime = _axis_feedback_runtime()
    return AxisFeedbackOrchestrator(
        persistence,
        runtime,
        LlamaIndexImpactAssessor(runtime),
        LlamaIndexSupportAssessor(runtime),
        LlamaIndexRecalibrator(runtime),
        LlamaIndexDecisionReviser(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
    )


def run_decide_axis_feedback_command(planning_run_id: UUID, feedback: str) -> AxisFeedbackDecisionResult:
    return _axis_feedback_orchestrator().decide(planning_run_id, feedback)


def run_refuse_axis_enrichment_command(planning_run_id: UUID) -> AxisFeedbackDecisionResult:
    return _axis_feedback_orchestrator().refuse_enrichment(planning_run_id)


def run_await_axis_enrichment_command(planning_run_id: UUID) -> AxisFeedbackDecisionResult:
    return _axis_feedback_orchestrator().await_enrichment(planning_run_id)


def run_plan_command(location: str) -> PlanningRun:
    project = _required("GOOGLE_CLOUD_PROJECT")
    request = load_planning_request(location, project=project)
    return PlanningOrchestrator(_planning_persistence()).execute(request)


def _application():
    from .application import HefestoApplication
    from .runtime import VertexRuntimeSettings, build_vertex_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return HefestoApplication(
        runtime=build_vertex_runtime(VertexRuntimeSettings(project=project, location=location)),
        persistence=GcpEditorialPersistence.from_settings(GcpPersistenceSettings(project=project, bucket=bucket)),
    )


async def _run(args: argparse.Namespace) -> None:
    if args.command == "plan":
        result = run_plan_command(args.request)
        print(result.model_dump_json())
        return
    if args.command == "extract-graph":
        result = run_extract_graph_command(args.source_lineage_id)
        print(result.model_dump_json())
        return
    if args.command == "index-graph":
        result = run_index_graph_command(args.source_lineage_id)
        print(result.model_dump_json())
        return
    if args.command == "propose-axis":
        result = run_propose_axis_command(args.planning_run_id)
        print(result.model_dump_json())
        return
    if args.command == "decide-axis-feedback":
        result = run_decide_axis_feedback_command(args.planning_run_id, _feedback(args.feedback_b64))
        print(result.model_dump_json())
        return
    if args.command == "refuse-axis-enrichment":
        result = run_refuse_axis_enrichment_command(args.planning_run_id)
        print(result.model_dump_json())
        return
    if args.command == "await-axis-enrichment":
        result = run_await_axis_enrichment_command(args.planning_run_id)
        print(result.model_dump_json())
        return
    app = _application()
    if args.command == "revise-axis":
        result = await app.revise_axis(args.narrative_axis_id, _feedback(args.feedback_b64))
    elif args.command == "approve-axis":
        result = await app.approve_axis(args.narrative_axis_id)
    elif args.command == "revise-project":
        result = await app.revise_project(args.editorial_project_id, _feedback(args.feedback_b64))
    elif args.command == "approve-project":
        result = await app.approve_project(args.editorial_project_id)
    elif args.command == "produce":
        result = await app.produce(args.editorial_project_id)
    else:
        raise ValueError(f"unsupported command: {args.command}")
    print(result.model_dump_json())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ComunicAmundo V4 Hefesto clean rebuild")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="validate the request, prepare source chunks, and persist a resumable PlanningRun")
    plan.add_argument("--request", required=True)
    extract_graph = commands.add_parser("extract-graph", help="extract one persisted chunk at a time until graph_extractions_ready")
    extract_graph.add_argument("--source-lineage-id", type=UUID, required=True)
    index_graph = commands.add_parser("index-graph", help="assemble persisted graph extractions and index until graph_ready")
    index_graph.add_argument("--source-lineage-id", type=UUID, required=True)
    propose_axis = commands.add_parser(
        "propose-axis",
        help="persist editorial understanding and propose a Narrative Axis until axis_proposed",
    )
    propose_axis.add_argument("--planning-run-id", type=UUID, required=True)
    decide_feedback = commands.add_parser(
        "decide-axis-feedback",
        help="assess impact and support of natural-language Axis feedback without skipping to a rewrite",
    )
    decide_feedback.add_argument("--planning-run-id", type=UUID, required=True)
    decide_feedback.add_argument("--feedback-b64", required=True)
    refuse_enrichment = commands.add_parser(
        "refuse-axis-enrichment",
        help="refuse additional sources and recalibrate the pending contribution to residual support",
    )
    refuse_enrichment.add_argument("--planning-run-id", type=UUID, required=True)
    await_enrichment = commands.add_parser(
        "await-axis-enrichment",
        help="keep waiting for human enrichment without searching or ingesting sources",
    )
    await_enrichment.add_argument("--planning-run-id", type=UUID, required=True)
    revise_axis = commands.add_parser("revise-axis")
    revise_axis.add_argument("--narrative-axis-id", type=UUID, required=True)
    revise_axis.add_argument("--feedback-b64", required=True)
    approve_axis = commands.add_parser("approve-axis")
    approve_axis.add_argument("--narrative-axis-id", type=UUID, required=True)
    revise_project = commands.add_parser("revise-project")
    revise_project.add_argument("--editorial-project-id", type=UUID, required=True)
    revise_project.add_argument("--feedback-b64", required=True)
    approve_project = commands.add_parser("approve-project")
    approve_project.add_argument("--editorial-project-id", type=UUID, required=True)
    produce = commands.add_parser("produce")
    produce.add_argument("--editorial-project-id", type=UUID, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
