from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
    NarrativeAxis,
)
from comunicamundo_v4.hefesto.production import SubjectResearcher


class PlainTextResearchLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.last_args = None

    async def apredict(self, prompt, **prompt_args):
        self.calls += 1
        self.last_args = prompt_args
        return (
            "## Síntese\n\nA comunicação honesta ajuda a criança a elaborar a perda.\n\n"
            "## Tensão editorial\n\nO impulso de proteger pode produzir silêncio."
        )

    async def astructured_predict(self, *args, **kwargs):
        raise AssertionError("subject research must not request structured JSON")


class Graph:
    async def context_for_subject(self, subject, brief, narrative_axis):
        assert "verdade" in narrative_axis.statement
        return "A criança precisa de informação clara e apoio para elaborar a perda."


async def test_subject_research_uses_axis_and_compact_subject_without_visible_questions() -> None:
    project_id = uuid4()
    brief = EditorialBrief(theme="O luto na infância", approach="Texto sensível", duration_minutes=8)
    axis = NarrativeAxis(
        project_id=project_id,
        brief=brief,
        statement="A verdade pode ser uma forma de cuidado quando encontra segurança e presença.",
    ).approve()
    subject = EditorialSubject(
        title="A verdade como cuidado",
        development="Transformar a compreensão da morte em comunicação concreta e acolhedora.",
        estimated_minutes=8,
    )
    project = EditorialProject(
        project_id=project_id,
        brief=brief,
        narrative_axis=axis,
        working_title="Quando a infância encontra a perda",
        subjects=[subject],
    ).approve()
    llm = PlainTextResearchLlm()

    research = await SubjectResearcher(llm).research_all(project=project, graph=Graph())

    assert llm.calls == 1
    assert research[0].subject_id == subject.subject_id
    assert research[0].synthesis.startswith("## Síntese")
    assert llm.last_args["narrative_axis"] == axis.statement
    assert llm.last_args["subject_development"] == subject.development
