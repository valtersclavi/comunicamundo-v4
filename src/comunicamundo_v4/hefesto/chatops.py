"""Permanent GitHub issue-comment control surface for Hefesto V4."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .operate import PreparedOperation, prepare_operation, render_step_summary

CHATOPS_PREFIX = "/hefesto "
CHATOPS_REPOSITORY = "valtersclavi/comunicamundo-v4"
CHATOPS_CONTROL_ISSUE = 6
CHATOPS_ALLOWED_USER = "valtersclavi"
CHATOPS_MAX_BYTES = 16 * 1024
_ALLOWED_FIELDS = {
    "operation",
    "request_uri",
    "narrative_axis_id",
    "editorial_project_id",
    "feedback",
    "execution_name",
}


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} is missing or invalid")
    return value


def _string_or_none(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def prepare_chatops_comment(
    body: str,
    *,
    issue_number: int,
    actor: str,
    repository: str,
    is_pull_request: bool = False,
    control_issue: int = CHATOPS_CONTROL_ISSUE,
    allowed_user: str = CHATOPS_ALLOWED_USER,
    allowed_repository: str = CHATOPS_REPOSITORY,
) -> PreparedOperation:
    """Validate one authorized control comment and reuse the canonical operator."""

    if repository != allowed_repository:
        raise ValueError("ChatOps repository is not authorized")
    if is_pull_request:
        raise ValueError("ChatOps control must be a GitHub issue, not a pull request")
    if issue_number != control_issue:
        raise ValueError("ChatOps comment is outside the control issue")
    if actor != allowed_user:
        raise ValueError("ChatOps actor is not authorized")
    if not isinstance(body, str) or not body.startswith(CHATOPS_PREFIX):
        raise ValueError("ChatOps command must start with /hefesto followed by JSON")
    if len(body.encode("utf-8")) > CHATOPS_MAX_BYTES:
        raise ValueError("ChatOps command exceeds the 16 KiB limit")

    raw = body[len(CHATOPS_PREFIX) :].strip()
    if not raw:
        raise ValueError("ChatOps JSON payload is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("ChatOps payload must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("ChatOps payload must be a JSON object")

    unexpected = sorted(set(payload) - _ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(f"unexpected ChatOps fields: {', '.join(unexpected)}")
    operation = payload.get("operation")
    if not isinstance(operation, str):
        raise ValueError("operation must be a string")

    return prepare_operation(
        operation=operation,
        request_uri=_string_or_none(payload, "request_uri"),
        narrative_axis_id=_string_or_none(payload, "narrative_axis_id"),
        editorial_project_id=_string_or_none(payload, "editorial_project_id"),
        feedback=_string_or_none(payload, "feedback"),
        execution_name=_string_or_none(payload, "execution_name"),
    )


def prepare_chatops_event(
    event: Mapping[str, Any],
    *,
    control_issue: int = CHATOPS_CONTROL_ISSUE,
    allowed_user: str = CHATOPS_ALLOWED_USER,
    allowed_repository: str = CHATOPS_REPOSITORY,
) -> PreparedOperation:
    """Extract and validate a GitHub issue_comment event."""

    if event.get("action") != "created":
        raise ValueError("ChatOps accepts only newly created comments")
    repository = _mapping(event.get("repository"), field="repository")
    issue = _mapping(event.get("issue"), field="issue")
    comment = _mapping(event.get("comment"), field="comment")
    user = _mapping(comment.get("user"), field="comment.user")

    full_name = repository.get("full_name")
    number = issue.get("number")
    actor = user.get("login")
    body = comment.get("body")
    if not isinstance(full_name, str):
        raise ValueError("repository.full_name is missing or invalid")
    if not isinstance(number, int):
        raise ValueError("issue.number is missing or invalid")
    if not isinstance(actor, str):
        raise ValueError("comment.user.login is missing or invalid")
    if not isinstance(body, str):
        raise ValueError("comment.body is missing or invalid")

    return prepare_chatops_comment(
        body,
        issue_number=number,
        actor=actor,
        repository=full_name,
        is_pull_request="pull_request" in issue,
        control_issue=control_issue,
        allowed_user=allowed_user,
        allowed_repository=allowed_repository,
    )


def _outputs(prepared: PreparedOperation) -> dict[str, str]:
    return {
        "operation": prepared.operation,
        "mode": prepared.mode,
        "gcloud_args": prepared.gcloud_args,
        "request_uri": prepared.request_uri or "",
        "narrative_axis_id": prepared.narrative_axis_id or "",
        "editorial_project_id": prepared.editorial_project_id or "",
        "execution_name": prepared.execution_name or "",
    }


def _write_github_output(path: Path, values: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value:
                raise ValueError(f"refusing multiline GitHub output: {key}")
            handle.write(f"{key}={value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Hefesto V4 ChatOps commands")
    parser.add_argument("--event-file", required=True, type=Path)
    parser.add_argument("--control-issue", type=int, default=CHATOPS_CONTROL_ISSUE)
    parser.add_argument("--allowed-user", default=CHATOPS_ALLOWED_USER)
    parser.add_argument("--allowed-repository", default=CHATOPS_REPOSITORY)
    parser.add_argument("--github-output")
    parser.add_argument("--summary-file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        event = json.loads(args.event_file.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("GitHub event must be a JSON object")
        prepared = prepare_chatops_event(
            event,
            control_issue=args.control_issue,
            allowed_user=args.allowed_user,
            allowed_repository=args.allowed_repository,
        )
        if args.github_output:
            _write_github_output(Path(args.github_output), _outputs(prepared))
        summary = render_step_summary(prepared)
        if args.summary_file:
            Path(args.summary_file).write_text(summary, encoding="utf-8")
        else:
            sys.stdout.write(summary)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
