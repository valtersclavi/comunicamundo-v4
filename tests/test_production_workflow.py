import asyncio
from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialReview,
    EditorialSubject,
    NarrativeBeat,
    NarrativePlan,
    NormalizedSource,
    SubjectResearch,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.production_workflow import (
    EditorialProductionWorkflow,
    run_editorial_production,
)


class Researcher:
    async def research_all(self, *, project, graph):
        return [
            SubjectResearch(
                subject_id=project.subjects[0].subject_id,
                synthesis="A comunicação adulta participa da elaboração do luto.",
                promising_angles=["Honestidade como forma de cuidado"],
                tensions=["Proteger também pode significar silenciar"],
                concrete_material=["A criança formula perguntas diretas"],
            )
        ]


class Architect:
    async def plan(self, *, project, research):
        return NarrativePlan(
            title=project.working_title,
            opening_move="Começar pela pergunta inesperada de uma criança.",
            central_thesis="Acolher exige verdade compreensível.",
            beats=[
                NarrativeBeat(
                    subject_id=project.subjects[0].subject_id,
                    narrative_role="Desenvolver a tensão central.",
                    transition="Da pergunta infantil para a resposta adulta.",
                )
            ],
            closing_move="Retornar à pergunta com uma resposta possível.",
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
                strengths=["Abertura clara"],
                problems=["Desenvolvimento insuficiente"],
                revision_instructions=["Aprofundar a tensão sem perder concisão"],
            )
        return EditorialReview(
            ready_to_publish=True,
            quality_score=92,
            strengths=["Texto coeso e sensível"],
        )


class Editor:
    async def revise(self, *, project, narrative_plan, draft, review):
        return (
            "Uma criança pergunta o que os adultos evitam responder. "
            "A resposta cuidadosa começa quando a verdade encontra uma linguagem possível."
        )


async def test_approved_project_reaches_final_article_after_quality_revision() -> None:
    graph = await asyncio.to_thread(
        ProjectGraph.build,
        [
            NormalizedSource(
                source_id=uuid4(),
                text="A comunicação honesta ajuda a criança a viver o luto.",
                language="pt",
            )
        ],
        llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=8),
    )
    subject = EditorialSubject(
        title="A comunicação com a criança",
        purpose="Explorar a relação entre verdade e cuidado.",
        research_questions=["Como conversar sobre a morte?"],
        estimated_minutes=8,
    )
    project = EditorialProject(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="O vivenciar do luto na infância",
            approach="Texto sensível para familiares",
            duration_minutes=8,
        ),
        narrative_axis_id=uuid4(),
        narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender a experiência infantil do luto.",
        subjects=[subject],
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

    article = await run_editorial_production(
        workflow,
        project=project,
        graph=graph,
    )

    assert critic.calls == 2
    assert article.quality_score == 92
    assert article.revision_rounds == 1
    assert "linguagem possível" in article.body_markdown
    assert article.word_count > 10
