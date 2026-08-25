import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.worker import PlanningJobRequest, _parser, _read_request


def test_local_planning_request_is_strictly_validated(tmp_path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {
                    "theme": "Tema",
                    "approach": "Abordagem",
                    "duration_minutes": 8,
                },
                "sources": [
                    {
                        "source_id": str(uuid4()),
                        "text": "Conteúdo",
                        "language": "pt",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    request = PlanningJobRequest.model_validate(
        _read_request(str(request_path), project="unused")
    )

    assert request.brief.duration_minutes == 8
    assert len(request.sources) == 1


def test_planning_request_rejects_recipe_fields(tmp_path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {
                    "theme": "Tema",
                    "approach": "Abordagem",
                    "duration_minutes": 8,
                },
                "sources": [
                    {
                        "source_id": str(uuid4()),
                        "text": "Conteúdo",
                        "language": "pt",
                    }
                ],
                "recipe": "blog-post-generator",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        PlanningJobRequest.model_validate(
            _read_request(str(request_path), project="unused")
        )


def test_axis_commands_require_narrative_axis_identity() -> None:
    narrative_axis_id = uuid4()

    revised = _parser().parse_args(
        [
            "revise-axis",
            "--narrative-axis-id",
            str(narrative_axis_id),
            "--feedback",
            "inclua que o adulto também sofre",
        ]
    )
    approved = _parser().parse_args(
        [
            "approve-axis",
            "--narrative-axis-id",
            str(narrative_axis_id),
        ]
    )

    assert revised.command == "revise-axis"
    assert revised.narrative_axis_id == narrative_axis_id
    assert revised.feedback == "inclua que o adulto também sofre"
    assert approved.command == "approve-axis"
    assert approved.narrative_axis_id == narrative_axis_id


def test_approval_command_requires_a_valid_editorial_project_id() -> None:
    editorial_project_id = uuid4()

    args = _parser().parse_args(
        [
            "approve",
            "--editorial-project-id",
            str(editorial_project_id),
        ]
    )

    assert args.command == "approve"
    assert args.editorial_project_id == editorial_project_id
