import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.worker import PlanningJobRequest, _decode_feedback, _parser, _read_request


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
