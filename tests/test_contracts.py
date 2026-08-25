from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectStatus,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisStatus,
)


def test_tripod_is_mandatory() -> None:
    with pytest.raises(ValidationError):
        EditorialBrief(theme="", approach="documental", duration_minutes=8)

    with pytest.raises(ValidationError):
        EditorialBrief(theme="luto infantil", approach="", duration_minutes=8)

    with pytest.raises(ValidationError):
        EditorialBrief(theme="luto infantil", approach="documental", duration_minutes=0)


def test_editorial_project_is_a_list_of_subjects_with_duration_budget() -> None:
    brief = EditorialBrief(
        theme="O vivenciar do luto na infância",
        approach="Texto sensível e acessível para familiares",
        duration_minutes=8,
    )
    axis_id = uuid4()
    project = EditorialProject(
        project_id=uuid4(),
        brief=brief,
        narrative_axis_id=axis_id,
        narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender como a criança vive e expressa o luto.",
        subjects=[
            EditorialSubject(
                title="Como a criança compreende a morte",
                purpose="Estabelecer a perspectiva infantil.",
                research_questions=["Como a compreensão muda com a idade?"],
                estimated_minutes=4,
            ),
            EditorialSubject(
                title="O papel da comunicação adulta",
                purpose="Mostrar como adultos podem acolher a experiência.",
                research_questions=["O que ajuda uma comunicação honesta?"],
                estimated_minutes=4,
            ),
        ],
    )

    approved = project.approve()

    assert approved.status is EditorialProjectStatus.APPROVED
    assert approved.approved_at is not None
    assert [subject.title for subject in approved.subjects] == [
        "Como a criança compreende a morte",
        "O papel da comunicação adulta",
    ]
    assert project.narrative_axis_id == axis_id
    assert "sofrimento adulto" in project.narrative_axis


def test_narrative_axis_is_proposed_until_explicit_approval() -> None:
    axis = NarrativeAxis(
        project_id=uuid4(),
        statement="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
    )
    approved = axis.approve()

    assert axis.status is NarrativeAxisStatus.PROPOSED
    assert axis.approved_at is None
    assert approved.status is NarrativeAxisStatus.APPROVED
    assert approved.approved_at is not None
    with pytest.raises(ValidationError):
        approved.statement = "outro centro"


def test_editorial_project_rejects_unbounded_duration() -> None:
    with pytest.raises(ValidationError, match="duration allocation"):
        EditorialProject(
            project_id=uuid4(),
            brief=EditorialBrief(
                theme="Tema",
                approach="Abordagem",
                duration_minutes=8,
            ),
            narrative_axis_id=uuid4(),
            narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
            working_title="Título",
            editorial_promise="Promessa",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    purpose="Propósito",
                    research_questions=["Pergunta?"],
                    estimated_minutes=2,
                )
            ],
        )

