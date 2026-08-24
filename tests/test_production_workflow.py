import asyncio
from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialReview,
    EditorialSubject,
    NarrativeAxis,
    NarrativeBeat,
    NarrativePlan,
    NormalizedSource,
    SubjectResearch,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.production_workflow import EditorialProductionWorkflow, run_editorial_production


class Researcher:
    async def research_all(self, *, project, graph):
        return [SubjectResearch(subject_id=project.subjects[0].subject_id, synthesis="Pesquisa interna.")]


class Architect:
    async def plan(self, *, project, research):
        return NarrativePlan(
            title=project.working_title,
            opening_move="Abrir pela tensão central.",
            central_thesis=project.narrative_axis.statement,
            beats=[NarrativeBeat(subject_id=project.subjects[0].subject_id, narrative_role="Desenvolver.", transition="Seguir.")],
            closing_move="Fechar retomando o eixo.",
        )


class Writer:
    async def draft(self, *, project, research, narrative_plan):
        return "Uma criança pergunta o que os adultos evitam responder."


class Critic:
    def __init__(self):
        self.calls = 0

    async def review(self, *, project, draft):
        self.calls += 1
        if self.calls == 1:
            return EditorialReview(
                ready_to_publish=False,
                quality_score=68,
                problems=["Desenvolvimento insuficiente"],
                revision_instructions=["Aprofundar sem desviar do eixo"],
            )
        return EditorialReview(ready_to_publish=True, quality_score=92)


class Editor:
    async def revise(self, *, project, narrative_plan, draft, review):
        return draft + " O adulto também precisa ser reconhecido como alguém que sofre."


async def test_approved_project_reaches_final_article_after_quality_revision() -> None:
    graph = await asyncio.to_thread(
        ProjectGraph.build,
        [NormalizedSource(source_id=uuid4(), text="A comunicação honesta ajuda a criança a viver o luto.", language="pt")],
        llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=8),
    )
    project_id = uuid4()
    brief = EditorialBrief(theme="O vivenciar do luto na infância", approach="Texto sensível para familiares", duration_minutes=8)
    axis = NarrativeAxis(
        project_id=project_id,
        brief=brief,
        statement="Acolher exige verdade sem esquecer que o adulto também está de luto.",
    ).approve()
    project = EditorialProject(
        project_id=project_id,
        brief=brief,
        narrative_axis=axis,
        working_title="Quando a infância encontra a perda",
        subjects=[EditorialSubject(title="A comunicação com a criança", development="Explorar verdade, segurança e sofrimento adulto.", estimated_minutes=8)],
    ).approve()
    workflow = EditorialProductionWorkflow(
        research_llm=MockLLM(max_tokens=64),
        architecture_llm=MockLLM(max_tokens=64),
        writing_llm=MockLLM(max_tokens=64),
        critic_llm=MockLLM(max_tokens=64),
        editing_llm=MockLLM(max_tokens=64),
    )
    critic = Critic()
    workflow._researcher = Researcher()
    workflow._architect = Architect()
    workflow._writer = Writer()
    workflow._critic = critic
    workflow._editor = Editor()

    article = await run_editorial_production(workflow, project=project, graph=graph)

    assert critic.calls == 2
    assert article.quality_score == 92
    assert article.revision_rounds == 1
    assert "adulto" in article.body_markdown
