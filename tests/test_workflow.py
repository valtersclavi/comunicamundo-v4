from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectStatus,
    EditorialSubject,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.workflow import (
    EditorialPlanningWorkflow,
    run_editorial_planning,
)


class DeterministicPlanner:
    async def build_project(
        self,
        *,
        project_id,
        brief,
        graph_context,
    ) -> EditorialProject:
        return EditorialProject(
            project_id=project_id,
            brief=brief,
            narrative_axis_id=uuid4(),
            narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
            working_title="Quando a infância encontra a perda",
            editorial_promise="Compreender a experiência infantil sem reduzi-la.",
            subjects=[
                EditorialSubject(
                    title="A compreensão infantil da morte",
                    purpose="Apresentar a perspectiva da criança.",
                    research_questions=["Como essa compreensão se manifesta?"],
                    estimated_minutes=brief.duration_minutes,
                )
            ],
        )


async def test_workflow_stops_at_editorial_approval_boundary(tmp_path) -> None:
    workflow = EditorialPlanningWorkflow(
        graph_llm=MockLLM(max_tokens=64),
        planning_llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=8),
    )
    workflow._planner = DeterministicPlanner()

    result = await run_editorial_planning(
        workflow,
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="O vivenciar do luto na infância",
            approach="Texto sensível para familiares",
            duration_minutes=8,
        ),
        sources=[
            NormalizedSource(
                source_id=uuid4(),
                text="A comunicação honesta ajuda a criança a viver o processo de luto.",
                language="pt",
            )
        ],
        graph_storage_path=tmp_path / "graph",
    )

    assert result.status is EditorialProjectStatus.PROPOSED
    assert result.approved_at is None
    assert result.brief.duration_minutes == 8
    assert len(result.subjects) == 1
    assert (tmp_path / "graph" / "property_graph_store.json").exists()
