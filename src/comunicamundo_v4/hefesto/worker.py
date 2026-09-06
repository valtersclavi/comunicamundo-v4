"""Cloud Run entrypoint for the clean Hefesto rebuild."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from uuid import UUID, uuid4

from google.cloud import storage

from .contracts import PlanningRequest

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


def _v2_location() -> str:
    # Standard PayGo exposes its organization-level baseline on the global
    # endpoint and Google recommends global routing to reduce regional 429s.
    # Keep this separate from the frozen V1 GOOGLE_CLOUD_LOCATION setting.
    location = os.environ.get("HEFESTO_V2_LOCATION", "global").strip()
    if not location:
        raise ValueError("HEFESTO_V2_LOCATION cannot be empty")
    return location


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


def _v2_execution_services(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
) -> _V2ExecutionServices:
    from .digestion_persistence import GcsDigestionArtifactStore
    from .execution_scheduler import BoundedFairExecutor, SmoothedTokenBucket
    from .llm_gateway import LLMGateway, VertexGenAITransport
    from .postgres import (
        PostgresDatabase,
        PostgresExecutionRunRepository,
        PostgresKnowledgeRepository,
        PostgresUsageLedger,
        PostgresWorkRepository,
    )
    from .pricing import price_resolver_from_environment

    project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    location = _v2_location()
    database = PostgresDatabase.from_runtime_environment(max_size=max_concurrency + 4)
    storage_client = storage.Client(project=project)
    gateway = LLMGateway(
        VertexGenAITransport(project=project, location=location),
        ledger=PostgresUsageLedger(database),
        price_resolver=price_resolver_from_environment(),
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
        work=PostgresWorkRepository(database),
        digestion_artifacts=GcsDigestionArtifactStore(
            storage_client,
            bucket_name=bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        digestion_runs=PostgresExecutionRunRepository(database),
        storage_client=storage_client,
        bucket=bucket,
        worker_id=f"{execution_name}:{task_index}",
    )


def _digestion_orchestrator(*, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int):
    from .digestion_orchestrator import DigestionOrchestrator
    from .digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
    from .postgres import PostgresKnowledgeRepository
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
        knowledge=PostgresKnowledgeRepository(services.database),
        worker_id=services.worker_id,
    )


def _editorial_v2_orchestrator(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
):
    from .editorial_v2_orchestrator import EditorialV2Orchestrator
    from .editorial_v2_persistence import GcsEditorialV2ArtifactStore
    from .editorial_v2_runtime import EditorialV2CallFactory, EditorialV2RuntimeConfig
    from .postgres import PostgresEditorialV2RunRepository

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
        editorial_runs=PostgresEditorialV2RunRepository(services.database),
        work=services.work,
        executor=services.executor,
        worker_id=services.worker_id,
    )


def _production_v2_orchestrator(
    *, tokens_per_minute: int, max_concurrency: int, per_project_concurrency: int
):
    from .editorial_v2_persistence import GcsEditorialV2ArtifactStore
    from .production_evidence import ProductionEvidenceBuilder
    from .production_v2_orchestrator import ProductionV2Orchestrator
    from .production_v2_persistence import GcsProductionV2ArtifactStore
    from .production_v2_runtime import ProductionV2CallFactory, ProductionV2RuntimeConfig
    from .postgres import PostgresEditorialV2RunRepository, PostgresProductionV2RunRepository

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
        editorial_runs=PostgresEditorialV2RunRepository(services.database),
        production_artifacts=GcsProductionV2ArtifactStore(
            services.storage_client,
            bucket_name=services.bucket,
            root_prefix="comunicamundo-v4-rebuild",
        ),
        production_runs=PostgresProductionV2RunRepository(services.database),
        work=services.work,
        worker_id=services.worker_id,
    )


async def run_digest_v2_command(
    location: str,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
    execution_run_id: UUID | None = None,
):
    project = _required("GOOGLE_CLOUD_PROJECT")
    request = load_planning_request(location, project=project)
    return await _digestion_orchestrator(
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    ).start(request, execution_run_id=execution_run_id)


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


async def run_report_v2_command(project_id: UUID):
    from .usage_reporting import (
        GcsUsageReportStore,
        build_and_persist_usage_report,
    )
    from .postgres import PostgresDatabase, PostgresUsageReader

    google_project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    database = PostgresDatabase.from_runtime_environment(max_size=2)
    storage_client = storage.Client(project=google_project)
    try:
        return await build_and_persist_usage_report(
            project_id,
            reader=PostgresUsageReader(database),
            store=GcsUsageReportStore(
                storage_client,
                bucket_name=bucket,
                root_prefix="comunicamundo-v4-rebuild",
            ),
        )
    finally:
        await database.close()


async def run_migrate_postgres_command() -> dict[str, Any]:
    from .postgres import apply_migrations, check_direct_connection

    direct_url = _required("HEFESTO_POSTGRES_DIRECT_URL")
    applied = await apply_migrations(direct_url)
    check = await check_direct_connection(direct_url)
    return {"status": "ready", "applied_migrations": applied, **check}


async def run_check_postgres_command() -> dict[str, Any]:
    from .postgres import PostgresDatabase

    database = PostgresDatabase.from_runtime_environment(max_size=1)
    try:
        return {"status": "ready", **(await database.check())}
    finally:
        await database.close()


async def run_complete_v2_command(
    location: str,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
) -> dict[str, Any]:
    """Run the approved A/B path in one Cloud Run execution and render the article."""

    from .postgres import PostgresDatabase, PostgresStageRecorder
    from .renderer import persist_complete_run_receipt, render_and_persist_article

    google_project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    request = load_planning_request(location, project=google_project)
    metrics_database = PostgresDatabase.from_runtime_environment(max_size=2)
    recorder = PostgresStageRecorder(metrics_database)
    stages: list[dict[str, Any]] = []
    execution_run_id = uuid4()

    async def measured(stage: str, operation):
        nonlocal execution_run_id
        started = datetime.now(UTC)
        try:
            value = await operation()
        except Exception as error:
            finished = datetime.now(UTC)
            await recorder.record(
                execution_run_id=execution_run_id,
                project_id=request.project_id,
                stage=stage,
                status="failed",
                started_at=started,
                finished_at=finished,
                details={"error_class": type(error).__name__, "error_message": str(error)[:4000]},
            )
            raise
        finished = datetime.now(UTC)
        duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        stages.append({"stage": stage, "duration_ms": duration_ms})
        await recorder.record(
            execution_run_id=execution_run_id,
            project_id=request.project_id,
            stage=stage,
            status="succeeded",
            started_at=started,
            finished_at=finished,
            details={},
        )
        return value

    try:
        digestion_started = datetime.now(UTC)
        try:
            digestion = await run_digest_v2_command(
                location,
                tokens_per_minute=tokens_per_minute,
                max_concurrency=max_concurrency,
                per_project_concurrency=per_project_concurrency,
                execution_run_id=execution_run_id,
            )
        except Exception as error:
            await recorder.record(
                execution_run_id=execution_run_id,
                project_id=request.project_id,
                stage="digestion",
                status="failed",
                started_at=digestion_started,
                finished_at=datetime.now(UTC),
                details={"error_class": type(error).__name__, "error_message": str(error)[:4000]},
            )
            raise
        digestion_finished = datetime.now(UTC)
        if digestion.execution_run.execution_run_id != execution_run_id:
            raise ValueError("digestion returned a different execution identity")
        digestion_ms = max(0, int((digestion_finished - digestion_started).total_seconds() * 1000))
        stages.append({"stage": "digestion", "duration_ms": digestion_ms})
        await recorder.record(
            execution_run_id=execution_run_id,
            project_id=request.project_id,
            stage="digestion",
            status="succeeded",
            started_at=digestion_started,
            finished_at=digestion_finished,
            details={
                "source_count": len(digestion.digestion_plan.sources),
                "model_calls": digestion.execution_run.completed_model_calls,
            },
        )
        await measured(
            "narrative_axis",
            lambda: run_propose_axis_v2_command(
                execution_run_id,
                tokens_per_minute=tokens_per_minute,
                max_concurrency=max_concurrency,
                per_project_concurrency=per_project_concurrency,
            ),
        )
        await measured(
            "editorial_project",
            lambda: run_approve_axis_v2_command(
                execution_run_id,
                tokens_per_minute=tokens_per_minute,
                max_concurrency=max_concurrency,
                per_project_concurrency=per_project_concurrency,
            ),
        )
        await measured(
            "project_approval",
            lambda: run_approve_project_v2_without_tips_command(
                execution_run_id,
                tokens_per_minute=tokens_per_minute,
                max_concurrency=max_concurrency,
                per_project_concurrency=per_project_concurrency,
            ),
        )
        production = await measured(
            "production",
            lambda: run_produce_v2_command(
                execution_run_id,
                tokens_per_minute=tokens_per_minute,
                max_concurrency=max_concurrency,
                per_project_concurrency=per_project_concurrency,
            ),
        )
        storage_client = storage.Client(project=google_project)
        rendered, manifest = await measured(
            "renderer",
            lambda: render_and_persist_article(
                database=metrics_database,
                storage_client=storage_client,
                bucket_name=bucket,
                root_prefix="comunicamundo-v4-rebuild",
                execution_run_id=execution_run_id,
                article=production.production_record.final_article,
                language=request.brief.output_language,
            ),
        )
        usage = await measured("usage_report", lambda: run_report_v2_command(request.project_id))
        dossier_findings = sum(len(item.findings) for item in digestion.source_dossiers)
        evidence_anchors = sum(
            len(finding.anchors)
            for dossier in digestion.source_dossiers
            for finding in dossier.findings
        )
        result = {
            "status": "complete",
            "project_id": str(request.project_id),
            "execution_run_id": str(execution_run_id),
            "digestion": {
                "source_count": len(digestion.digestion_plan.sources),
                "section_count": sum(len(item.sections) for item in digestion.digestion_plan.sources),
                "sentence_count": sum(
                    len(paragraph.sentences)
                    for source in digestion.digestion_plan.sources
                    for section in source.sections
                    for paragraph in section.paragraphs
                ),
                "planned_model_calls": sum(
                    item.planned_model_calls for item in digestion.digestion_plan.sources
                ),
                "dossier_findings": dossier_findings,
                "evidence_anchors": evidence_anchors,
                "cross_source_agreements": len(digestion.evidence_matrix.agreements),
                "cross_source_divergences": len(digestion.evidence_matrix.divergences),
                "coverage_gaps": sum(
                    len(item.coverage_gaps) for item in digestion.source_dossiers
                ),
            },
            "article": production.production_record.final_article.model_dump(mode="json"),
            "production": {
                "research_subjects": len(production.production_record.research_bundle.subjects),
                "revision_rounds": production.production_record.final_article.revision_rounds,
            },
            "rendered": rendered.model_dump(mode="json"),
            "renderer_manifest": manifest.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "stages": stages,
        }
        receipt = persist_complete_run_receipt(
            storage_client,
            bucket_name=bucket,
            root_prefix="comunicamundo-v4-rebuild",
            project_id=request.project_id,
            execution_run_id=execution_run_id,
            payload=result,
        )
        return {**result, "receipt": receipt.model_dump(mode="json")}
    except Exception as error:
        usage = await run_report_v2_command(request.project_id)
        print(json.dumps({
            "status": "failed",
            "project_id": str(request.project_id),
            "execution_run_id": str(execution_run_id),
            "failed_stage": "digestion" if not stages else "post_digestion",
            "error_class": type(error).__name__,
            "error_message": str(error)[:4000],
            "usage": usage.model_dump(mode="json"),
        }, ensure_ascii=False, sort_keys=True), flush=True)
        raise
    finally:
        await metrics_database.close()


async def run_proof_v2_command(
    variant: str,
    *,
    tokens_per_minute: int,
    max_concurrency: int,
    per_project_concurrency: int,
) -> dict[str, Any]:
    """Stage fresh proof inputs as the runtime identity, then execute the V2 path."""

    from .proof_requests import stage_proof_request

    google_project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    staged = await asyncio.to_thread(
        stage_proof_request,
        variant,
        bucket_name=bucket,
        storage_client=storage.Client(project=google_project),
    )
    result = await run_complete_v2_command(
        staged.request_uri,
        tokens_per_minute=tokens_per_minute,
        max_concurrency=max_concurrency,
        per_project_concurrency=per_project_concurrency,
    )
    return {**result, "proof": staged.model_dump(mode="json")}


async def _run(args: argparse.Namespace) -> None:
    if args.command == "migrate-postgres":
        print(json.dumps(await run_migrate_postgres_command(), sort_keys=True))
        return
    if args.command == "check-postgres":
        print(json.dumps(await run_check_postgres_command(), sort_keys=True))
        return
    if args.command == "run-v2":
        result = await run_complete_v2_command(
            args.request,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if args.command == "run-proof-v2":
        result = await run_proof_v2_command(
            args.variant,
            tokens_per_minute=args.tokens_per_minute,
            max_concurrency=args.max_concurrency,
            per_project_concurrency=args.per_project_concurrency,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
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
    if args.command == "report-v2":
        result = await run_report_v2_command(args.project_id)
        print(result.model_dump_json())
        return
    raise ValueError(f"unsupported command: {args.command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ComunicAmundo V4 Hefesto clean rebuild")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "migrate-postgres",
        help="apply versioned migrations using the direct PostgreSQL connection",
    )
    run_v2 = commands.add_parser(
        "run-v2",
        help="digest, approve test gates, produce, render, and report in one execution",
    )
    run_v2.add_argument("--request", required=True)
    run_v2.add_argument(
        "--auto-approve-without-tips",
        action="store_true",
        required=True,
        help="explicitly approve the Axis and Project while accepting zero Dicas",
    )
    _add_v2_capacity_arguments(run_v2)
    run_proof_v2 = commands.add_parser(
        "run-proof-v2",
        help="stage fresh canonical canary/A/B sources and run the complete V2 path",
    )
    run_proof_v2.add_argument("--variant", choices=("canary", "A", "B"), required=True)
    _add_v2_capacity_arguments(run_proof_v2)
    commands.add_parser(
        "check-postgres",
        help="verify the pooled runtime connection without exposing credentials",
    )
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
    report_v2 = commands.add_parser(
        "report-v2",
        help="aggregate and persist provider usage, latency, and rated cost for one V2 project",
    )
    report_v2.add_argument("--project-id", type=UUID, required=True)
    return parser


def _add_v2_capacity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tokens-per-minute",
        type=int,
        required=True,
        help="verified Standard PayGo baseline TPM for this model; no guessed default",
    )
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--per-project-concurrency", type=int, default=6)


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
