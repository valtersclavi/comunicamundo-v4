"""Cloud Run entrypoint for the clean Hefesto rebuild."""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from uuid import UUID

from google.cloud import firestore, storage

from .contracts import AxisFeedbackReviewResult, AxisProposalResult, EditorialProjectProposalResult, EnrichmentResolutionChoice, PlanningRequest, PlanningRun, SourceLineage
from .persistence import GcpEditorialPersistence, GcpPersistenceSettings
from .planning import PlanningOrchestrator

MAX_REQUEST_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _V2ExecutionServices:
    project: str
    location: str
    gateway: Any
    executor: Any
    database: Any
    work: Any
    digestion_artifacts: Any
    digestion_runs: Any
    storage_client: Any
    bucket: str
    worker_id: str


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


def _editorial_project_runtime():
    from .runtime import VertexRuntimeSettings, build_editorial_project_runtime

    project = _required("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return build_editorial_project_runtime(VertexRuntimeSettings(project=project, location=location))


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


def run_review_axis_feedback_command(planning_run_id: UUID, feedback: str) -> AxisFeedbackReviewResult:
    from .axis_feedback import (
        AxisFeedbackOrchestrator,
        LlamaIndexAxisFeedbackDecider,
        LlamaIndexAxisFeedbackReviser,
        LlamaIndexBoundedRecalibrator,
    )
    from .axis_proposal import LlamaIndexAxisEvidenceReviewer

    persistence = _planning_persistence()
    runtime = _axis_proposal_runtime()
    return AxisFeedbackOrchestrator(
        persistence,
        runtime,
        LlamaIndexAxisFeedbackDecider(runtime),
        LlamaIndexAxisFeedbackReviser(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
        LlamaIndexBoundedRecalibrator(runtime),
    ).review(planning_run_id, feedback)


def run_resolve_axis_feedback_command(planning_run_id: UUID, choice: EnrichmentResolutionChoice) -> AxisFeedbackReviewResult:
    from .axis_feedback import (
        AxisFeedbackOrchestrator,
        LlamaIndexAxisFeedbackDecider,
        LlamaIndexAxisFeedbackReviser,
        LlamaIndexBoundedRecalibrator,
    )
    from .axis_proposal import LlamaIndexAxisEvidenceReviewer

    persistence = _planning_persistence()
    runtime = _axis_proposal_runtime()
    return AxisFeedbackOrchestrator(
        persistence,
        runtime,
        LlamaIndexAxisFeedbackDecider(runtime),
        LlamaIndexAxisFeedbackReviser(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
        LlamaIndexBoundedRecalibrator(runtime),
    ).resolve(planning_run_id, choice)


def run_approve_axis_command(
    planning_run_id: UUID, narrative_axis_id: UUID | None = None
) -> EditorialProjectProposalResult:
    from .editorial_project import EditorialProjectOrchestrator
    from .hefesto_tips import HefestoTipsEditorialProjectBuilder

    persistence = _planning_persistence()
    runtime = _editorial_project_runtime()
    return EditorialProjectOrchestrator(
        persistence,
        runtime,
        HefestoTipsEditorialProjectBuilder(runtime),
    ).execute(planning_run_id, narrative_axis_id=narrative_axis_id)


def run_plan_command(location: str) -> PlanningRun:
    project = _required("GOOGLE_CLOUD_PROJECT")
    request = load_planning_request(location, project=project)
    return PlanningOrchestrator(_planning_persistence()).execute(request)


def _v2_execution_services(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
) -> _V2ExecutionServices:
    from .digestion_persistence import FirestoreExecutionRunRepository, GcsDigestionArtifactStore
    from .execution_persistence import FirestoreUsageLedger, FirestoreWorkRepository
    from .execution_scheduler import BoundedFairExecutor, SmoothedTokenBucket
    from .llm_gateway import LLMGateway, VertexGenAITransport

    project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    database = firestore.Client(project=project)
    storage_client = storage.Client(project=project)
    gateway = LLMGateway(
        VertexGenAITransport(project=project, location=location),
        ledger=FirestoreUsageLedger(database),
    )
    executor = BoundedFairExecutor(
        SmoothedTokenBucket(tokens_per_minute=tokens_per_minute, burst_window_seconds=15),
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    )
    execution_name = os.environ.get("CLOUD_RUN_EXECUTION", "manual")
    task_index = os.environ.get("CLOUD_RUN_TASK_INDEX", "0")
    return _V2ExecutionServices(
        project=project,
        location=location,
        gateway=gateway,
        executor=executor,
        database=database,
        work=FirestoreWorkRepository(database),
        digestion_artifacts=GcsDigestionArtifactStore(
            storage_client,
            bucket_name=bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        digestion_runs=FirestoreExecutionRunRepository(database),
        storage_client=storage_client,
        bucket=bucket,
        worker_id=f"{execution_name}:{task_index}",
    )


def _digestion_orchestrator(*, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int):
    from .digestion_orchestrator import DigestionOrchestrator
    from .digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
    from .source_digestion import SourceDigestionPlanner

    services = _v2_execution_services(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    )
    model = os.environ.get("HEFESTO_V2_DIGESTION_MODEL", "gemini-2.5-flash").strip()
    if not model:
        raise ValueError("HEFESTO_V2_DIGESTION_MODEL cannot be empty")
    config = DigestionRuntimeConfig(model=model, location=services.location)
    return DigestionOrchestrator(
        planner=SourceDigestionPlanner(),
        calls=DigestionCallFactory(config),
        gateway=services.gateway,
        executor=services.executor,
        artifacts=services.digestion_artifacts,
        runs=services.digestion_runs,
        work=services.work,
        worker_id=services.worker_id,
    )


def _editorial_v2_orchestrator(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
):
    from .editorial_v2_orchestrator import EditorialV2Orchestrator
    from .editorial_v2_persistence import (
        FirestoreEditorialV2RunRepository,
        GcsEditorialV2ArtifactStore,
    )
    from .editorial_v2_runtime import EditorialV2CallFactory, EditorialV2RuntimeConfig

    services = _v2_execution_services(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    )
    model = os.environ.get("HEFESTO_V2_EDITORIAL_MODEL", "gemini-2.5-flash").strip()
    if not model:
        raise ValueError("HEFESTO_V2_EDITORIAL_MODEL cannot be empty")
    return EditorialV2Orchestrator(
        calls=EditorialV2CallFactory(
            EditorialV2RuntimeConfig(model=model, location=services.location)
        ),
        gateway=services.gateway,
        digestion_artifacts=services.digestion_artifacts,
        digestion_runs=services.digestion_runs,
        editorial_artifacts=GcsEditorialV2ArtifactStore(
            services.storage_client,
            bucket_name=services.bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        editorial_runs=FirestoreEditorialV2RunRepository(services.database),
        work=services.work,
        executor=services.executor,
        worker_id=services.worker_id,
    )


def _production_v2_orchestrator(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
):
    from .editorial_v2_persistence import (
        FirestoreEditorialV2RunRepository,
        GcsEditorialV2ArtifactStore,
    )
    from .production_evidence import ProductionEvidenceBuilder
    from .production_v2_orchestrator import ProductionV2Orchestrator
    from .production_v2_persistence import (
        FirestoreProductionV2RunRepository,
        GcsProductionV2ArtifactStore,
    )
    from .production_v2_runtime import ProductionV2CallFactory, ProductionV2RuntimeConfig

    services = _v2_execution_services(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    )
    model = os.environ.get("HEFESTO_V2_PRODUCTION_MODEL", "gemini-2.5-flash").strip()
    if not model:
        raise ValueError("HEFESTO_V2_PRODUCTION_MODEL cannot be empty")
    return ProductionV2Orchestrator(
        evidence_builder=ProductionEvidenceBuilder(),
        calls=ProductionV2CallFactory(
            ProductionV2RuntimeConfig(model=model, location=services.location)
        ),
        gateway=services.gateway,
        executor=services.executor,
        digestion_artifacts=services.digestion_artifacts,
        digestion_runs=services.digestion_runs,
        editorial_artifacts=GcsEditorialV2ArtifactStore(
            services.storage_client,
            bucket_name=services.bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        editorial_runs=FirestoreEditorialV2RunRepository(services.database),
        production_artifacts=GcsProductionV2ArtifactStore(
            services.storage_client,
            bucket_name=services.bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        production_runs=FirestoreProductionV2RunRepository(services.database),
        work=services.work,
        worker_id=services.worker_id,
    )


async def run_digest_v2_command(
    location: str,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    project = _required("GOOGLE_CLOUD_PROJECT")
    request = load_planning_request(location, project=project)
    return await _digestion_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).start(request)


async def run_resume_digest_v2_command(
    execution_run_id: UUID,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    return await _digestion_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).resume(execution_run_id)


async def run_propose_axis_v2_command(
    execution_run_id: UUID,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    return await _editorial_v2_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).propose_axis(execution_run_id)


async def run_approve_axis_v2_command(
    execution_run_id: UUID,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    return await _editorial_v2_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).approve_axis_and_propose_project(execution_run_id)


async def run_approve_project_v2_without_tips_command(
    execution_run_id: UUID,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    return await _editorial_v2_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).approve_project_without_tips(execution_run_id)


async def run_produce_v2_command(
    execution_run_id: UUID,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
):
    return await _production_v2_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).run(execution_run_id)


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
    if args.command == "digest-v2":
        result = await run_digest_v2_command(
            args.request,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "resume-digest-v2":
        result = await run_resume_digest_v2_command(
            args.execution_run_id,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "propose-axis-v2":
        result = await run_propose_axis_v2_command(
            args.execution_run_id,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "approve-axis-v2":
        result = await run_approve_axis_v2_command(
            args.execution_run_id,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "approve-project-v2":
        result = await run_approve_project_v2_without_tips_command(
            args.execution_run_id,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "produce-v2":
        result = await run_produce_v2_command(
            args.execution_run_id,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(result.model_dump_json())
        return
    if args.command == "extract-graph":
        result = await asyncio.to_thread(run_extract_graph_command, args.source_lineage_id)
        print(result.model_dump_json())
        return
    if args.command == "index-graph":
        result = await asyncio.to_thread(run_index_graph_command, args.source_lineage_id)
        print(result.model_dump_json())
        return
    if args.command == "propose-axis":
        result = await asyncio.to_thread(run_propose_axis_command, args.planning_run_id)
        print(result.model_dump_json())
        return
    if args.command == "review-axis-feedback":
        result = await asyncio.to_thread(
            run_review_axis_feedback_command, args.planning_run_id, _feedback(args.feedback_b64)
        )
        print(result.model_dump_json())
        return
    if args.command == "resolve-axis-feedback":
        result = await asyncio.to_thread(
            run_resolve_axis_feedback_command, args.planning_run_id, EnrichmentResolutionChoice(args.choice)
        )
        print(result.model_dump_json())
        return
    if args.command == "approve-axis":
        result = await asyncio.to_thread(
            run_approve_axis_command, args.planning_run_id, args.narrative_axis_id
        )
        print(result.model_dump_json())
        return
    app = _application()
    if args.command == "revise-project":
        result = await app.revise_project(args.editorial_project_id, _feedback(args.feedback_b64))
    elif args.command == "incorporate-hefesto-tips":
        selected_tips = None if args.all else args.tip
        result = await app.incorporate_hefesto_tips(args.editorial_project_id, selected_tips)
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
    digest_v2 = commands.add_parser(
        "digest-v2",
        help="run the opt-in graph-free V2 digestion path and persist its editorial understanding",
    )
    digest_v2.add_argument("--request", required=True)
    _add_v2_capacity_arguments(digest_v2)
    resume_digest_v2 = commands.add_parser(
        "resume-digest-v2",
        help="resume a persisted V2 digestion run without repeating completed work units",
    )
    resume_digest_v2.add_argument("--execution-run-id", type=UUID, required=True)
    _add_v2_capacity_arguments(resume_digest_v2)
    propose_axis_v2 = commands.add_parser(
        "propose-axis-v2",
        help="propose and evidence-review a graph-free V2 Narrative Axis; STOP for approval",
    )
    propose_axis_v2.add_argument("--execution-run-id", type=UUID, required=True)
    _add_v2_capacity_arguments(propose_axis_v2)
    approve_axis_v2 = commands.add_parser(
        "approve-axis-v2",
        help="approve the V2 Axis and propose an Editorial Project with preserved Dicas; STOP",
    )
    approve_axis_v2.add_argument("--execution-run-id", type=UUID, required=True)
    _add_v2_capacity_arguments(approve_axis_v2)
    approve_project_v2 = commands.add_parser(
        "approve-project-v2",
        help="approve the V2 Editorial Project while accepting zero Dicas do Hefesto",
    )
    approve_project_v2.add_argument("--execution-run-id", type=UUID, required=True)
    approve_project_v2.add_argument(
        "--without-tips",
        action="store_true",
        required=True,
        help="explicitly preserve generated Dicas while accepting none",
    )
    _add_v2_capacity_arguments(approve_project_v2)
    produce_v2 = commands.add_parser(
        "produce-v2",
        help="produce, critique, revise, and persist the complete article from an approved V2 Project",
    )
    produce_v2.add_argument("--execution-run-id", type=UUID, required=True)
    _add_v2_capacity_arguments(produce_v2)
    extract_graph = commands.add_parser("extract-graph", help="extract one persisted chunk at a time until graph_extractions_ready")
    extract_graph.add_argument("--source-lineage-id", type=UUID, required=True)
    index_graph = commands.add_parser("index-graph", help="assemble persisted graph extractions and index until graph_ready")
    index_graph.add_argument("--source-lineage-id", type=UUID, required=True)
    propose_axis = commands.add_parser(
        "propose-axis",
        help="persist editorial understanding and propose a Narrative Axis until axis_proposed",
    )
    propose_axis.add_argument("--planning-run-id", type=UUID, required=True)
    review_feedback = commands.add_parser(
        "review-axis-feedback",
        help="assess human feedback against the proposed Axis without search or Editorial Project",
    )
    review_feedback.add_argument("--planning-run-id", type=UUID, required=True)
    review_feedback.add_argument("--feedback-b64", required=True)
    resolve_feedback = commands.add_parser(
        "resolve-axis-feedback",
        help="accept or decline an enrichment_required axis feedback decision",
    )
    resolve_feedback.add_argument("--planning-run-id", type=UUID, required=True)
    resolve_feedback.add_argument("--choice", required=True, choices=["accept", "decline"])
    # The old Axis rewrite CLI is not registered; proposed Axis feedback must go through review-axis-feedback.
    approve_axis = commands.add_parser(
        "approve-axis",
        help="approve the active Narrative Axis and propose one Editorial Project; STOP for human approval",
    )
    approve_axis.add_argument("--planning-run-id", type=UUID, required=True)
    approve_axis.add_argument("--narrative-axis-id", type=UUID, required=False)
    revise_project = commands.add_parser("revise-project")
    revise_project.add_argument("--editorial-project-id", type=UUID, required=True)
    revise_project.add_argument("--feedback-b64", required=True)
    incorporate_tips = commands.add_parser(
        "incorporate-hefesto-tips",
        help="incorporate selected Dicas do Hefesto and increase duration without shrinking approved scope",
    )
    incorporate_tips.add_argument("--editorial-project-id", type=UUID, required=True)
    tip_selection = incorporate_tips.add_mutually_exclusive_group(required=True)
    tip_selection.add_argument("--all", action="store_true", help="incorporate every Dica do Hefesto")
    tip_selection.add_argument("--tip", action="append", help="exact Dica do Hefesto text; repeat to select more than one")
    approve_project = commands.add_parser("approve-project")
    approve_project.add_argument("--editorial-project-id", type=UUID, required=True)
    produce = commands.add_parser("produce")
    produce.add_argument("--editorial-project-id", type=UUID, required=True)
    return parser


def _add_v2_capacity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tokens-per-minute",
        type=int,
        required=True,
        help="verified Vertex model TPM available to this worker; no guessed default",
    )
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--per-project-concurrency", type=int, default=4)


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
