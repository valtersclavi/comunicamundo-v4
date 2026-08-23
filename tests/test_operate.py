import json
from pathlib import Path
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.operate import (
    PROOF_BUCKET,
    PROOF_JOB,
    main,
    prepare_operation,
    render_step_summary,
    summarize_execution,
    validate_plan_request_file,
)
from comunicamundo_v4.hefesto.worker import _parser


def _plan_payload() -> dict[str, object]:
    return {
        "project_id": str(uuid4()),
        "brief": {
            "theme": "Tema",
            "approach": "Abordagem",
            "duration_minutes": 8,
        },
        "sources": [
            {
                "source_id": str(uuid4()),
                "text": "Conteúdo que não deve ir para o summary",
                "language": "pt",
            }
        ],
    }


def test_prepare_plan_matches_worker_parser() -> None:
    uri = f"gs://{PROOF_BUCKET}/requests/{uuid4()}.json"
    prepared = prepare_operation(operation="plan", request_uri=uri)

    args = _parser().parse_args(list(prepared.worker_argv))

    assert prepared.mode == "dispatch"
    assert prepared.gcloud_args == f"plan,--request,{uri}"
    assert args.command == "plan"
    assert args.request == uri


def test_prepare_approve_and_produce_require_uuid() -> None:
    editorial_project_id = uuid4()

    approve = prepare_operation(
        operation="approve",
        editorial_project_id=str(editorial_project_id),
    )
    produce = prepare_operation(
        operation="produce",
        editorial_project_id=f"  {editorial_project_id}  ",
    )

    assert _parser().parse_args(list(approve.worker_argv)).editorial_project_id == (
        editorial_project_id
    )
    assert produce.gcloud_args == (
        f"produce,--editorial-project-id,{editorial_project_id}"
    )


def test_prepare_inspect_accepts_job_execution_name() -> None:
    prepared = prepare_operation(
        operation="inspect",
        execution_name="hefesto-v4-abc12",
    )

    assert prepared.mode == "inspect"
    assert prepared.worker_argv == ()
    assert prepared.gcloud_args == ""


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"operation": "plan"}, "plan requires only request_uri"),
        (
            {
                "operation": "plan",
                "request_uri": f"gs://{PROOF_BUCKET}/a.json",
                "editorial_project_id": str(uuid4()),
            },
            "plan requires only request_uri",
        ),
        (
            {"operation": "plan", "request_uri": "https://example.com/a.json"},
            "plan request must be a gs:// URI",
        ),
        (
            {"operation": "plan", "request_uri": "gs://other-bucket/a.json"},
            "plan request must use the V4 proof bucket",
        ),
        (
            {"operation": "plan", "request_uri": f"gs://{PROOF_BUCKET}/a,b.json"},
            "request URI cannot contain commas",
        ),
        (
            {"operation": "plan", "request_uri": f"gs://{PROOF_BUCKET}/../a.json"},
            "invalid Cloud Storage request URI",
        ),
        (
            {"operation": "approve", "request_uri": f"gs://{PROOF_BUCKET}/a.json"},
            "approve requires only editorial_project_id",
        ),
        (
            {"operation": "approve", "editorial_project_id": "not-a-uuid"},
            "editorial_project_id must be a UUID",
        ),
        (
            {
                "operation": "produce",
                "editorial_project_id": str(uuid4()),
                "execution_name": "hefesto-v4-abc12",
            },
            "produce requires only editorial_project_id",
        ),
        ({"operation": "inspect"}, "inspect requires only execution_name"),
        (
            {"operation": "inspect", "execution_name": "other-job-1"},
            "execution name must match",
        ),
        (
            {"operation": "inspect", "execution_name": "hefesto-v4-ABC"},
            "execution name must match",
        ),
        ({"operation": "delete"}, "operation must be plan, approve, produce, or inspect"),
    ],
)
def test_prepare_fails_closed(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_operation(**kwargs)


def test_validate_plan_request_file_omits_source_text(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    payload = _plan_payload()
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = validate_plan_request_file(request_path)

    assert summary["project_id"] == payload["project_id"]
    assert summary["source_count"] == 1
    assert "Conteúdo" not in json.dumps(summary)


def test_validate_plan_request_rejects_unknown_fields(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    payload = _plan_payload()
    payload["recipe"] = "blog-post-generator"
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception):
        validate_plan_request_file(request_path)


def test_summarize_execution_extracts_ids_and_skips_secrets() -> None:
    editorial_project_id = str(uuid4())
    execution = {
        "metadata": {
            "name": "hefesto-v4-abc12",
            "creationTimestamp": "2026-08-23T22:00:00Z",
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "image": "us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/hefesto-v4@sha256:abc",
                            "args": ["approve", "--editorial-project-id", editorial_project_id],
                        }
                    ]
                }
            }
        },
        "status": {
            "startTime": "2026-08-23T22:00:01Z",
            "completionTime": "2026-08-23T22:00:10Z",
            "succeededCount": 1,
            "failedCount": 0,
            "logUri": "https://console.cloud.google.com/logs/query",
        },
    }
    logs = "\n".join(
        [
            "Authorization: Bearer super-secret",
            json.dumps(
                {
                    "editorial_project": {
                        "editorial_project_id": editorial_project_id,
                        "project_id": str(uuid4()),
                        "status": "approved",
                        "working_title": "Título",
                    },
                    "graph_artifact": {
                        "manifest_uri": f"gs://{PROOF_BUCKET}/comunicamundo-v4/manifest.json",
                        "bucket": PROOF_BUCKET,
                    },
                }
            ),
        ]
    )

    report = summarize_execution(execution, log_text=logs)

    assert report["state"] == "SUCCEEDED"
    assert report["evidence"]["editorial_project_id"] == editorial_project_id
    assert report["evidence"]["manifest_uri"].startswith("gs://")
    assert "super-secret" not in json.dumps(report)


def test_cli_prepare_writes_github_output(tmp_path: Path) -> None:
    output = tmp_path / "github.output"
    uri = f"gs://{PROOF_BUCKET}/requests/job.json"
    exit_code = main(
        [
            "prepare",
            "--operation",
            "plan",
            "--request-uri",
            uri,
            "--github-output",
            str(output),
        ]
    )

    text = output.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "operation=plan" in text
    assert f"gcloud_args=plan,--request,{uri}" in text


def test_cli_prepare_fails_closed_for_invalid_combination() -> None:
    assert (
        main(
            [
                "prepare",
                "--operation",
                "approve",
                "--request-uri",
                f"gs://{PROOF_BUCKET}/a.json",
            ]
        )
        == 1
    )


def test_empty_optional_fields_are_ignored_for_plan() -> None:
    uri = f"gs://{PROOF_BUCKET}/requests/{uuid4()}.json"
    prepared = prepare_operation(
        operation="plan",
        request_uri=uri,
        editorial_project_id="",
        execution_name="   ",
    )
    assert prepared.editorial_project_id is None
    assert prepared.execution_name is None


def test_operate_workflow_stays_on_the_v4_proof_project() -> None:
    workflow = Path(".github/workflows/operate.yml").read_text(encoding="utf-8")

    assert "PROJECT_ID: comunicamundo-v4" in workflow
    assert "BUCKET: comunicamundo-v4-hefesto-30524898837" in workflow
    assert "github-v4-deployer@comunicamundo-v4.iam.gserviceaccount.com" in workflow
    assert "hefesto-v4-sa@comunicamundo-v4.iam.gserviceaccount.com" in workflow
    assert "workloadIdentityPools/github-actions-v4/providers/github-v4" in workflow
    assert "--async" in workflow
    assert "STAGING_" not in workflow
    assert "comunicamundo-v3" not in workflow
    assert "workflow_dispatch" in workflow


def test_step_summary_does_not_include_secrets() -> None:
    prepared = prepare_operation(
        operation="inspect",
        execution_name=f"{PROOF_JOB}-abc12",
    )
    summary = render_step_summary(
        prepared,
        execution_name=f"{PROOF_JOB}-abc12",
        inspect_report={
            "state": "RUNNING",
            "log_uri": "https://console.cloud.google.com/logs/query",
            "evidence": {"editorial_project_id": str(uuid4())},
        },
    )

    assert "OPERATE: INSPECTED" in summary
    assert "Bearer" not in summary
    assert "api_key" not in summary
