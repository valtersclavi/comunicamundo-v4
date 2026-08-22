"""Cloud Run Job entrypoint for planning, approval, or production."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from uuid import UUID

from google.cloud import storage
from pydantic import Field

from .application import HefestoApplication
from .contracts import EditorialBrief, NormalizedSource, StrictModel
from .persistence import GcpEditorialPersistence, GcpPersistenceSettings
from .runtime import VertexRuntimeSettings, build_vertex_runtime


MAX_REQUEST_BYTES = 50 * 1024 * 1024


class PlanningJobRequest(StrictModel):
    project_id: UUID
    brief: EditorialBrief
    sources: list[NormalizedSource] = Field(min_length=1)


def _required_environment(name: str) -> str:
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
        payload = (
            storage.Client(project=project)
            .bucket(parsed.netloc)
            .blob(object_name)
            .download_as_bytes()
        )
    else:
        payload = Path(location).read_bytes()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("planning request exceeds the 50 MiB limit")
    parsed_payload = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed_payload, dict):
        raise ValueError("planning request must be a JSON object")
    return parsed_payload


def _build_application() -> HefestoApplication:
    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    bucket = _required_environment("HEFESTO_GCS_BUCKET")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return HefestoApplication(
        runtime=build_vertex_runtime(
            VertexRuntimeSettings(project=project, location=location)
        ),
        persistence=GcpEditorialPersistence.from_settings(
            GcpPersistenceSettings(project=project, bucket=bucket)
        ),
    )


async def _run(args: argparse.Namespace) -> None:
    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    application = _build_application()
    if args.command == "plan":
        request = PlanningJobRequest.model_validate(
            _read_request(args.request, project=project)
        )
        result = await application.plan_project(
            project_id=request.project_id,
            brief=request.brief,
            sources=request.sources,
        )
    elif args.command == "approve":
        result = await application.approve_editorial_project(
            args.editorial_project_id
        )
    else:
        result = await application.produce_article(args.editorial_project_id)
    print(result.model_dump_json())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ComunicAmundo V4 Hefesto worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="build an Editorial Project")
    plan.add_argument("--request", required=True, help="local path or gs:// JSON URI")
    approve = subparsers.add_parser("approve", help="approve an Editorial Project")
    approve.add_argument("--editorial-project-id", required=True, type=UUID)
    produce = subparsers.add_parser("produce", help="write an approved project")
    produce.add_argument("--editorial-project-id", required=True, type=UUID)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
