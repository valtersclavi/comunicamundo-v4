import json
from pathlib import Path
from uuid import uuid4

from comunicamundo_v4.hefesto.chatops import (
    CHATOPS_ALLOWED_USER,
    CHATOPS_CONTROL_ISSUE,
    CHATOPS_REPOSITORY,
    main,
    prepare_chatops_comment,
    prepare_chatops_event,
)


def _event(body: str) -> dict:
    return {
        "action": "created",
        "repository": {"full_name": CHATOPS_REPOSITORY},
        "issue": {"number": CHATOPS_CONTROL_ISSUE},
        "comment": {
            "body": body,
            "user": {"login": CHATOPS_ALLOWED_USER},
        },
    }


def test_plan_comment_reuses_canonical_validation() -> None:
    prepared = prepare_chatops_comment(
        '/hefesto {"operation":"plan","request_uri":"gs://comunicamundo-v4-hefesto-30524898837/requests/test.json"}',
        issue_number=CHATOPS_CONTROL_ISSUE,
        actor=CHATOPS_ALLOWED_USER,
        repository=CHATOPS_REPOSITORY,
    )
    assert prepared.operation == "plan"
    assert prepared.mode == "dispatch"
    assert prepared.worker_argv == (
        "plan",
        "--request",
        "gs://comunicamundo-v4-hefesto-30524898837/requests/test.json",
    )


def test_probe_request_read_comment_does_not_dispatch_plan() -> None:
    uri = "gs://comunicamundo-v4-hefesto-30524898837/requests/13101c35-723d-43fe-9b45-0082b49c0a76.json"
    prepared = prepare_chatops_comment(
        '/hefesto {"operation":"probe-request-read","request_uri":"%s"}' % uri,
        issue_number=CHATOPS_CONTROL_ISSUE,
        actor=CHATOPS_ALLOWED_USER,
        repository=CHATOPS_REPOSITORY,
    )
    assert prepared.operation == "probe-request-read"
    assert prepared.worker_argv == (
        "probe",
        "--boundary",
        "request-read",
        "--request",
        uri,
    )
    assert "plan" not in prepared.gcloud_args


def test_revision_feedback_is_encoded_and_not_rendered() -> None:
    axis_id = str(uuid4())
    feedback = "Sem esquecer que o adulto também está sofrendo muito."
    prepared = prepare_chatops_event(
        _event(
            "/hefesto "
            + json.dumps(
                {
                    "operation": "revise-axis",
                    "narrative_axis_id": axis_id,
                    "feedback": feedback,
                },
                ensure_ascii=False,
            )
        )
    )
    assert prepared.operation == "revise-axis"
    assert prepared.feedback_supplied is True
    assert feedback not in prepared.gcloud_args
    assert "--feedback-b64" in prepared.worker_argv
    assert prepared.non_sensitive_arguments()["feedback"] == "provided"


def test_wrong_control_surface_is_rejected() -> None:
    body = '/hefesto {"operation":"inspect","execution_name":"hefesto-v4-abc12"}'
    for kwargs in (
        {"issue_number": 999, "actor": CHATOPS_ALLOWED_USER, "repository": CHATOPS_REPOSITORY},
        {"issue_number": CHATOPS_CONTROL_ISSUE, "actor": "someone-else", "repository": CHATOPS_REPOSITORY},
        {"issue_number": CHATOPS_CONTROL_ISSUE, "actor": CHATOPS_ALLOWED_USER, "repository": "other/repo"},
    ):
        try:
            prepare_chatops_comment(body, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("unauthorized ChatOps surface was accepted")


def test_pull_request_comment_is_rejected() -> None:
    event = _event('/hefesto {"operation":"inspect","execution_name":"hefesto-v4-abc12"}')
    event["issue"]["pull_request"] = {"url": "https://example.invalid"}
    try:
        prepare_chatops_event(event)
    except ValueError as error:
        assert "not a pull request" in str(error)
    else:
        raise AssertionError("pull request comment was accepted")


def test_payload_shape_fails_closed() -> None:
    bad_payloads = (
        "/hefesto not-json",
        '/hefesto {"operation":"approve","editorial_project_id":"x","extra":"no"}',
        '/hefesto {"operation":123}',
        '/hefesto {"operation":"probe-embed-construct","request_uri":"gs://comunicamundo-v4-hefesto-30524898837/a.json"}',
    )
    for body in bad_payloads:
        try:
            prepare_chatops_event(_event(body))
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid payload was accepted: {body}")


def test_cli_writes_safe_outputs(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    output_file = tmp_path / "output.txt"
    summary_file = tmp_path / "summary.md"
    execution_name = "hefesto-v4-abc12"
    event_file.write_text(
        json.dumps(
            _event(
                "/hefesto "
                + json.dumps(
                    {"operation": "inspect", "execution_name": execution_name}
                )
            )
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--event-file",
            str(event_file),
            "--github-output",
            str(output_file),
            "--summary-file",
            str(summary_file),
        ]
    )
    assert result == 0
    outputs = output_file.read_text(encoding="utf-8")
    assert "operation=inspect" in outputs
    assert f"execution_name={execution_name}" in outputs
    assert "mode=inspect" in outputs
    assert execution_name in summary_file.read_text(encoding="utf-8")
