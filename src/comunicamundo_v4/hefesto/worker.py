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

from .gateway import VertexGateway
from .ingestion import IngestionResult, ingest
from .maintenance import clean_operational_history
from .postgres import ProductRepository, apply_migrations
from .service import Hefesto
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
    stages: list[dict[str, object]] = []
    started = perf_counter()
    try:
        ingestion = await asyncio.to_thread(
            ingest,
            input_request,
            client=client,
            bucket_name=bucket_name,
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
                    "editorial_project",
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
) -> None:
    blob = client.bucket(required("HEFESTO_GCS_BUCKET")).blob(
        f"hefesto-product/executions/{execution_id}/{action}-dispatch.json"
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
        report = await asyncio.to_thread(
            clean_operational_history,
            direct_url=required("HEFESTO_POSTGRES_DIRECT_URL"),
            client=client,
            bucket_name=required("HEFESTO_GCS_BUCKET"),
            keep_execution_ids=args.keep_execution_run_id,
            execute=args.execute,
        )
        if args.execute:
            client.bucket(required("HEFESTO_GCS_BUCKET")).blob(
                "hefesto-product/maintenance/freeze-cleanup.json"
            ).upload_from_string(
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
    if args.command == "resume-plan":
        try:
            await resume_plan(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed"
        )
        return
    if args.command == "approve-project":
        try:
            await approve_project(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed"
        )
        return
    if args.command == "produce-text":
        try:
            await produce_text(args.execution_run_id)
        except Exception as error:
            mark_action_dispatch(
                storage_client(), args.execution_run_id, args.command, "failed", error
            )
            raise
        mark_action_dispatch(
            storage_client(), args.execution_run_id, args.command, "completed"
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
    cleanup.add_argument(
        "--keep-execution-run-id", action="append", type=UUID, required=True
    )
    cleanup.add_argument("--execute", action="store_true")
    ingestion = commands.add_parser("ingest")
    _input_transport(ingestion)
    planning = commands.add_parser("plan")
    _input_transport(planning)
    planning.add_argument("--execution-run-id", type=UUID)
    resume = commands.add_parser("resume-plan")
    resume.add_argument("--execution-run-id", type=UUID, required=True)
    approve_project_parser = commands.add_parser("approve-project")
    approve_project_parser.add_argument("--execution-run-id", type=UUID, required=True)
    produce_text_parser = commands.add_parser("produce-text")
    produce_text_parser.add_argument("--execution-run-id", type=UUID, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--execution-run-id", type=UUID, required=True)
    return root


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
