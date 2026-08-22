import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.worker import PlanningJobRequest, _read_request


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
