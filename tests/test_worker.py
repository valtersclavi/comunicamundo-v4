import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.runtime import RuntimeProfile
from comunicamundo_v4.hefesto.worker import (
    PlanningJobRequest,
    _decode_feedback,
    _parser,
    _read_request,
    _safe_failure_payload,
    load_planning_request,
    main,
)


def test_local_planning_request_is_strictly_validated(tmp_path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [{"source_id": str(uuid4()), "text": "Conteúdo", "language": "pt"}],
            }
        ),
        encoding="utf-8",
    )
    request = PlanningJobRequest.model_validate(_read_request(str(request_path), project="unused"))
    assert request.brief.duration_minutes == 8
    assert len(request.sources) == 1
    loaded = load_planning_request(str(request_path), project="unused")
    assert loaded.project_id == request.project_id


def test_planning_request_rejects_recipe_fields(tmp_path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [{"source_id": str(uuid4()), "text": "Conteúdo", "language": "pt"}],
                "recipe": "blog-post-generator",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        PlanningJobRequest.model_validate(_read_request(str(request_path), project="unused"))


def test_axis_and_project_commands_require_valid_ids() -> None:
    axis_id = uuid4()
    project_id = uuid4()
    encoded = base64.urlsafe_b64encode("Inclua o luto do adulto.".encode()).decode()

    approve_axis = _parser().parse_args(["approve-axis", "--narrative-axis-id", str(axis_id)])
    revise_axis = _parser().parse_args(
        ["revise-axis", "--narrative-axis-id", str(axis_id), "--feedback-b64", encoded]
    )
    revise_project = _parser().parse_args(
        ["revise-project", "--editorial-project-id", str(project_id), "--feedback-b64", encoded]
    )

    assert approve_axis.narrative_axis_id == axis_id
    assert revise_axis.narrative_axis_id == axis_id
    assert revise_project.editorial_project_id == project_id
    assert _decode_feedback(encoded) == "Inclua o luto do adulto."


def test_safe_failure_omits_validation_input_and_secrets() -> None:
    with pytest.raises(ValidationError) as caught:
        PlanningJobRequest.model_validate(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [
                    {
                        "source_id": str(uuid4()),
                        "text": "texto confidencial da fonte",
                        "language": "pt",
                    }
                ],
                "recipe": "blog-post-generator",
            }
        )
    payload = _safe_failure_payload(caught.value)
    assert payload["error_type"] == "ValidationError"
    assert "recipe" in payload["error"]
    assert "texto confidencial" not in payload["error"]

    secret = _safe_failure_payload(ValueError("Authorization Bearer abc.def"))
    assert secret["error"] == "ValueError"
    assert "Bearer" not in secret["error"]


def test_worker_emits_json_failure_before_runtime_construction(
    tmp_path, monkeypatch, capsys
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [{"source_id": str(uuid4()), "text": "Conteúdo", "language": "pt"}],
                "recipe": "blog-post-generator",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "comunicamundo-v4")
    monkeypatch.setenv("HEFESTO_GCS_BUCKET", "comunicamundo-v4-hefesto-30524898837")

    def fail_if_runtime_built(*args, **kwargs):
        raise AssertionError("Vertex runtime must not be built for an invalid request")

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.build_vertex_runtime", fail_if_runtime_built
    )
    with pytest.raises(SystemExit) as caught:
        main(["plan", "--request", str(request_path)])
    assert caught.value.code == 1
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["error_type"] == "ValidationError"
    assert "recipe" in printed["error"]


def test_plan_requests_planning_runtime_profile(tmp_path, monkeypatch, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [{"source_id": str(uuid4()), "text": "Conteúdo", "language": "pt"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "comunicamundo-v4")
    monkeypatch.setenv("HEFESTO_GCS_BUCKET", "comunicamundo-v4-hefesto-30524898837")
    captured: dict[str, object] = {}

    def fake_runtime(settings, *, profile):
        captured["profile"] = profile
        return object()

    class FakeApplication:
        def __init__(self, *, runtime, persistence) -> None:
            captured["runtime"] = runtime

        async def plan_project(self, **kwargs):
            return type("Stored", (), {"model_dump_json": lambda self: "{}"})()

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.build_vertex_runtime", fake_runtime
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.HefestoApplication", FakeApplication
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.GcpEditorialPersistence.from_settings",
        classmethod(lambda cls, settings: object()),
    )
    main(["plan", "--request", str(request_path)])
    assert captured["profile"] is RuntimeProfile.PLAN
    assert capsys.readouterr().out.strip() == "{}"
