from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from time import perf_counter
from uuid import UUID, uuid4, uuid5

from google.cloud import storage
from google.api_core.exceptions import NotFound

from .gateway import VertexGateway
from .contracts import CallUsage, NormalizedSource, PlanningRequest, ProductRun, ProvisionalEditorialProject, ProvisionalEditorialSubject, EditorialInputRequest, RunState, SourceMode
from .ingestion import IngestionResult, ingest, ingest_acquired
from .maintenance import clean_operational_history
from .postgres import ProductRepository, apply_migrations
from .service import Hefesto
from .segmentation import segment_sources
from .source_editor import EditorialSourceEditor
from .source_pipeline import OpenAlexSearchProvider, SourceAcquirer, SourceDiscovery, YouSearchProvider
from .source_research import SourceResearch
from .storage import load_input_request, persist_receipt


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing environment variable {name}")
    return value


def print_receipt(receipt: dict[str, object], uri: str) -> None:
    compact = {**receipt, "receipt_uri": uri}
    usage = compact.get("usage")
    if isinstance(usage, dict):
        compact["usage"] = {key: value for key, value in usage.items() if key != "calls"}
    print(json.dumps(compact, ensure_ascii=False), flush=True)


def storage_client() -> storage.Client:
    return storage.Client(project=required("GOOGLE_CLOUD_PROJECT"))


async def services() -> tuple[ProductRepository, Hefesto]:
    project = required("GOOGLE_CLOUD_PROJECT")
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    gateway = VertexGateway(
        project=project,
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    return repository, Hefesto(repository, gateway)


async def plan(args: argparse.Namespace) -> None:
    client = storage_client()
    input_request = load_input_request(
        client,
        uri=args.request,
    )
    execution_id = args.execution_run_id or uuid4()
    bucket_name = required("HEFESTO_GCS_BUCKET")
    if input_request.source_mode is SourceMode.FIND_FOR_ME:
        try:
            await _propose_provisional_project(
                input_request, execution_id, client, bucket_name, getattr(args, "engine", "legacy"),
            )
        except Exception as error:
            await _record_pre_run_failure(
                execution_id, error, "provisional_editorial_project"
            )
            raise
        return
    stages: list[dict[str, object]] = []
    started = perf_counter()
    progress_repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await progress_repository.open()
    submitted = [{"input_uri": item.uri, "title": item.title}
                 for item in input_request.sources]
    progress = [{**item, "ordinal": index, "status": "queued"}
                for index, item in enumerate(submitted, start=1)]
    loop = asyncio.get_running_loop()
    from threading import Lock
    progress_lock = Lock()
    stored_sources: list[NormalizedSource] = []

    async def store_normalized_source(
        ordinal: int, source: NormalizedSource, source_receipt: object,
    ) -> None:
        """Commit each completed source before the remaining conversions finish."""
        rank = len(stored_sources) + 1
        paragraphs = [p.model_copy(update={"source_ordinal": rank})
                      for p in segment_sources([source])]
        assets = [a.model_copy(update={"source_ordinal": rank})
                  for a in source.assets]
        stored_sources.append(source)
        partial_request = PlanningRequest(
            project_id=input_request.project_id, brief=input_request.brief,
            sources=[s.model_copy(update={
                "paragraphs": [], "assets": [], "asset_links": [],
            }) for s in stored_sources],
        )
        run = await progress_repository.get_run(execution_id)
        if run is None:
            run = ProductRun(execution_run_id=execution_id,
                             project_id=input_request.project_id,
                             request=partial_request)
        else:
            run = run.model_copy(update={"request": partial_request})
        await progress_repository.save_run(run)
        await progress_repository.save_structure(
            execution_id, paragraphs, assets, source.asset_links,
        )
        counts = await progress_repository.structure_counts(execution_id)
        expected = (source_receipt.paragraphs, source_receipt.sentences)
        if counts.get(source.source_id) != expected:
            raise RuntimeError(
                f"Postgres did not confirm source {ordinal}: "
                f"expected {expected}, got {counts.get(source.source_id)}"
            )

    def persist_source(ordinal: int, source: NormalizedSource, receipt: object) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                store_normalized_source(ordinal, source, receipt), loop,
            ).result()
        except Exception as error:
            publish_progress({
                "ordinal": ordinal, "status": "failed",
                "failed_step": "gravação no Postgres",
                "error_class": type(error).__name__,
                "error_message": str(error)[:2000],
            })
            raise
        publish_progress({
            "ordinal": ordinal, "status": "persisted",
            "source_id": str(source.source_id),
            "paragraphs": receipt.paragraphs, "sentences": receipt.sentences,
        })

    def publish_progress(update: dict[str, object]) -> None:
        with progress_lock:
            index = int(update["ordinal"]) - 1
            progress[index] = {**progress[index], **update}
            receipt = {
                "status": "ingesting", "project_id": str(input_request.project_id),
                "execution_run_id": str(execution_id),
                "input": {"submitted_sources": submitted,
                          "source_progress": [item.copy() for item in progress]},
            }
            asyncio.run_coroutine_threadsafe(
                progress_repository.record_product_receipt(execution_id, receipt), loop
            ).result()

    try:
        await progress_repository.record_product_receipt(execution_id, {
            "status": "ingesting", "project_id": str(input_request.project_id),
            "execution_run_id": str(execution_id),
            "input": {"submitted_sources": submitted, "source_progress": progress},
        })
        acquirer = None
        if any(item.uri.startswith("https://") for item in input_request.sources):
            acquirer = SourceAcquirer(
                you_api_key=required("YOU_API_KEY"),
                jina_api_key=required("JINA_API_KEY"),
                browser_use_api_key=required("BROWSER_USE_API_KEY"),
            )
        ingestion = await asyncio.to_thread(
            ingest,
            input_request,
            client=client,
            bucket_name=bucket_name,
            acquirer=acquirer,
            on_progress=publish_progress,
            on_normalized=persist_source,
        )
    except Exception as error:
        previous = await progress_repository.product_receipt(execution_id) or {}
        receipt = {
            **previous,
            "status": "input_failed",
            "project_id": str(input_request.project_id),
            "execution_run_id": str(execution_id),
            "error_class": type(error).__name__,
            "error_message": str(error)[:4000],
            "usage": _empty_usage(),
            "stages": [{
                "stage": "ingestion", "status": "failed",
                "duration_ms": int((perf_counter() - started) * 1000),
                "error_class": type(error).__name__,
                "error_message": str(error)[:2000],
            }],
            "generated_at": datetime.now(UTC).isoformat(),
        }
        uri = persist_receipt(
            client, bucket_name=bucket_name,
            execution_run_id=execution_id, payload=receipt,
        )
        repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
        await repository.open()
        try:
            await repository.record_product_receipt(execution_id, receipt)
        finally:
            await repository.close()
        print_receipt(receipt, uri)
        raise
    finally:
        await progress_repository.close()
    stages.append({
        "stage": "ingestion",
        "duration_ms": int((perf_counter() - started) * 1000),
        "receipt_uri": ingestion.receipt_uri,
        "model_invocations": 0,
        "source_count": ingestion.receipt.source_count,
        "table_count": sum(item.tables for item in ingestion.receipt.sources),
        "figure_count": sum(item.figures for item in ingestion.receipt.sources),
        "failed_normalization_count": len(ingestion.receipt.failed_normalizations),
    })
    repository, hefesto = await services()
    try:
        started = perf_counter()
        prepared = await hefesto.prepare(
            ingestion.request, execution_run_id=execution_id
        )
        await repository.save_run(prepared.model_copy(
            update={"editorial_engine": getattr(args, "engine", "legacy")}
        ))
        stages.append({
            "stage": "relational_structure",
            "duration_ms": int((perf_counter() - started) * 1000),
            "model_invocations": 0,
        })
        await hefesto.record_stages(execution_id, stages)
        # Publish the per-file outcome as soon as the relational structure is stored.
        # Editorial work can take minutes and must not hide ingestion failures.
        await _persist_product_receipt(
            hefesto, client, execution_id, stages,
            ingestion=ingestion, submitted_sources=input_request.sources,
        )
        await _continue_to_project(
            hefesto, client, execution_id, stages,
            ingestion=ingestion,
        )
    finally:
        await repository.close()


async def _propose_provisional_project(
    input_request: object,
    execution_id: UUID,
    client: storage.Client,
    bucket_name: str,
    engine: str = "legacy",
) -> None:
    from .contracts import EditorialInputRequest

    request = EditorialInputRequest.model_validate(input_request)
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    gateway = VertexGateway(
        project=required("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    started = perf_counter()
    try:
        result = await EditorialSourceEditor(gateway).provisional_project(request.brief)
        stage = {
            "stage": "provisional_editorial_project",
            "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            "model_invocations": 1,
        }
        receipt = {
            "status": "provisional_project_proposed",
            "editorial_engine": engine,
            "project_id": str(request.project_id),
            "execution_run_id": str(execution_id),
            "provisional_editorial_project": result.value.model_dump(mode="json"),
            "stages": [stage],
            "usage": _usage_from_calls([result.usage]),
            "provisional_usage": result.usage.model_dump(mode="json"),
            "generated_at": datetime.now(UTC).isoformat(),
        }
        await repository.record_product_receipt(execution_id, receipt)
        uri = persist_receipt(
            client, bucket_name=bucket_name,
            execution_run_id=execution_id, payload=receipt,
        )
        print_receipt(receipt, uri)
    finally:
        await repository.close()


async def source_search(execution_id: UUID) -> None:
    client = storage_client()
    bucket_name = required("HEFESTO_GCS_BUCKET")
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    try:
        existing_run = await repository.get_run(execution_id)
        if existing_run is not None:
            hefesto = Hefesto(repository, VertexGateway(
                project=required("GOOGLE_CLOUD_PROJECT"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            ))
            await _continue_to_project(hefesto, client, execution_id, [])
            return
        request = await repository.submission_request(execution_id)
        receipt = await repository.product_receipt(execution_id)
        if request is None or receipt is None:
            raise KeyError("zero-source submission is missing")
        if request.source_mode is not SourceMode.FIND_FOR_ME:
            raise ValueError("source search is only valid for find-for-me submissions")
        if receipt.get("status") != "provisional_project_proposed":
            raise ValueError(
                "source search requires an approved provisional Editorial Project"
            )
        provisional = ProvisionalEditorialProject.model_validate(
            receipt.get("provisional_editorial_project")
        )
        gateway = VertexGateway(
            project=required("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        editor = EditorialSourceEditor(gateway)
        discovery = SourceDiscovery([
            YouSearchProvider(required("YOU_API_KEY")),
            OpenAlexSearchProvider(required("OPENALEX_API_KEY")),
        ], results_per_query=2)
        research = SourceResearch(
            editor,
            discovery,
            SourceAcquirer(
                you_api_key=required("YOU_API_KEY"),
                jina_api_key=required("JINA_API_KEY"),
                browser_use_api_key=required("BROWSER_USE_API_KEY"),
            ),
        )
        stages = list(receipt.get("stages") or [])
        started = perf_counter()
        await repository.record_product_receipt(execution_id, {
            **receipt, "status": "searching_sources", "stages": [
                *stages, {"stage": "source_research", "status": "running", "duration_ms": 0},
            ],
        })
        result = await research.run(request.brief, provisional)
        stages.append({
            "stage": "source_research", "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            "candidate_count": len(result.candidates),
            "selected_count": len(result.selection.selected_candidate_ids),
            "subject_coverage": result.selection.subject_coverage,
            "coverage_complete": result.selection.coverage_complete,
            "attempts_by_subject": result.attempts_by_subject,
            "unsupported_subject_indexes": sorted(
                set(range(len(provisional.subjects)))
                - {
                    index
                    for indexes in result.selection.subject_coverage.values()
                    for index in indexes
                }
            ),
        })
        stages.append({
            "stage": "source_acquisition", "status": "completed",
            "duration_ms": 0,
            "acquired_count": len(result.acquired),
            "failed_count": result.failed_acquisition_count,
            "sources": [item.record.model_dump(mode="json") for item in result.acquisitions],
        })
        started = perf_counter()
        ingestion = await asyncio.to_thread(
            ingest_acquired,
            request,
            list(result.acquisitions),
            client=client,
            bucket_name=bucket_name,
            provisional_project=provisional,
            supported_subject_indexes=sorted({
                index
                for indexes in result.selection.subject_coverage.values()
                for index in indexes
            }),
            subject_coverage=result.selection.subject_coverage,
        )
        stages.append({
            "stage": "redigestion", "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            "receipt_uri": ingestion.receipt_uri,
            "source_count": ingestion.receipt.source_count,
            "table_count": sum(item.tables for item in ingestion.receipt.sources),
            "figure_count": sum(item.figures for item in ingestion.receipt.sources),
            "failed_normalization_count": len(ingestion.receipt.failed_normalizations),
        })
        hefesto = Hefesto(repository, gateway)
        prepared = await hefesto.prepare(ingestion.request, execution_run_id=execution_id)
        await repository.save_run(prepared.model_copy(
            update={"editorial_engine": receipt.get("editorial_engine", "legacy")}
        ))
        provisional_calls = list((receipt.get("usage") or {}).get("calls") or [])
        for provisional_usage in provisional_calls:
            await repository.record_usage(
                execution_id, request.project_id, CallUsage.model_validate(provisional_usage)
            )
        for usage in result.usages:
            await repository.record_usage(execution_id, request.project_id, usage)
        await hefesto.record_stages(execution_id, stages)
        await _continue_to_project(
            hefesto, client, execution_id, stages, ingestion=ingestion
        )
    finally:
        await repository.close()


async def _record_pre_run_failure(
    execution_id: UUID, error: Exception, stage: str
) -> None:
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    try:
        if await repository.get_run(execution_id) is not None:
            return
        receipt = await repository.product_receipt(execution_id) or {}
        stages = list(receipt.get("stages") or [])
        stages = [item for item in stages if item.get("stage") != stage]
        stages.append({
            "stage": stage,
            "status": "failed",
            "duration_ms": 0,
            "error_class": type(error).__name__,
            "error_message": str(error)[:2000],
        })
        await repository.record_product_receipt(execution_id, {
            **receipt,
            "status": "failed",
            "error_class": type(error).__name__,
            "error_message": str(error)[:4000],
            "stages": stages,
            "generated_at": datetime.now(UTC).isoformat(),
        })
    finally:
        await repository.close()


async def revise_provisional(execution_id: UUID, instruction_uri: str) -> None:
    client = storage_client()
    bucket_name = required("HEFESTO_GCS_BUCKET")
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    try:
        request = await repository.submission_request(execution_id)
        receipt = await repository.product_receipt(execution_id)
        if request is None or receipt is None:
            raise KeyError("zero-source submission is missing")
        if receipt.get("status") != "provisional_project_proposed":
            raise ValueError(
                "provisional revision is only valid while the pre-project awaits approval"
            )
        bucket, name = instruction_uri.removeprefix("gs://").split("/", 1)
        instruction = json.loads(client.bucket(bucket).blob(name).download_as_bytes())
        message = str(instruction.get("message") or "").strip()
        if not message:
            raise ValueError("provisional revision instruction is empty")
        current = ProvisionalEditorialProject.model_validate(
            receipt.get("provisional_editorial_project")
        )
        gateway = VertexGateway(
            project=required("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        result = await EditorialSourceEditor(gateway).revise_provisional_project(
            request.brief, current, message
        )
        previous_calls = list((receipt.get("usage") or {}).get("calls") or [])
        calls = [CallUsage.model_validate(item) for item in previous_calls]
        calls.append(result.usage)
        history = list(receipt.get("provisional_revision_history") or [])
        history.append({
            "instruction": message,
            "previous_project": current.model_dump(mode="json"),
            "revised_at": datetime.now(UTC).isoformat(),
        })
        revised = {
            **receipt,
            "status": "provisional_project_proposed",
            "provisional_editorial_project": result.value.model_dump(mode="json"),
            "provisional_revision_history": history,
            "usage": _usage_from_calls(calls),
            "provisional_usage": result.usage.model_dump(mode="json"),
            "last_action_error": None,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        await repository.record_product_receipt(execution_id, revised)
        uri = persist_receipt(
            client, bucket_name=bucket_name,
            execution_run_id=execution_id, payload=revised,
        )
        print_receipt(revised, uri)
    finally:
        await repository.close()


async def revise_project(execution_id: UUID, instruction_uri: str) -> None:
    client = storage_client()
    bucket, name = instruction_uri.removeprefix("gs://").split("/", 1)
    instruction = json.loads(client.bucket(bucket).blob(name).download_as_bytes())
    message = str(instruction.get("message") or "").strip()
    if not message:
        raise ValueError("revision instruction is empty")
    repository, hefesto = await services()
    try:
        run = await repository.get_run(execution_id)
        if run is None or run.state is not RunState.PROJECT_PROPOSED:
            raise ValueError("Editorial Project is not awaiting revision")
        await hefesto.propose_matrix_project(
            execution_id, revision_instruction=message,
        )
        await _persist_product_receipt(hefesto, client, execution_id, [])
    finally:
        await repository.close()


async def accept_model_suggestion(execution_id: UUID, index: int) -> None:
    client = storage_client()
    repository, hefesto = await services()
    try:
        run = await repository.get_run(execution_id)
        if run is None or run.state is not RunState.PROJECT_PROPOSED:
            raise ValueError("Editorial Project is not awaiting suggestions")
        suggestions = run.editorial_project.proposal.model_knowledge_suggestions
        if index < 0 or index >= len(suggestions):
            raise ValueError("unknown model suggestion")
        statement = suggestions[index]
        await repository.save_model_contribution(
            execution_id, run.editorial_round + 1, index, statement,
        )
        await hefesto.propose_matrix_project(
            execution_id,
            revision_instruction=(
                "Integrate this small model-knowledge contribution into the "
                "relevant existing subject without presenting it as sourced "
                f"evidence: {statement}"
            ),
        )
        await _persist_product_receipt(hefesto, client, execution_id, [])
    finally:
        await repository.close()


async def enrich_project_source(execution_id: UUID, index: int) -> None:
    client = storage_client()
    repository, hefesto = await services()
    original = await repository.get_run(execution_id)
    if original is None or original.state is not RunState.PROJECT_PROPOSED:
        await repository.close()
        raise ValueError("Editorial Project is not awaiting source enrichment")
    proposal = original.editorial_project.proposal
    options = proposal.source_enrichment_opportunities
    if index < 0 or index >= len(options):
        await repository.close()
        raise ValueError("unknown source enrichment opportunity")
    opportunity = options[index]
    added_ids: list[UUID] = []
    try:
        gateway = VertexGateway(
            project=required("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        provisional = ProvisionalEditorialProject(
            working_title=proposal.working_title,
            editorial_promise=proposal.editorial_promise,
            narrative_axis=proposal.narrative_axis,
            duration_minutes=2,
            subjects=[ProvisionalEditorialSubject(
                title=opportunity, editorial_role=opportunity,
                desired_depth=opportunity, allocated_minutes=2,
            )],
            disclaimer="Esta oportunidade exige uma nova fonte.",
        )
        research = SourceResearch(
            EditorialSourceEditor(gateway),
            SourceDiscovery([
                YouSearchProvider(required("YOU_API_KEY")),
                OpenAlexSearchProvider(required("OPENALEX_API_KEY")),
            ], results_per_query=2),
            SourceAcquirer(
                you_api_key=required("YOU_API_KEY"),
                jina_api_key=required("JINA_API_KEY"),
                browser_use_api_key=required("BROWSER_USE_API_KEY"),
            ),
        )
        result = await research.run(original.request.brief, provisional)
        for usage in result.usages:
            await repository.record_usage(execution_id, original.project_id, usage)
        isolated_project_id = uuid5(
            original.project_id,
            f"matrix-source:{original.editorial_project.editorial_project_id}:{index}",
        )
        isolated_request = EditorialInputRequest(
            project_id=isolated_project_id, brief=original.request.brief,
            source_mode=SourceMode.FIND_FOR_ME,
        )
        ingested = await asyncio.to_thread(
            ingest_acquired, isolated_request, list(result.acquisitions),
            client=client, bucket_name=required("HEFESTO_GCS_BUCKET"),
            provisional_project=provisional,
            supported_subject_indexes=[0],
            subject_coverage=result.selection.subject_coverage,
        )
        offset = len(original.request.sources)
        additions = list(ingested.request.sources)
        added_ids = [source.source_id for source in additions]
        paragraphs = [
            item.model_copy(update={"source_ordinal": offset + rank})
            for rank, source in enumerate(additions, start=1)
            for item in segment_sources([source])
        ]
        assets = [
            item.model_copy(update={"source_ordinal": offset + rank})
            for rank, source in enumerate(additions, start=1)
            for item in source.assets
        ]
        links = [item for source in additions for item in source.asset_links]
        # Preserve old structured rows; append only the new source's rows.
        await repository.save_structure(execution_id, paragraphs, assets, links)
        clean_additions = [
            source.model_copy(update={
                "paragraphs": [], "assets": [], "asset_links": [],
            }) for source in additions
        ]
        request = original.request.model_copy(update={
            "sources": [*original.request.sources, *clean_additions],
            "existing_editorial_project": proposal,
        })
        active = original.model_copy(update={
            "request": request, "editorial_project": None,
            "editorial_round": original.editorial_round + 1,
            "editorial_engine": "matrix",
            "state": RunState.SOURCE_STRUCTURE_READY,
            "error_class": None, "error_message": None,
        })
        await repository.save_run(active)
        await hefesto.select(execution_id)
        await hefesto.extract_matrix(execution_id)
        claims = await repository.matrix_claims(execution_id)
        if not any(
            item["origin"] == "source"
            and any(e["source_ordinal"] > offset for e in item["evidence"])
            for item in claims
        ):
            raise ValueError("new source did not contribute to the proposed enrichment")
        await hefesto.propose_matrix_project(execution_id)
        await _persist_product_receipt(hefesto, client, execution_id, [])
    except Exception:
        if added_ids:
            await repository.rollback_enrichment_sources(
                execution_id, added_ids, original.editorial_round,
            )
        latest = await repository.get_run(execution_id)
        if latest is not None and latest.editorial_project is None:
            await repository.save_run(original)
        raise
    finally:
        await repository.close()


async def resume_plan(execution_id: UUID) -> None:
    client = storage_client()
    repository, hefesto = await services()
    try:
        await _continue_to_project(hefesto, client, execution_id, [])
    finally:
        await repository.close()


async def _continue_to_project(
    hefesto: Hefesto,
    client: storage.Client,
    execution_id: UUID,
    stages: list[dict[str, object]],
    *,
    ingestion: IngestionResult | None = None,
) -> None:
    active_stage = "selection"
    started = perf_counter()
    try:
        await hefesto.record_stages(execution_id, [{
            "stage": active_stage, "status": "running", "duration_ms": 0,
        }])
        await hefesto.select(execution_id)
        stages.append({
            "stage": active_stage,
            "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            **_task_metrics(
                await hefesto.usage_report(execution_id),
                {"source_selection"},
            ),
        })
        await hefesto.record_stages(execution_id, stages)
        active_stage, started = "editorial_comprehension", perf_counter()
        await hefesto.record_stages(execution_id, [{
            "stage": active_stage, "status": "running", "duration_ms": 0,
        }])
        active = await hefesto._required_run(execution_id)
        if active.editorial_engine == "matrix":
            await hefesto.propose_matrix_project(execution_id)
        else:
            await hefesto.propose_project(execution_id)
        stages.append({
            "stage": active_stage,
            "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            **_task_metrics(
                await hefesto.usage_report(execution_id),
                {
                    "editorial_comprehension_part",
                    "editorial_comprehension_merge",
                    "editorial_project_scaffold",
                    "editorial_subject_routing",
                    "editorial_subject_digest",
                    "editorial_subject_digest_merge",
                    "editorial_subject_core",
                    "editorial_subject_nuance",
                    "editorial_subject_evidence",
                },
            ),
        })
        await hefesto.record_stages(execution_id, stages)
    except Exception as error:
        stages.append({
            "stage": active_stage, "status": "failed",
            "duration_ms": int((perf_counter() - started) * 1000),
            "error_class": type(error).__name__,
            "error_message": str(error)[:2000],
        })
        await _persist_product_receipt(
            hefesto, client, execution_id, stages,
            ingestion=ingestion,
        )
        raise
    await _persist_product_receipt(
        hefesto, client, execution_id, stages,
        ingestion=ingestion,
    )


async def approve_project(execution_id: UUID) -> None:
    client = storage_client()
    repository, hefesto = await services()
    try:
        await hefesto.approve_project(execution_id)
        await _continue_to_text(hefesto, client, execution_id)
    finally:
        await repository.close()


async def produce_text(execution_id: UUID) -> None:
    client = storage_client()
    repository, hefesto = await services()
    try:
        await _continue_to_text(hefesto, client, execution_id)
    finally:
        await repository.close()


async def _continue_to_text(
    hefesto: Hefesto,
    client: storage.Client,
    execution_id: UUID,
) -> None:
    try:
        run = await repository.get_run(execution_id)
        if run is not None and run.editorial_engine == "matrix":
            await hefesto.produce_matrix_text(execution_id)
        else:
            await hefesto.produce_text(execution_id)
    except Exception:
        await _persist_product_receipt(hefesto, client, execution_id, [])
        raise
    await _persist_product_receipt(hefesto, client, execution_id, [])


def mark_action_dispatch(
    client: storage.Client,
    execution_id: UUID,
    action: str,
    status: str,
    error: Exception | None = None,
    dispatch_key: str | None = None,
) -> None:
    blob = client.bucket(required("HEFESTO_GCS_BUCKET")).blob(
        f"hefesto-product/executions/{execution_id}/{dispatch_key or action}-dispatch.json"
    )
    try:
        current = json.loads(blob.download_as_bytes())
    except Exception:
        current = {
            "execution_run_id": str(execution_id),
            "action": action,
            "created_at": datetime.now(UTC).isoformat(),
        }
    payload = {
        **current,
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if error is not None:
        payload.update({
            "error_class": type(error).__name__,
            "error_message": str(error)[:2000],
        })
    blob.upload_from_string(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
        content_type="application/json",
    )


async def record_action_failure(
    execution_id: UUID,
    action: str,
    error: Exception,
    *,
    stage: str | None = None,
) -> None:
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    try:
        receipt = await repository.product_receipt(execution_id)
        if receipt is None:
            return
        await repository.record_product_receipt(execution_id, {
            **receipt,
            "last_action_error": {
                "action": action,
                "stage": stage or action,
                "status": "failed",
                "error_class": type(error).__name__,
                "error_message": str(error)[:4000],
                "updated_at": datetime.now(UTC).isoformat(),
            },
            "generated_at": datetime.now(UTC).isoformat(),
        })
    finally:
        await repository.close()


async def _persist_product_receipt(
    hefesto: Hefesto,
    client: storage.Client,
    execution_id: UUID,
    stages: list[dict[str, object]],
    *,
    ingestion: IngestionResult | None = None,
    submitted_sources: list | None = None,
) -> None:
    await hefesto.record_stages(execution_id, stages)
    previous_receipt = await hefesto.stored_product_receipt(execution_id)
    previous_input = (
        previous_receipt.get("input")
        if isinstance(previous_receipt, dict) else None
    )
    stored_counts = await hefesto.structure_counts(execution_id) if ingestion is not None else {}
    receipt = {
        **(await hefesto.receipt(execution_id)),
        "input": (
            {
                "status": ingestion.receipt.status,
                "ingestion_receipt_uri": ingestion.receipt_uri,
                "normalized_request_uri": ingestion.receipt.normalized_request_uri,
                "source_count": ingestion.receipt.source_count,
                "source_progress": (previous_input or {}).get("source_progress", []),
                "attempted_count": (
                    len(submitted_sources) if submitted_sources is not None
                    else (previous_input or {}).get("attempted_count")
                ),
                "paragraph_count": ingestion.receipt.paragraph_count,
                "sentence_count": ingestion.receipt.sentence_count,
                "table_count": sum(item.tables for item in ingestion.receipt.sources),
                "figure_count": sum(item.figures for item in ingestion.receipt.sources),
                "sources": [
                    {
                        "source_id": str(item.source_id),
                        "input_uri": item.input_uri,
                        "title": next(
                            (source.title for source in (submitted_sources or []) if source.uri == item.input_uri),
                            next((source.get("title") for source in (previous_input or {}).get("sources", [])
                                  if source.get("input_uri") == item.input_uri), None),
                        ),
                        "status": item.normalization_status,
                        "original_stored": bool(item.original_uri),
                        "normalized": bool(item.normalization_receipt_uri or item.normalized_text_sha256),
                        "structured": item.paragraphs > 0 and item.sentences > 0,
                        "persisted": stored_counts.get(item.source_id) == (
                            item.paragraphs, item.sentences
                        ),
                        "stored_paragraphs": stored_counts.get(item.source_id, (0, 0))[0],
                        "stored_sentences": stored_counts.get(item.source_id, (0, 0))[1],
                        "bytes": item.bytes,
                        "pages": item.pages,
                        "paragraphs": item.paragraphs,
                        "sentences": item.sentences,
                        "tables": item.tables,
                        "figures": item.figures,
                        "normalization_receipt_uri": item.normalization_receipt_uri,
                    }
                    for item in ingestion.receipt.sources
                ],
                "failed_normalizations": [
                    item.model_dump(mode="json")
                    for item in ingestion.receipt.failed_normalizations
                ],
                "failed_acquisitions": [
                    {"input_uri": item.candidate.url, "title": item.candidate.title,
                     "error_message": item.failure_reason or "Aquisição falhou"}
                    for item in ingestion.receipt.failed_acquisitions
                ],
                "submitted_sources": (
                    [{"input_uri": source.uri, "title": source.title}
                     for source in submitted_sources]
                    if submitted_sources is not None
                    else (previous_input or {}).get("submitted_sources", [])
                ),
                "assets": [
                    {
                        "asset_id": asset.asset_id,
                        "source_id": str(asset.source_id),
                        "kind": asset.kind,
                        "role": asset.role,
                        "page_no": asset.page_no,
                        "caption": asset.caption,
                        "has_image": bool(asset.blob_uri),
                        "has_structure": bool(asset.structured_uri),
                        "linked_paragraphs": sum(
                            link.asset_id == asset.asset_id
                            for link in source.asset_links
                        ),
                    }
                    for source in ingestion.request.sources
                    for asset in source.assets
                ],
            }
            if ingestion is not None else previous_input
        ),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    await hefesto.record_product_receipt(execution_id, receipt)
    uri = persist_receipt(
        client,
        bucket_name=required("HEFESTO_GCS_BUCKET"),
        execution_run_id=execution_id,
        payload=receipt,
    )
    print_receipt(receipt, uri)


def _empty_usage() -> dict[str, object]:
    return {
        "call_count": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "provider_latency_ms": 0,
        "rated_cost_usd": "0.000000000",
        "calls": [],
    }


def _usage_from_calls(calls: list[CallUsage]) -> dict[str, object]:
    return {
        "call_count": len(calls),
        "prompt_tokens": sum(item.prompt_tokens for item in calls),
        "cached_tokens": sum(item.cached_tokens for item in calls),
        "output_tokens": sum(item.output_tokens for item in calls),
        "reasoning_tokens": sum(item.reasoning_tokens for item in calls),
        "total_tokens": sum(item.total_tokens for item in calls),
        "provider_latency_ms": sum(item.latency_ms for item in calls),
        "rated_cost_usd": str(sum(
            (Decimal(item.rated_cost_usd or "0") for item in calls),
            start=Decimal("0"),
        )),
        "calls": [item.model_dump(mode="json") for item in calls],
    }


def _task_metrics(
    report: dict[str, object], task_types: set[str]
) -> dict[str, object]:
    calls = [
        call for call in report.get("calls", [])
        if isinstance(call, dict) and call.get("task_type") in task_types
    ]
    cost = sum(
        (Decimal(str(call.get("rated_cost_usd") or "0")) for call in calls),
        start=Decimal("0"),
    )
    return {
        "model_invocations": len(calls),
        "prompt_tokens": sum(int(call.get("prompt_tokens") or 0) for call in calls),
        "cached_tokens": sum(int(call.get("cached_tokens") or 0) for call in calls),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in calls),
        "reasoning_tokens": sum(
            int(call.get("reasoning_tokens") or 0) for call in calls
        ),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in calls),
        "provider_latency_ms": sum(int(call.get("latency_ms") or 0) for call in calls),
        "rated_cost_usd": str(cost),
    }


async def run(args: argparse.Namespace) -> None:
    if args.command is None:
        print(json.dumps({"status": "ready", "model_invocations": 0}))
        return
    if args.command == "migrate-postgres":
        directory = os.environ.get("HEFESTO_MIGRATIONS_DIR", "/app/migrations")
        print(json.dumps({
            "status": "ready",
            "applied": apply_migrations(
                required("HEFESTO_POSTGRES_DIRECT_URL"), directory
            ),
        }))
        return
    if args.command == "clean-operational-history":
        client = storage_client()
        receipt_blob = client.bucket(required("HEFESTO_GCS_BUCKET")).blob(
            "hefesto-product/maintenance/freeze-cleanup.json"
        )
        previous = None
        if args.execute:
            try:
                previous = json.loads(receipt_blob.download_as_bytes())
            except NotFound:
                pass
        expected_ids = sorted(str(value) for value in args.keep_execution_run_id)
        if previous is not None:
            if (
                previous.get("status") != "executed"
                or sorted(previous.get("preserved_execution_run_ids", []))
                != expected_ids
            ):
                raise ValueError("existing cleanup receipt does not match request")
            report = previous
        else:
            report = await asyncio.to_thread(
                clean_operational_history,
                direct_url=required("HEFESTO_POSTGRES_DIRECT_URL"),
                client=client,
                bucket_name=required("HEFESTO_GCS_BUCKET"),
                keep_execution_ids=args.keep_execution_run_id,
                execute=args.execute,
            )
            if args.execute:
                receipt_blob.upload_from_string(
                    json.dumps(report, ensure_ascii=False, sort_keys=True).encode(),
                    content_type="application/json",
                )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.command == "ingest":
        client = storage_client()
        input_request = load_input_request(
            client, uri=args.request
        )
        result = await asyncio.to_thread(
            ingest, input_request, client=client,
            bucket_name=required("HEFESTO_GCS_BUCKET"),
        )
        execution_id = uuid4()
        repository, hefesto = await services()
        try:
            await hefesto.prepare(
                result.request, execution_run_id=execution_id
            )
            receipt = {
                "status": "source_structure_ready",
                "project_id": str(result.request.project_id),
                "execution_run_id": str(execution_id),
                "input": {
                    **result.receipt.model_dump(mode="json"),
                    "ingestion_receipt_uri": result.receipt_uri,
                },
                "usage": _empty_usage(),
                "generated_at": datetime.now(UTC).isoformat(),
            }
            uri = persist_receipt(
                client,
                bucket_name=required("HEFESTO_GCS_BUCKET"),
                execution_run_id=execution_id,
                payload=receipt,
            )
            print_receipt(receipt, uri)
        finally:
            await repository.close()
        return
    if args.command == "plan":
        await plan(args)
        return
    if args.command == "source-search":
        try:
            await source_search(args.execution_run_id)
        except Exception as error:
            await _record_pre_run_failure(
                args.execution_run_id, error, "source_research"
            )
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error,
                dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed",
            dispatch_key=args.dispatch_key,
        )
        return
    if args.command == "revise-provisional":
        try:
            await revise_provisional(args.execution_run_id, args.instruction)
        except Exception as error:
            await record_action_failure(
                args.execution_run_id,
                args.command,
                error,
                stage="provisional_editorial_revision",
            )
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error,
                dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed",
            dispatch_key=args.dispatch_key,
        )
        return
    if args.command == "extract-matrix":
        repository, hefesto = await services()
        try:
            await hefesto.extract_matrix(args.execution_run_id)
            report = await repository.matrix_report(args.execution_run_id)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        finally:
            await repository.close()
        return
    if args.command == "enrich-project-source":
        try:
            await enrich_project_source(args.execution_run_id, args.index)
        except Exception as error:
            await record_action_failure(args.execution_run_id, args.command, error)
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed",
                error, dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command,
            "completed", dispatch_key=args.dispatch_key,
        )
        return
    if args.command in {"revise-project", "accept-model-suggestion"}:
        try:
            if args.command == "revise-project":
                await revise_project(args.execution_run_id, args.instruction)
            else:
                await accept_model_suggestion(args.execution_run_id, args.index)
        except Exception as error:
            await record_action_failure(args.execution_run_id, args.command, error)
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed",
                error, dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command,
            "completed", dispatch_key=args.dispatch_key,
        )
        return
    if args.command == "resume-plan":
        try:
            await resume_plan(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error,
                dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed",
            dispatch_key=args.dispatch_key,
        )
        return
    if args.command == "approve-project":
        try:
            await approve_project(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error,
                dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed",
            dispatch_key=args.dispatch_key,
        )
        return
    if args.command == "produce-text":
        try:
            await produce_text(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error,
                dispatch_key=args.dispatch_key,
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed",
            dispatch_key=args.dispatch_key,
        )
        return
    repository, hefesto = await services()
    client = storage_client()
    try:
        if args.command == "check-postgres":
            print(json.dumps(await repository.check()))
            return
        if args.command == "inspect":
            await _persist_product_receipt(
                hefesto, client, args.execution_run_id, []
            )
            return
        raise ValueError(f"unsupported command {args.command}")
    finally:
        await repository.close()


def _input_transport(command: argparse.ArgumentParser) -> None:
    command.add_argument("--request", required=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Hefesto editorial product")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("migrate-postgres")
    commands.add_parser("check-postgres")
    cleanup = commands.add_parser("clean-operational-history")
    cleanup.add_argument("keep_execution_run_id", nargs="+", type=UUID)
    cleanup.add_argument("--execute", action="store_true")
    ingestion = commands.add_parser("ingest")
    _input_transport(ingestion)
    planning = commands.add_parser("plan")
    _input_transport(planning)
    planning.add_argument("--execution-run-id", type=UUID)
    planning.add_argument("--engine", choices=["legacy", "matrix"], default="legacy")
    matrix = commands.add_parser("extract-matrix")
    matrix.add_argument("--execution-run-id", type=UUID, required=True)
    revision = commands.add_parser("revise-project")
    revision.add_argument("--execution-run-id", type=UUID, required=True)
    revision.add_argument("--instruction", required=True)
    revision.add_argument("--dispatch-key")
    source_suggestion = commands.add_parser("enrich-project-source")
    source_suggestion.add_argument("--execution-run-id", type=UUID, required=True)
    source_suggestion.add_argument("--index", type=int, required=True)
    source_suggestion.add_argument("--dispatch-key")
    model_suggestion = commands.add_parser("accept-model-suggestion")
    model_suggestion.add_argument("--execution-run-id", type=UUID, required=True)
    model_suggestion.add_argument("--index", type=int, required=True)
    model_suggestion.add_argument("--dispatch-key")
    resume = commands.add_parser("resume-plan")
    resume.add_argument("--execution-run-id", type=UUID, required=True)
    resume.add_argument("--dispatch-key")
    source_search_parser = commands.add_parser("source-search")
    source_search_parser.add_argument("--execution-run-id", type=UUID, required=True)
    source_search_parser.add_argument("--dispatch-key")
    revise_provisional_parser = commands.add_parser("revise-provisional")
    revise_provisional_parser.add_argument("--execution-run-id", type=UUID, required=True)
    revise_provisional_parser.add_argument("--instruction", required=True)
    revise_provisional_parser.add_argument("--dispatch-key")
    approve_project_parser = commands.add_parser("approve-project")
    approve_project_parser.add_argument("--execution-run-id", type=UUID, required=True)
    approve_project_parser.add_argument("--dispatch-key")
    produce_text_parser = commands.add_parser("produce-text")
    produce_text_parser.add_argument("--execution-run-id", type=UUID, required=True)
    produce_text_parser.add_argument("--dispatch-key")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--execution-run-id", type=UUID, required=True)
    return root


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
