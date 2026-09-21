from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from time import perf_counter
from uuid import UUID, uuid4

from google.cloud import storage
from google.api_core.exceptions import NotFound

from .gateway import VertexGateway
from .contracts import CallUsage, ProvisionalEditorialProject, SourceMode
from .ingestion import IngestionResult, ingest, ingest_acquired
from .maintenance import clean_operational_history
from .postgres import ProductRepository, apply_migrations
from .service import Hefesto
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
                input_request, execution_id, client, bucket_name
            )
        except Exception as error:
            await _record_pre_run_failure(
                execution_id, error, "provisional_editorial_project"
            )
            raise
        return
    stages: list[dict[str, object]] = []
    started = perf_counter()
    try:
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
        )
    except Exception as error:
        receipt = {
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
    stages.append({
        "stage": "ingestion",
        "duration_ms": int((perf_counter() - started) * 1000),
        "receipt_uri": ingestion.receipt_uri,
        "model_invocations": 0,
    })
    repository, hefesto = await services()
    try:
        started = perf_counter()
        await hefesto.prepare(
            ingestion.request, execution_run_id=execution_id
        )
        stages.append({
            "stage": "relational_structure",
            "duration_ms": int((perf_counter() - started) * 1000),
            "model_invocations": 0,
        })
        await hefesto.record_stages(execution_id, stages)
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
        )
        stages.append({
            "stage": "redigestion", "status": "completed",
            "duration_ms": int((perf_counter() - started) * 1000),
            "receipt_uri": ingestion.receipt_uri,
        })
        hefesto = Hefesto(repository, gateway)
        await hefesto.prepare(ingestion.request, execution_run_id=execution_id)
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
                    "editorial_subject_corpus",
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
) -> None:
    await hefesto.record_stages(execution_id, stages)
    receipt = {
        **(await hefesto.receipt(execution_id)),
        "input": (
            {
                "status": ingestion.receipt.status,
                "ingestion_receipt_uri": ingestion.receipt_uri,
                "normalized_request_uri": ingestion.receipt.normalized_request_uri,
                "source_count": ingestion.receipt.source_count,
                "paragraph_count": ingestion.receipt.paragraph_count,
                "sentence_count": ingestion.receipt.sentence_count,
            }
            if ingestion is not None else None
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
