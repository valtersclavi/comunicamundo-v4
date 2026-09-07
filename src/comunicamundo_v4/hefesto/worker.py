from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from google.cloud import storage

from .gateway import VertexGateway
from .postgres import ProductRepository, apply_migrations
from .proof_requests import stage_proof_request
from .service import Hefesto
from .storage import load_request, persist_receipt


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing environment variable {name}")
    return value


async def services() -> tuple[ProductRepository, Hefesto, storage.Client]:
    project = required("GOOGLE_CLOUD_PROJECT")
    repository = ProductRepository(required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    client = storage.Client(project=project)
    gateway = VertexGateway(
        project=project,
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    return repository, Hefesto(repository, gateway), client


async def run(args: argparse.Namespace) -> None:
    if args.command is None:
        print(json.dumps({"status": "ready", "model_invocations": 0}))
        return
    if args.command == "migrate-postgres":
        directory = os.environ.get("HEFESTO_MIGRATIONS_DIR", "/app/migrations")
        print(json.dumps({"status": "ready", "applied": apply_migrations(required("HEFESTO_POSTGRES_DIRECT_URL"), directory)}))
        return
    repository, hefesto, client = await services()
    try:
        if args.command == "check-postgres":
            print(json.dumps(await repository.check()))
            return
        if args.command == "digest":
            result = await hefesto.digest(load_request(client, args.request))
            print(result.model_dump_json())
            return
        if args.command == "resume-digest":
            result = await hefesto.resume_digest(args.execution_run_id)
            print(result.model_dump_json())
            return
        if args.command == "propose-axis":
            print((await hefesto.propose_axis(args.execution_run_id)).model_dump_json())
            return
        if args.command == "approve-axis":
            print((await hefesto.approve_axis_and_propose_project(args.execution_run_id)).model_dump_json())
            return
        if args.command == "inspect":
            print(json.dumps(await hefesto.receipt(args.execution_run_id), ensure_ascii=False))
            return
        if args.command == "run-proof":
            request, request_uri = await asyncio.to_thread(
                stage_proof_request,
                args.variant,
                client=client,
                bucket_name=required("HEFESTO_GCS_BUCKET"),
            )
            execution_id = uuid4()
            stages: list[dict[str, object]] = []
            active_stage = "digestion"
            started = perf_counter()
            try:
                current = await hefesto.digest(request, execution_run_id=execution_id)
                stages.append({"stage": active_stage, "duration_ms": int((perf_counter() - started) * 1000)})
                active_stage, started = "narrative_axis", perf_counter()
                current = await hefesto.propose_axis(current.execution_run_id)
                stages.append({"stage": active_stage, "duration_ms": int((perf_counter() - started) * 1000)})
                active_stage, started = "editorial_project", perf_counter()
                await hefesto.approve_axis_and_propose_project(current.execution_run_id)
                stages.append({"stage": active_stage, "duration_ms": int((perf_counter() - started) * 1000)})
            except Exception:
                stages.append({"stage": active_stage, "status": "failed", "duration_ms": int((perf_counter() - started) * 1000)})
                receipt = {
                    **(await hefesto.receipt(execution_id)),
                    "proof": {"variant": args.variant, "request_uri": request_uri},
                    "stages": stages,
                    "generated_at": datetime.now(UTC).isoformat(),
                }
                uri = persist_receipt(
                    client, bucket_name=required("HEFESTO_GCS_BUCKET"),
                    execution_run_id=execution_id, payload=receipt,
                )
                print(json.dumps({**receipt, "receipt_uri": uri}, ensure_ascii=False), flush=True)
                raise
            receipt = {
                **(await hefesto.receipt(execution_id)),
                "proof": {"variant": args.variant, "request_uri": request_uri},
                "stages": stages,
                "generated_at": datetime.now(UTC).isoformat(),
            }
            uri = persist_receipt(
                client, bucket_name=required("HEFESTO_GCS_BUCKET"),
                execution_run_id=execution_id, payload=receipt,
            )
            print(json.dumps({**receipt, "receipt_uri": uri}, ensure_ascii=False), flush=True)
            return
        raise ValueError(f"unsupported command {args.command}")
    finally:
        await repository.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Hefesto paragraph-first product")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("migrate-postgres")
    commands.add_parser("check-postgres")
    digest = commands.add_parser("digest")
    digest.add_argument("--request", required=True)
    resume = commands.add_parser("resume-digest")
    resume.add_argument("--execution-run-id", type=UUID, required=True)
    axis = commands.add_parser("propose-axis")
    axis.add_argument("--execution-run-id", type=UUID, required=True)
    approve = commands.add_parser("approve-axis")
    approve.add_argument("--execution-run-id", type=UUID, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--execution-run-id", type=UUID, required=True)
    proof = commands.add_parser("run-proof")
    proof.add_argument("--variant", choices=("A",), required=True)
    return root


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
