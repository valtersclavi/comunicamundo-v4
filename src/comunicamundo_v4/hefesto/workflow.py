"""Sources to the first durable Narrative Axis approval boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import LLM
from llama_index.core.workflow import Event, StartEvent, StopEvent, Workflow, step

from .contracts import EditorialBrief, NarrativeAxis, NormalizedSource
from .graph import ProjectGraph
from .planner import NarrativeAxisPlanner


class BuildNarrativeAxisRequest(StartEvent):
    project_id: UUID
    brief: EditorialBrief
    sources: list[NormalizedSource]
    graph_storage_path: Path


class GraphBuilt(Event):
    project_id: UUID
    brief: EditorialBrief
    graph: ProjectGraph


class EditorialPlanningWorkflow(Workflow):
    """Build the graph, propose the Narrative Axis, and stop for approval."""

    def __init__(
        self,
        *,
        graph_llm: LLM,
        axis_planner: NarrativeAxisPlanner,
        embed_model: BaseEmbedding,
        timeout: float | None = 1800,
    ) -> None:
        super().__init__(timeout=timeout)
        self._graph_llm = graph_llm
        self._embed_model = embed_model
        self._axis_planner = axis_planner

    @step
    async def build_graph(self, event: BuildNarrativeAxisRequest) -> GraphBuilt:
        # PropertyGraphIndex performs its own bounded async extraction. Running
        # construction in a worker thread prevents its event loop from nesting
        # inside the Workflow event loop and keeps the workflow responsive.
        graph = await asyncio.to_thread(
            ProjectGraph.build,
            event.sources,
            llm=self._graph_llm,
            embed_model=self._embed_model,
        )
        await asyncio.to_thread(graph.persist, event.graph_storage_path)
        return GraphBuilt(
            project_id=event.project_id,
            brief=event.brief,
            graph=graph,
        )

    @step
    async def build_narrative_axis(self, event: GraphBuilt) -> StopEvent:
        graph_context = await event.graph.context_for_editorial_project(event.brief)
        axis = await self._axis_planner.propose(
            project_id=event.project_id,
            brief=event.brief,
            graph_context=graph_context,
        )
        return StopEvent(result=axis)


async def run_editorial_planning(
    workflow: EditorialPlanningWorkflow,
    *,
    project_id: UUID,
    brief: EditorialBrief,
    sources: list[NormalizedSource],
    graph_storage_path: Path,
) -> NarrativeAxis:
    result = await workflow.run(
        start_event=BuildNarrativeAxisRequest(
            project_id=project_id,
            brief=brief,
            sources=sources,
            graph_storage_path=graph_storage_path,
        )
    )
    if not isinstance(result, NarrativeAxis):
        raise TypeError(f"expected NarrativeAxis, received {type(result).__name__}")
    return result
