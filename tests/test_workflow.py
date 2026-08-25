from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    NarrativeAxis,
    NarrativeAxisStatus,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.workflow import (
    EditorialPlanningWorkflow,
    run_narrative_axis_planning,
)


class DeterministicAxisPlanner:
    async def propose(self, *, project_id, brief, graph_context) -> NarrativeAxis:
        return NarrativeAxis(
            project_id=project_id,
            statement=(
                f"Como {brief.theme} pode ser narrado a partir da compreensão "
                "já recuperada do material."
            ),
        )


async def test_workflow_stops_at_proposed_narrative_axis(tmp_path) -> None:
    workflow = EditorialPlanningWorkflow(
        graph_llm=MockLLM(max_tokens=64),
        planning_llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=8),
    )
    workflow._axis_planner = DeterministicAxisPlanner()

    result = await run_narrative_axis_planning(
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

    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert result.brief.duration_minutes == 8
    assert "luto na infância" in result.narrative_axis.statement
    assert (tmp_path / "graph" / "property_graph_store.json").exists()
