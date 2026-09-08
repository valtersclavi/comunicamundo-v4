from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from time import perf_counter
from uuid import UUID, uuid4

from google.cloud import storage

from .gateway import VertexGateway
from .ingestion import IngestionResult, ingest
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
        uri=getattr(args, "request", None),
        encoded=getattr(args, "request_base64", None),
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
            }],
            "generated_at": datetime.now(UTC).isoformat(),
        }
        uri = persist_receipt(
            client, bucket_name=bucket_name,
            execution_run_id=execution_id, payload=receipt,
        )
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
        await _continue_to_axis(
            hefesto, client, execution_id, stages,
            ingestion=ingestion,
        )
    finally:
        await repository.close()


async def resume_plan(execution_id: UUID) -> None:
    client = storage_client()
    repository, hefesto = await services()
    try:
        stages: list[dict[str, object]] = []
        await _continue_to_axis(hefesto, client, execution_id, stages)
    finally:
        await repository.close()


async def _continue_to_axis(
    hefesto: Hefesto,
    client: storage.Client,
    execution_id: UUID,
    stages: list[dict[str, object]],
    *,
    ingestion: IngestionResult | None = None,
) -> None:
    active_stage = "digestion"
    started = perf_counter()
    try:
        if ingestion is None:
            current = await hefesto.resume_digest(execution_id)
        else:
            current = await hefesto.digest_prepared(execution_id)
        stages.append({
            "stage": active_stage,
            "duration_ms": int((perf_counter() - started) * 1000),
        })
        active_stage, started = "narrative_axis", perf_counter()
        await hefesto.propose_axis(current.execution_run_id)
        stages.append({
            "stage": active_stage,
            "duration_ms": int((perf_counter() - started) * 1000),
        })
    except Exception:
        stages.append({
            "stage": active_stage, "status": "failed",
            "duration_ms": int((perf_counter() - started) * 1000),
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


async def approve_axis(execution_id: UUID) -> None:
    client = storage_client()
    repository, hefesto = await services()
    started = perf_counter()
    stages: list[dict[str, object]] = []
    try:
        try:
            await hefesto.approve_axis_and_propose_project(execution_id)
            stages.append({
                "stage": "editorial_project",
                "duration_ms": int((perf_counter() - started) * 1000),
            })
        except Exception:
            stages.append({
                "stage": "editorial_project", "status": "failed",
                "duration_ms": int((perf_counter() - started) * 1000),
            })
            await _persist_product_receipt(
                hefesto, client, execution_id, stages
            )
            raise
        await _persist_product_receipt(
            hefesto, client, execution_id, stages
        )
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
        "stages": stages,
        "generated_at": datetime.now(UTC).isoformat(),
    }
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
    if args.command == "ingest":
        client = storage_client()
        input_request = load_input_request(
            client, uri=args.request, encoded=args.request_base64
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
        await resume_plan(args.execution_run_id)
        return
    if args.command == "approve-axis":
        await approve_axis(args.execution_run_id)
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
    transport = command.add_mutually_exclusive_group(required=True)
    transport.add_argument("--request")
    transport.add_argument("--request-base64")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Hefesto editorial product")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("migrate-postgres")
    commands.add_parser("check-postgres")
    ingestion = commands.add_parser("ingest")
    _input_transport(ingestion)
    planning = commands.add_parser("plan")
    _input_transport(planning)
    planning.add_argument("--execution-run-id", type=UUID)
    resume = commands.add_parser("resume-plan")
    resume.add_argument("--execution-run-id", type=UUID, required=True)
    approve = commands.add_parser("approve-axis")
    approve.add_argument("--execution-run-id", type=UUID, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--execution-run-id", type=UUID, required=True)
    return root


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
