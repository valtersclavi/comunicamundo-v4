from uuid import uuid4
import pytest
from comunicamundo_v4.hefesto.contracts import EditorialBrief, EditorialProject, EditorialSubject, NarrativeAxis, NarrativeAxisStatus


def test_axis_requires_human_approval() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Uma direção intelectual específica.")
    assert axis.status is NarrativeAxisStatus.PROPOSED
    approved = axis.approve()
    assert approved.status is NarrativeAxisStatus.APPROVED
    assert approved.approved_at is not None
    assert axis.status is NarrativeAxisStatus.PROPOSED


def test_approved_axis_is_immutable_to_supersede() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Direção.").approve()
    with pytest.raises(ValueError):
        axis.supersede()


def test_project_carries_axis_and_requires_separate_approval() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Direção.").approve()
    project = EditorialProject(
        project_id=axis.project_id,
        narrative_axis_id=axis.narrative_axis_id,
        narrative_axis=axis.statement,
        brief=EditorialBrief(theme="Luto infantil", approach="sensível", duration_minutes=8),
        working_title="Título",
        editorial_promise="Promessa",
        core_subjects=[EditorialSubject(title="Assunto", development="Desenvolvimento")],
    )
    assert project.status.value == "proposed"
    assert project.approve().status.value == "approved"
