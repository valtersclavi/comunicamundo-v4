"""Cloud Run Job entrypoint for Hefesto V4 durable editorial operations."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
from uuid import UUID

from google.cloud import storage
from pydantic import Field, ValidationError

from .application import HefestoApplication
from .contracts import EditorialBrief, NormalizedSource, StrictModel
from .persistence import GcpEditorialPersistence, GcpPersistenceSettings
from .runtime import VertexRuntimeSettings, build_vertex_runtime, profile_for_command


MAX_REQUEST_BYTES = 50 * 1024 * 1024
MAX_FEEDBACK_BYTES = 8 * 1024
MAX_FAILURE_MESSAGE_BYTES = 2000
_SENSITIVE = re.compile(
    r"(?i)(secret|token|password|api[_-]?key|authorization|bearer|"
    r"private[_-]?key|begin (rsa |ec |openssh )?private)"
)


class PlanningJobRequest(StrictModel):
    project_id: UUID
    brief: EditorialBrief
    sources: list[NormalizedSource] = Field(min_length=1)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def load_planning_request(location: str, *, project: str) -> PlanningJobRequest:
    """Read and strictly validate one planning request. No Vertex, no persistence."""

    return PlanningJobRequest.model_validate(_read_request(location, project=project))


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


def _decode_feedback(value: str) -> str:
    try:
        payload = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as error:
        raise ValueError("feedback must be URL-safe base64") from error
    if len(payload) > MAX_FEEDBACK_BYTES:
        raise ValueError("feedback exceeds the 8 KiB limit")
    try:
        feedback = payload.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError("feedback must be UTF-8") from error
    if not feedback:
        raise ValueError("feedback cannot be empty")
    return feedback


def _build_application(command: str) -> HefestoApplication:
    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    bucket = _required_environment("HEFESTO_GCS_BUCKET")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return HefestoApplication(
        runtime=build_vertex_runtime(
            VertexRuntimeSettings(project=project, location=location),
            profile=profile_for_command(command),
        ),
        persistence=GcpEditorialPersistence.from_settings(
            GcpPersistenceSettings(project=project, bucket=bucket)
        ),
    )


def _safe_failure_payload(error: BaseException) -> dict[str, str]:
    """Compact, non-sensitive failure for Cloud Run stdout and inspect."""

    if isinstance(error, ValidationError):
        details = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg')}"
            for item in error.errors(include_url=False, include_input=False)
        )
        message = details or "request validation failed"
    else:
        message = str(error).strip() or type(error).__name__
    if _SENSITIVE.search(message):
        message = type(error).__name__
    return {
        "error_type": type(error).__name__,
        "error": message[:MAX_FAILURE_MESSAGE_BYTES],
    }


def _emit_safe_failure(error: BaseException) -> None:
    print(json.dumps(_safe_failure_payload(error), ensure_ascii=False), flush=True)


async def _run(args: argparse.Namespace) -> None:
    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    if args.command == "probe":
        from .probe import run_probe

        print(
            json.dumps(
                run_probe(args.boundary, request=args.request),
                ensure_ascii=False,
            )
        )
        return
    request: PlanningJobRequest | None = None
    if args.command == "plan":
        request = load_planning_request(args.request, project=project)
    application = _build_application(args.command)
    if args.command == "plan":
        assert request is not None
        result = await application.plan_project(
            project_id=request.project_id,
            brief=request.brief,
            sources=request.sources,
        )
    elif args.command == "revise-axis":
        result = await application.revise_narrative_axis(
            args.narrative_axis_id,
            _decode_feedback(args.feedback_b64),
        )
    elif args.command == "approve-axis":
        result = await application.approve_narrative_axis(args.narrative_axis_id)
    elif args.command == "revise-project":
        result = await application.revise_editorial_project(
            args.editorial_project_id,
            _decode_feedback(args.feedback_b64),
        )
    elif args.command == "approve":
        result = await application.approve_editorial_project(args.editorial_project_id)
    else:
        result = await application.produce_article(args.editorial_project_id)
    print(result.model_dump_json())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ComunicAmundo V4 Hefesto worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="build graph and propose Narrative Axis")
    plan.add_argument("--request", required=True, help="local path or gs:// JSON URI")

    revise_axis = subparsers.add_parser("revise-axis", help="revise a proposed Narrative Axis")
    revise_axis.add_argument("--narrative-axis-id", required=True, type=UUID)
    revise_axis.add_argument("--feedback-b64", required=True)

    approve_axis = subparsers.add_parser("approve-axis", help="approve Narrative Axis and build Editorial Project")
    approve_axis.add_argument("--narrative-axis-id", required=True, type=UUID)

    revise_project = subparsers.add_parser("revise-project", help="revise a proposed Editorial Project")
    revise_project.add_argument("--editorial-project-id", required=True, type=UUID)
    revise_project.add_argument("--feedback-b64", required=True)

    approve = subparsers.add_parser("approve", help="approve an Editorial Project")
    approve.add_argument("--editorial-project-id", required=True, type=UUID)

    produce = subparsers.add_parser("produce", help="write an approved Editorial Project")
    produce.add_argument("--editorial-project-id", required=True, type=UUID)

    probe = subparsers.add_parser(
        "probe",
        help="run one atomic plan-startup boundary; later boundaries stay refused",
    )
    probe.add_argument(
        "--boundary",
        required=True,
        help="one of: request-read, persistence, embed-construct, "
        "graph-llm-construct, axis-llm-construct, graph-init, "
        "graph-extract, axis-generate",
    )
    probe.add_argument(
        "--request",
        help="local path or gs:// JSON URI (required for request-read)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as error:
        _emit_safe_failure(error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
