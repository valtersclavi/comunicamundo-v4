import base64
from uuid import uuid4
import pytest
from comunicamundo_v4.hefesto.worker import _feedback, _parser


def test_worker_exposes_both_approval_boundaries() -> None:
    parser = _parser()
    axis_id = str(uuid4())
    project_id = str(uuid4())
    assert parser.parse_args(["approve-axis", "--narrative-axis-id", axis_id]).command == "approve-axis"
    assert parser.parse_args(["approve-project", "--editorial-project-id", project_id]).command == "approve-project"


def test_feedback_is_transport_encoded_but_natural_language() -> None:
    text = "Inclua que o adulto também está sofrendo."
    encoded = base64.urlsafe_b64encode(text.encode()).decode()
    assert _feedback(encoded) == text
    with pytest.raises(ValueError):
        _feedback("")
