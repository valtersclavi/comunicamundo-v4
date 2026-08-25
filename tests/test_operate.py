import base64
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


def _plan_payload():
    return {
        "project_id": str(uuid4()),
        "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
        "sources": [{"source_id": str(uuid4()), "text": "Conteúdo que não deve ir para o summary", "language": "pt"}],
    }


def test_prepare_plan_matches_worker_parser() -> None:
    uri = f"gs://{PROOF_BUCKET}/requests/{uuid4()}.json"
    prepared = prepare_operation(operation="plan", request_uri=uri)
    args = _parser().parse_args(list(prepared.worker_argv))
    assert prepared.gcloud_args == f"plan,--request,{uri}"
    assert args.command == "plan"


def test_prepare_axis_approval_and_revision() -> None:
    axis_id = uuid4()
    approved = prepare_operation(operation="approve-axis", narrative_axis_id=str(axis_id))
    revised = prepare_operation(operation="revise-axis", narrative_axis_id=str(axis_id), feedback="Sem esquecer que o adulto também está sofrendo.")
    assert approved.gcloud_args == f"approve-axis,--narrative-axis-id,{axis_id}"
    args = _parser().parse_args(list(revised.worker_argv))
    assert args.command == "revise-axis"
    assert base64.urlsafe_b64decode(args.feedback_b64).decode().startswith("Sem esquecer")
    assert "Sem esquecer" not in render_step_summary(revised)


def test_prepare_project_revision_approval_and_production() -> None:
    project_id = uuid4()
    revised = prepare_operation(operation="revise-project", editorial_project_id=str(project_id), feedback="A práxis precisa ter mais peso.")
    approve = prepare_operation(operation="approve", editorial_project_id=str(project_id))
    produce = prepare_operation(operation="produce", editorial_project_id=str(project_id))
    assert _parser().parse_args(list(revised.worker_argv)).command == "revise-project"
    assert approve.gcloud_args == f"approve,--editorial-project-id,{project_id}"
    assert produce.gcloud_args == f"produce,--editorial-project-id,{project_id}"


def test_prepare_inspect_accepts_job_execution_name() -> None:
    prepared = prepare_operation(operation="inspect", execution_name="hefesto-v4-abc12")
    assert prepared.mode == "inspect"
    assert prepared.worker_argv == ()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"operation": "plan"}, "plan requires only request_uri"),
        ({"operation": "approve-axis"}, "approve-axis requires only narrative_axis_id"),
        ({"operation": "revise-axis", "narrative_axis_id": str(uuid4())}, "revise-axis requires"),
        ({"operation": "revise-project", "editorial_project_id": str(uuid4())}, "revise-project requires"),
        ({"operation": "approve", "request_uri": f"gs://{PROOF_BUCKET}/a.json"}, "approve requires only editorial_project_id"),
        ({"operation": "inspect"}, "inspect requires only execution_name"),
        ({"operation": "delete"}, "operation must be plan"),
    ],
)
def test_prepare_fails_closed(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_operation(**kwargs)


def test_validate_plan_request_file_omits_source_text(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    payload = _plan_payload()
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    summary = validate_plan_request_file(request_path)
    assert summary["project_id"] == payload["project_id"]
    assert "Conteúdo" not in json.dumps(summary)


def test_summarize_execution_surfaces_axis_and_compact_project() -> None:
    axis_id = str(uuid4())
    project_id = str(uuid4())
    execution = {
        "metadata": {"name": "hefesto-v4-abc12", "creationTimestamp": "2026-08-24T22:00:00Z"},
        "spec": {"template": {"spec": {"containers": [{"image": "image", "args": ["approve-axis", "--narrative-axis-id", axis_id]}]}}},
        "status": {"completionTime": "2026-08-24T22:00:10Z", "succeededCount": 1, "failedCount": 0},
    }
    logs = json.dumps({"editorial_project": {"editorial_project_id": project_id, "project_id": str(uuid4()), "status": "proposed", "working_title": "Título", "narrative_axis": {"narrative_axis_id": axis_id, "statement": "O adulto também está de luto."}, "subjects": [{"title": "Assunto", "development": "O que precisa ser desenvolvido."}]}})
    report = summarize_execution(execution, log_text=logs)
    assert report["state"] == "SUCCEEDED"
    assert report["evidence"]["narrative_axis"] == "O adulto também está de luto."
    assert "Assunto" in report["evidence"]["subjects"]


def test_cli_prepare_writes_github_output(tmp_path: Path) -> None:
    output = tmp_path / "github.output"
    uri = f"gs://{PROOF_BUCKET}/requests/job.json"
    assert main(["prepare", "--operation", "plan", "--request-uri", uri, "--github-output", str(output)]) == 0
    assert f"gcloud_args=plan,--request,{uri}" in output.read_text(encoding="utf-8")


def test_operate_workflow_exposes_both_approval_boundaries() -> None:
    workflow = Path(".github/workflows/operate.yml").read_text(encoding="utf-8")
    assert "PROJECT_ID: comunicamundo-v4" in workflow
    assert "- revise-axis" in workflow
    assert "- approve-axis" in workflow
    assert "- revise-project" in workflow
    assert "feedback:" in workflow
    assert "--async" in workflow
    assert "STAGING_" not in workflow
    assert "comunicamundo-v3" not in workflow


def test_execution_describe_does_not_use_unsupported_job_flag() -> None:
    for path in (
        Path(".github/workflows/operate.yml"),
        Path(".github/workflows/chatops-operate.yml"),
    ):
        workflow = path.read_text(encoding="utf-8")
        assert 'gcloud run jobs executions describe "${PREPARED_EXECUTION_NAME}"' in workflow
        inspect_block = workflow.split(
            'gcloud run jobs executions describe "${PREPARED_EXECUTION_NAME}"', 1
        )[1].split('--format=json > "${RUNNER_TEMP}/execution.json"', 1)[0]
        assert '--job="${JOB_NAME}"' not in inspect_block


def test_planning_request_is_read_only_by_worker_runtime() -> None:
    for path in (
        Path(".github/workflows/operate.yml"),
        Path(".github/workflows/chatops-operate.yml"),
    ):
        workflow = path.read_text(encoding="utf-8")
        assert "gcloud storage cat" not in workflow
        assert "validate-plan-request" not in workflow
        assert "--args=\"${PREPARED_GCLOUD_ARGS}\"" in workflow


def test_step_summary_does_not_include_feedback_or_secrets() -> None:
    prepared = prepare_operation(operation="revise-axis", narrative_axis_id=str(uuid4()), feedback="texto sensível do usuário")
    summary = render_step_summary(prepared)
    assert "texto sensível" not in summary
    assert "feedback: `provided`" in summary
    assert "Bearer" not in summary
