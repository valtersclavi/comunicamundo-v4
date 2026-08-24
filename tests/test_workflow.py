from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    NarrativeAxis,
    NarrativeAxisStatus,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.workflow import EditorialPlanningWorkflow, run_editorial_planning


class DeterministicAxisPlanner:
    async def propose(self, *, project_id, brief, graph_context) -> NarrativeAxis:
        return NarrativeAxis(
            project_id=project_id,
            brief=brief,
            statement="Acolher o luto infantil exige verdade e segurança sem apagar o luto do adulto.",
        )


async def test_workflow_stops_at_narrative_axis_approval_boundary(tmp_path) -> None:
    workflow = EditorialPlanningWorkflow(
        graph_llm=MockLLM(max_tokens=64),
        axis_planner=DeterministicAxisPlanner(),
        embed_model=MockEmbedding(embed_dim=8),
    )

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

    assert result.status is NarrativeAxisStatus.PROPOSED
    assert result.approved_at is None
    assert "adulto" in result.statement
    assert (tmp_path / "graph" / "property_graph_store.json").exists()
