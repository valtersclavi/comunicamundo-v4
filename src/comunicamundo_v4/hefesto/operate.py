"""Validate and prepare iPad/GitHub Actions operation of the Hefesto worker."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import UUID

from .worker import MAX_REQUEST_BYTES, PlanningJobRequest, _parser, _read_request

PROOF_PROJECT = "comunicamundo-v4"
PROOF_BUCKET = "comunicamundo-v4-hefesto-30524898837"
PROOF_JOB = "hefesto-v4"
PROOF_REGION = "us-central1"
OPERATIONS = (
    "plan",
    "revise-axis",
    "approve-axis",
    "revise-project",
    "approve",
    "produce",
    "inspect",
)
_BLANK = ""
_SENSITIVE = re.compile(
    r"(?i)(secret|token|password|api[_-]?key|authorization|bearer|"
    r"private[_-]?key|begin (rsa |ec |openssh )?private)"
)
_EXECUTION_NAME = re.compile(
    rf"^{re.escape(PROOF_JOB)}-[a-z0-9](?:[a-z0-9-]{{0,48}}[a-z0-9])?$"
)


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    operation: str
    mode: str
    worker_argv: tuple[str, ...]
    request_uri: str | None = None
    narrative_axis_id: str | None = None
    editorial_project_id: str | None = None
    execution_name: str | None = None
    feedback_supplied: bool = False

    @property
    def gcloud_args(self) -> str:
        if any("," in part for part in self.worker_argv):
            raise ValueError("Cloud Run --args values cannot contain commas")
        return ",".join(self.worker_argv)

    def non_sensitive_arguments(self) -> dict[str, str]:
        payload = {"operation": self.operation, "mode": self.mode}
        if self.request_uri:
            payload["request_uri"] = self.request_uri
        if self.narrative_axis_id:
            payload["narrative_axis_id"] = self.narrative_axis_id
        if self.editorial_project_id:
            payload["editorial_project_id"] = self.editorial_project_id
        if self.execution_name:
            payload["execution_name"] = self.execution_name
        if self.feedback_supplied:
            payload["feedback"] = "provided"
        if self.worker_argv:
            safe_argv = list(self.worker_argv)
            if "--feedback-b64" in safe_argv:
                index = safe_argv.index("--feedback-b64")
                if index + 1 < len(safe_argv):
                    safe_argv[index + 1] = "<encoded>"
            payload["worker_argv"] = " ".join(safe_argv)
        return payload


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _require_uuid(value: str, *, field: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError(f"{field} must be a UUID") from error


def _encode_feedback(value: str) -> str:
    feedback = value.strip()
    if not feedback:
        raise ValueError("feedback cannot be empty")
    if len(feedback.encode("utf-8")) > 8 * 1024:
        raise ValueError("feedback exceeds the 8 KiB limit")
    return base64.urlsafe_b64encode(feedback.encode("utf-8")).decode("ascii")


def _require_request_uri(value: str, *, allowed_bucket: str) -> str:
    if "," in value:
        raise ValueError("request URI cannot contain commas")
    parsed = urlparse(value)
    object_name = parsed.path.lstrip("/")
    if parsed.scheme != "gs":
        raise ValueError("plan request must be a gs:// URI")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("request URI cannot contain query or fragment")
    if not parsed.netloc or not object_name:
        raise ValueError("invalid Cloud Storage request URI")
    if parsed.netloc != allowed_bucket:
        raise ValueError("plan request must use the V4 proof bucket")
    if ".." in object_name.split("/"):
        raise ValueError("invalid Cloud Storage request URI")
    return value


def _require_execution_name(value: str) -> str:
    if not _EXECUTION_NAME.fullmatch(value):
        raise ValueError(
            f"execution name must match {PROOF_JOB}-<id> and stay within Cloud Run limits"
        )
    return value


def _unexpected_fields(provided: Mapping[str, str | None], allowed: set[str]) -> list[str]:
    return sorted(
        name for name, value in provided.items() if value is not None and name not in allowed
    )


def prepare_operation(
    *,
    operation: str,
    request_uri: str | None = None,
    narrative_axis_id: str | None = None,
    editorial_project_id: str | None = None,
    feedback: str | None = None,
    execution_name: str | None = None,
    allowed_bucket: str = PROOF_BUCKET,
) -> PreparedOperation:
    command = _blank_to_none(operation)
    if command not in OPERATIONS:
        raise ValueError(
            "operation must be plan, revise-axis, approve-axis, revise-project, approve, produce, or inspect"
        )
    fields = {
        "request_uri": _blank_to_none(request_uri),
        "narrative_axis_id": _blank_to_none(narrative_axis_id),
        "editorial_project_id": _blank_to_none(editorial_project_id),
        "feedback": _blank_to_none(feedback),
        "execution_name": _blank_to_none(execution_name),
    }

    if command == "plan":
        unexpected = _unexpected_fields(fields, {"request_uri"})
        if unexpected or fields["request_uri"] is None:
            raise ValueError("plan requires only request_uri")
        uri = _require_request_uri(fields["request_uri"], allowed_bucket=allowed_bucket)
        argv = ("plan", "--request", uri)
        _parser().parse_args(list(argv))
        return PreparedOperation(command, "dispatch", argv, request_uri=uri)

    if command == "approve-axis":
        unexpected = _unexpected_fields(fields, {"narrative_axis_id"})
        if unexpected or fields["narrative_axis_id"] is None:
            raise ValueError("approve-axis requires only narrative_axis_id")
        axis_id = _require_uuid(fields["narrative_axis_id"], field="narrative_axis_id")
        argv = ("approve-axis", "--narrative-axis-id", axis_id)
        _parser().parse_args(list(argv))
        return PreparedOperation(command, "dispatch", argv, narrative_axis_id=axis_id)

    if command == "revise-axis":
        unexpected = _unexpected_fields(fields, {"narrative_axis_id", "feedback"})
        if unexpected or fields["narrative_axis_id"] is None or fields["feedback"] is None:
            raise ValueError("revise-axis requires narrative_axis_id and feedback only")
        axis_id = _require_uuid(fields["narrative_axis_id"], field="narrative_axis_id")
        encoded = _encode_feedback(fields["feedback"])
        argv = ("revise-axis", "--narrative-axis-id", axis_id, "--feedback-b64", encoded)
        _parser().parse_args(list(argv))
        return PreparedOperation(
            command,
            "dispatch",
            argv,
            narrative_axis_id=axis_id,
            feedback_supplied=True,
        )

    if command == "revise-project":
        unexpected = _unexpected_fields(fields, {"editorial_project_id", "feedback"})
        if unexpected or fields["editorial_project_id"] is None or fields["feedback"] is None:
            raise ValueError("revise-project requires editorial_project_id and feedback only")
        project_id = _require_uuid(
            fields["editorial_project_id"], field="editorial_project_id"
        )
        encoded = _encode_feedback(fields["feedback"])
        argv = (
            "revise-project",
            "--editorial-project-id",
            project_id,
            "--feedback-b64",
            encoded,
        )
        _parser().parse_args(list(argv))
        return PreparedOperation(
            command,
            "dispatch",
            argv,
            editorial_project_id=project_id,
            feedback_supplied=True,
        )

    if command in {"approve", "produce"}:
        unexpected = _unexpected_fields(fields, {"editorial_project_id"})
        if unexpected or fields["editorial_project_id"] is None:
            raise ValueError(f"{command} requires only editorial_project_id")
        project_id = _require_uuid(
            fields["editorial_project_id"], field="editorial_project_id"
        )
        argv = (command, "--editorial-project-id", project_id)
        _parser().parse_args(list(argv))
        return PreparedOperation(
            command, "dispatch", argv, editorial_project_id=project_id
        )

    unexpected = _unexpected_fields(fields, {"execution_name"})
    if unexpected or fields["execution_name"] is None:
        raise ValueError("inspect requires only execution_name")
    name = _require_execution_name(fields["execution_name"])
    return PreparedOperation(command, "inspect", (), execution_name=name)


def validate_plan_request_file(path: Path, *, project: str = PROOF_PROJECT) -> dict[str, Any]:
    if path.stat().st_size > MAX_REQUEST_BYTES:
        raise ValueError("planning request exceeds the 50 MiB limit")
    request = PlanningJobRequest.model_validate(_read_request(str(path), project=project))
    return {
        "project_id": str(request.project_id),
        "theme": request.brief.theme,
        "approach": request.brief.approach,
        "duration_minutes": request.brief.duration_minutes,
        "output_language": request.brief.output_language,
        "source_count": len(request.sources),
    }


def _container_spec(execution: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        return execution["spec"]["template"]["spec"]["containers"][0]
    except (KeyError, IndexError, TypeError):
        return {}


def _status_name(execution: Mapping[str, Any]) -> str:
    status = execution.get("status")
    if not isinstance(status, Mapping):
        return "UNKNOWN"
    if status.get("completionTime"):
        if int(status.get("failedCount") or 0) > 0:
            return "FAILED"
        if int(status.get("cancelledCount") or 0) > 0:
            return "CANCELLED"
        if int(status.get("succeededCount") or 0) > 0:
            return "SUCCEEDED"
        return "COMPLETED"
    if status.get("startTime"):
        return "RUNNING"
    return "PENDING"


def _json_objects_from_logs(log_text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if _SENSITIVE.search(line) or not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
    return objects


def _evidence_from_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    axis = payload.get("narrative_axis")
    if isinstance(axis, Mapping):
        for source, target in (
            ("narrative_axis_id", "narrative_axis_id"),
            ("project_id", "project_id"),
            ("status", "narrative_axis_status"),
            ("version", "narrative_axis_version"),
            ("statement", "narrative_axis"),
        ):
            if axis.get(source) not in (None, _BLANK):
                evidence[target] = axis[source]

    editorial = payload.get("editorial_project")
    if isinstance(editorial, Mapping):
        for key in (
            "editorial_project_id",
            "project_id",
            "status",
            "working_title",
            "version",
        ):
            if editorial.get(key) not in (None, _BLANK):
                evidence[key] = editorial[key]
        nested_axis = editorial.get("narrative_axis")
        if isinstance(nested_axis, Mapping) and nested_axis.get("statement"):
            evidence["narrative_axis"] = nested_axis["statement"]
            if nested_axis.get("narrative_axis_id"):
                evidence["narrative_axis_id"] = nested_axis["narrative_axis_id"]
        subjects = editorial.get("subjects")
        if isinstance(subjects, list):
            rendered: list[str] = []
            for item in subjects:
                if isinstance(item, Mapping) and item.get("title"):
                    development = item.get("development") or ""
                    rendered.append(f"{item['title']} — {development}".strip())
            if rendered:
                evidence["subjects"] = " | ".join(rendered)

    graph = payload.get("graph_artifact")
    if isinstance(graph, Mapping):
        for key in ("artifact_id", "manifest_uri", "bucket", "prefix"):
            if graph.get(key) not in (None, _BLANK):
                evidence[key] = graph[key]
    if payload.get("editorial_project_id") not in (None, _BLANK):
        evidence.setdefault("editorial_project_id", payload["editorial_project_id"])
    for key in (
        "article_id",
        "project_id",
        "body_uri",
        "word_count",
        "quality_score",
        "title",
        "error_type",
        "error",
    ):
        if payload.get(key) not in (None, _BLANK) and key not in evidence:
            evidence[key] = payload[key]
    return evidence


def summarize_execution(
    execution: Mapping[str, Any], *, log_text: str = _BLANK
) -> dict[str, Any]:
    metadata = execution.get("metadata")
    status = execution.get("status")
    container = _container_spec(execution)
    if not isinstance(metadata, Mapping):
        raise ValueError("execution metadata is missing")
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("execution name is missing")
    report = {
        "execution_name": name,
        "job": PROOF_JOB,
        "project": PROOF_PROJECT,
        "region": PROOF_REGION,
        "state": _status_name(execution),
        "creation_timestamp": metadata.get("creationTimestamp"),
        "start_time": status.get("startTime") if isinstance(status, Mapping) else None,
        "completion_time": (
            status.get("completionTime") if isinstance(status, Mapping) else None
        ),
        "succeeded_count": (
            status.get("succeededCount") if isinstance(status, Mapping) else None
        ),
        "failed_count": status.get("failedCount") if isinstance(status, Mapping) else None,
        "log_uri": status.get("logUri") if isinstance(status, Mapping) else None,
        "image": container.get("image") if isinstance(container, Mapping) else None,
        "args": container.get("args") if isinstance(container, Mapping) else None,
        "evidence": {},
    }
    for payload in _json_objects_from_logs(log_text):
        report["evidence"].update(_evidence_from_worker_payload(payload))
    return report


def render_step_summary(
    prepared: PreparedOperation,
    *,
    execution_name: str | None = None,
    inspect_report: Mapping[str, Any] | None = None,
    plan_request: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> str:
    lines = [
        "# ComunicAmundo V4 operate",
        "",
        f"- Operation: `{prepared.operation}`",
        f"- Mode: `{prepared.mode}`",
        f"- Project: `{PROOF_PROJECT}`",
        f"- Job: `{PROOF_JOB}`",
        f"- Region: `{PROOF_REGION}`",
    ]
    for key, value in prepared.non_sensitive_arguments().items():
        if key not in {"operation", "mode"}:
            lines.append(f"- {key}: `{value}`")
    if plan_request:
        lines.append(f"- plan.project_id: `{plan_request['project_id']}`")
        lines.append(f"- plan.theme: `{plan_request['theme']}`")
        lines.append(f"- plan.duration_minutes: `{plan_request['duration_minutes']}`")
        lines.append(f"- plan.source_count: `{plan_request['source_count']}`")
    if execution_name:
        lines.append(f"- Execution: `{execution_name}`")
    if inspect_report:
        lines.append(f"- State: `{inspect_report.get('state', 'UNKNOWN')}`")
        if inspect_report.get("log_uri"):
            lines.append(f"- Logs: {inspect_report['log_uri']}")
        evidence = inspect_report.get("evidence") or {}
        if evidence:
            lines.extend(["", "## Evidence"])
            for key, value in evidence.items():
                lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("- Model invocation: not started by this workflow dispatch helper")
    if error:
        lines.extend(["", "## OPERATE: FAILED", "", error])
    elif prepared.mode == "dispatch" and execution_name:
        lines.extend(["", "## OPERATE: DISPATCHED"])
    elif prepared.mode == "inspect" and inspect_report:
        lines.extend(["", "## OPERATE: INSPECTED"])
    else:
        lines.extend(["", "## OPERATE: PREPARED"])
    return "\n".join(lines) + "\n"


def _write_github_output(path: Path, values: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value:
                raise ValueError(f"refusing to write multiline GitHub output: {key}")
            handle.write(f"{key}={value}\n")


def _parser_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Hefesto V4 iPad operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--operation", required=True)
    prepare.add_argument("--request-uri", default=_BLANK)
    prepare.add_argument("--narrative-axis-id", default=_BLANK)
    prepare.add_argument("--editorial-project-id", default=_BLANK)
    prepare.add_argument("--feedback", default=_BLANK)
    prepare.add_argument("--execution-name", default=_BLANK)
    prepare.add_argument("--github-output")
    prepare.add_argument("--summary-file")

    validate = subparsers.add_parser("validate-plan-request")
    validate.add_argument("--request-file", required=True, type=Path)
    validate.add_argument("--github-output")
    validate.add_argument("--summary-file")

    inspect = subparsers.add_parser("summarize-inspect")
    inspect.add_argument("--execution-json", required=True, type=Path)
    inspect.add_argument("--log-file", type=Path)
    inspect.add_argument("--github-output")
    inspect.add_argument("--summary-file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser_cli().parse_args(argv)
    try:
        if args.command == "prepare":
            prepared = prepare_operation(
                operation=args.operation,
                request_uri=args.request_uri,
                narrative_axis_id=args.narrative_axis_id,
                editorial_project_id=args.editorial_project_id,
                feedback=args.feedback,
                execution_name=args.execution_name,
            )
            outputs = {
                "operation": prepared.operation,
                "mode": prepared.mode,
                "gcloud_args": prepared.gcloud_args,
                "request_uri": prepared.request_uri or _BLANK,
                "narrative_axis_id": prepared.narrative_axis_id or _BLANK,
                "editorial_project_id": prepared.editorial_project_id or _BLANK,
                "execution_name": prepared.execution_name or _BLANK,
            }
            summary = render_step_summary(prepared)
        elif args.command == "validate-plan-request":
            plan_request = validate_plan_request_file(args.request_file)
            outputs = {f"plan_{key}": str(value) for key, value in plan_request.items()}
            prepared = PreparedOperation("plan", "dispatch", ())
            summary = render_step_summary(prepared, plan_request=plan_request)
        else:
            execution = json.loads(args.execution_json.read_text(encoding="utf-8"))
            log_text = (
                args.log_file.read_text(encoding="utf-8") if args.log_file else _BLANK
            )
            report = summarize_execution(execution, log_text=log_text)
            prepared = prepare_operation(
                operation="inspect", execution_name=report["execution_name"]
            )
            outputs = {
                "execution_name": report["execution_name"],
                "state": report["state"],
            }
            summary = render_step_summary(prepared, inspect_report=report)
        if getattr(args, "github_output", None):
            _write_github_output(Path(args.github_output), outputs)
        if getattr(args, "summary_file", None):
            Path(args.summary_file).write_text(summary, encoding="utf-8")
        else:
            sys.stdout.write(summary)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
