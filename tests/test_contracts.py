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


def _brief() -> EditorialBrief:
    return EditorialBrief(
        theme="O vivenciar do luto na infância",
        approach="Texto sensível e acessível para familiares",
        duration_minutes=8,
    )


def _approved_axis(project_id=None) -> NarrativeAxis:
    return NarrativeAxis(
        project_id=project_id or uuid4(),
        brief=_brief(),
        statement=(
            "Proteger a criança não é escondê-la da perda, mas ajudá-la a atravessá-la "
            "com verdade e segurança, reconhecendo que os adultos também estão sofrendo."
        ),
    ).approve()


def test_tripod_is_mandatory() -> None:
    with pytest.raises(ValidationError):
        EditorialBrief(theme="", approach="documental", duration_minutes=8)
    with pytest.raises(ValidationError):
        EditorialBrief(theme="luto infantil", approach="", duration_minutes=8)
    with pytest.raises(ValidationError):
        EditorialBrief(theme="luto infantil", approach="documental", duration_minutes=0)


def test_narrative_axis_is_first_approvable_object_and_revision_is_versioned() -> None:
    axis = NarrativeAxis(
        project_id=uuid4(),
        brief=_brief(),
        statement="A verdade pode ser uma forma de cuidado no luto infantil.",
    )
    revised = axis.revise(
        "A verdade pode ser uma forma de cuidado, sem esquecer que o adulto também vive a perda."
    )
    approved = revised.approve()

    assert axis.status is NarrativeAxisStatus.PROPOSED
    assert revised.version == 2
    assert approved.status is NarrativeAxisStatus.APPROVED
    assert approved.approved_at is not None
    with pytest.raises(ValueError, match="approved Narrative Axis"):
        approved.revise("Outro eixo")


def test_editorial_project_requires_approved_axis_and_compact_subjects() -> None:
    project_id = uuid4()
    axis = _approved_axis(project_id)
    project = EditorialProject(
        project_id=project_id,
        brief=axis.brief,
        narrative_axis=axis,
        working_title="Quando a infância encontra a perda",
        subjects=[
            EditorialSubject(
                title="A criança diante da realidade da morte",
                development="Como ela compreende a perda e por que suas reações não devem ser julgadas pelo modelo adulto.",
                estimated_minutes=3,
            ),
            EditorialSubject(
                title="O adulto que cuida também está de luto",
                development="Como oferecer segurança e honestidade quando o próprio cuidador está abalado.",
                estimated_minutes=5,
            ),
        ],
    )

    approved = project.approve()

    assert approved.status is EditorialProjectStatus.APPROVED
    assert approved.narrative_axis.statement == axis.statement
    assert not hasattr(approved.subjects[0], "research_questions")
    assert not hasattr(approved.subjects[0], "purpose")


def test_editorial_project_rejects_unapproved_axis() -> None:
    project_id = uuid4()
    axis = NarrativeAxis(
        project_id=project_id,
        brief=_brief(),
        statement="Eixo ainda não aprovado.",
    )
    with pytest.raises(ValidationError, match="approved Narrative Axis"):
        EditorialProject(
            project_id=project_id,
            brief=axis.brief,
            narrative_axis=axis,
            working_title="Título",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    development="Desenvolvimento.",
                    estimated_minutes=8,
                )
            ],
        )


def test_editorial_project_rejects_unbounded_duration() -> None:
    project_id = uuid4()
    axis = _approved_axis(project_id)
    with pytest.raises(ValidationError, match="duration allocation"):
        EditorialProject(
            project_id=project_id,
            brief=axis.brief,
            narrative_axis=axis,
            working_title="Título",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    development="Desenvolvimento.",
                    estimated_minutes=2,
                )
            ],
        )
