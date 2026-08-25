from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
)
from comunicamundo_v4.hefesto.production import SubjectResearcher


class PlainTextResearchLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def apredict(self, prompt, **prompt_args):
        self.calls += 1
        return (
            "## Síntese\n\nA comunicação honesta ajuda a criança a elaborar a perda.\n\n"
            "## Tensão editorial\n\nO impulso de proteger pode produzir silêncio."
        )

    async def astructured_predict(self, *args, **kwargs):
        raise AssertionError("subject research must not request structured JSON")


class Graph:
    async def context_for_subject(self, subject, brief):
        return "A criança precisa de informação clara e apoio para elaborar a perda."


async def test_subject_research_is_internal_prose_not_structured_json() -> None:
    subject = EditorialSubject(
        title="A verdade como cuidado",
        purpose="Explorar a comunicação honesta.",
        research_questions=["Como conversar sobre a morte?"],
        estimated_minutes=8,
    )
    project = EditorialProject(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="O luto na infância",
            approach="Texto sensível",
            duration_minutes=8,
        ),
        narrative_axis_id=uuid4(),
        narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender para acolher.",
        subjects=[subject],
    ).approve()
    llm = PlainTextResearchLlm()

    research = await SubjectResearcher(llm).research_all(
        project=project,
        graph=Graph(),
    )

    assert llm.calls == 1
    assert research[0].subject_id == subject.subject_id
    assert research[0].synthesis.startswith("## Síntese")
    assert research[0].promising_angles == []
