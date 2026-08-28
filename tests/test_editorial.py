from uuid import uuid4
from comunicamundo_v4.hefesto.contracts import EditorialBrief, EditorialProjectProposal, EditorialSubjectProposal, NarrativeAxisProposal
from comunicamundo_v4.hefesto.editorial import EditorialDirector


class StructuredLlm:
    def __init__(self, responses):
        self.responses = list(responses)
    async def astructured_predict(self, model, prompt, **kwargs):
        value = self.responses.pop(0)
        assert isinstance(value, model)
        return value


async def test_axis_precedes_project() -> None:
    director = EditorialDirector(
        axis_llm=StructuredLlm([NarrativeAxisProposal(statement="A criança e o adulto atravessam a perda em relação, e a honestidade adequada à idade pode transformar proteção em presença compartilhada.")]),
        project_llm=StructuredLlm([EditorialProjectProposal(
            working_title="Quando a família aprende a perder junta",
            editorial_promise="Explicar e tornar praticável.",
            core_subjects=[EditorialSubjectProposal(title="A experiência infantil", development="Como a criança compreende e expressa a perda e como conversar sem esconder nem sobrecarregar.")],
            llm_subjects=["Diferenças por faixa etária"],
            enrichment_opportunities=["Dados longitudinais sobre luto infantil"],
        )]),
    )
    brief = EditorialBrief(theme="Luto infantil", approach="sensível e prático", duration_minutes=8, audience="pais e cuidadores")
    project_id = uuid4()
    axis = await director.propose_axis(project_id=project_id, brief=brief, graph_context="Compreensão profunda das fontes")
    assert axis.status.value == "proposed"
    project = await director.build_project(axis=axis.approve(), brief=brief, graph_context="Compreensão profunda das fontes")
    assert project.status.value == "proposed"
    assert project.narrative_axis == axis.statement
