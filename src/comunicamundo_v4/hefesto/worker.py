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
from pydantic import Field

from .application import HefestoApplication
from .contracts import EditorialBrief, NormalizedSource, StrictModel
from .persistence import GcpEditorialPersistence, GcpPersistenceSettings
from .runtime import VertexRuntimeSettings, build_vertex_runtime

MAX_REQUEST_BYTES = 50 * 1024 * 1024


class PlanningRequest(StrictModel):
    project_id: UUID
    brief: EditorialBrief
    sources: list[NormalizedSource] = Field(min_length=1)


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


def _feedback(encoded: str) -> str:
    try:
        value = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8").strip()
    except Exception as exc:
        raise ValueError("invalid feedback encoding") from exc
    if not value:
        raise ValueError("feedback cannot be empty")
    return value


def _application() -> HefestoApplication:
    project = _required("GOOGLE_CLOUD_PROJECT")
    bucket = _required("HEFESTO_GCS_BUCKET")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return HefestoApplication(
        runtime=build_vertex_runtime(VertexRuntimeSettings(project=project, location=location)),
        persistence=GcpEditorialPersistence.from_settings(GcpPersistenceSettings(project=project, bucket=bucket)),
    )


async def _run(args: argparse.Namespace) -> None:
    app = _application()
    if args.command == "plan":
        project = _required("GOOGLE_CLOUD_PROJECT")
        request = PlanningRequest.model_validate(_read_request(args.request, project=project))
        result = await app.plan(project_id=request.project_id, brief=request.brief, sources=request.sources)
    elif args.command == "revise-axis":
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
    plan = commands.add_parser("plan", help="build a fresh graph and propose a Narrative Axis")
    plan.add_argument("--request", required=True)
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
