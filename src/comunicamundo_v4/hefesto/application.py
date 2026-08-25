"""Application service joining Hefesto workflows to durable approval boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from .contracts import EditorialBrief, EditorialProjectStatus, NarrativeAxisStatus, NormalizedSource
from .persistence import (
    GcpEditorialPersistence,
    PublishedArticle,
    StoredEditorialProject,
    StoredNarrativeAxis,
)
from .production_workflow import run_editorial_production
from .runtime import HefestoRuntime
from .workflow import run_editorial_planning


class HefestoApplication:
    """Runs one durable editorial stage per worker invocation."""

    def __init__(
        self,
        *,
        runtime: HefestoRuntime,
        persistence: GcpEditorialPersistence,
    ) -> None:
        self._runtime = runtime
        self._persistence = persistence

    async def plan_project(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        sources: list[NormalizedSource],
    ) -> StoredNarrativeAxis:
        """Build the graph and stop at Narrative Axis approval."""
        with TemporaryDirectory(prefix="hefesto-v4-plan-") as temporary:
            graph_directory = Path(temporary) / "graph"
            axis = await run_editorial_planning(
                self._runtime.planning_workflow,
                project_id=project_id,
                brief=brief,
                sources=sources,
                graph_storage_path=graph_directory,
            )
            return await asyncio.to_thread(
                self._persistence.persist_planning_result,
                axis,
                graph_directory,
            )

    async def revise_narrative_axis(
        self,
        narrative_axis_id: UUID,
        feedback: str,
    ) -> StoredNarrativeAxis:
        stored = await asyncio.to_thread(
            self._persistence.get_narrative_axis, narrative_axis_id
        )
        if stored is None:
            raise KeyError(f"Narrative Axis not found: {narrative_axis_id}")
        if stored.narrative_axis.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("an approved Narrative Axis cannot be revised")
        if stored.editorial_project_id is not None:
            raise ValueError("Narrative Axis already has an Editorial Project")

        with TemporaryDirectory(prefix="hefesto-v4-axis-revise-") as temporary:
            graph_directory = Path(temporary) / "graph"
            await asyncio.to_thread(
                self._persistence.restore_graph, stored, graph_directory
            )
            graph = await asyncio.to_thread(self._runtime.load_graph, graph_directory)
            graph_context = await graph.context_for_editorial_project(
                stored.narrative_axis.brief
            )
            revised = await self._runtime.axis_planner.revise(
                axis=stored.narrative_axis,
                feedback=feedback,
                graph_context=graph_context,
            )
            return await asyncio.to_thread(
                self._persistence.save_narrative_axis, revised
            )

    async def approve_narrative_axis(
        self,
        narrative_axis_id: UUID,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(
            self._persistence.get_narrative_axis, narrative_axis_id
        )
        if stored is None:
            raise KeyError(f"Narrative Axis not found: {narrative_axis_id}")
        if stored.editorial_project_id is not None:
            existing = await asyncio.to_thread(
                self._persistence.get_editorial_project,
                stored.editorial_project_id,
            )
            if existing is None:
                raise ValueError("Narrative Axis points to a missing Editorial Project")
            return existing

        approved_axis = stored.narrative_axis.approve()
        stored = await asyncio.to_thread(
            self._persistence.save_narrative_axis, approved_axis
        )

        with TemporaryDirectory(prefix="hefesto-v4-project-") as temporary:
            graph_directory = Path(temporary) / "graph"
            await asyncio.to_thread(
                self._persistence.restore_graph, stored, graph_directory
            )
            graph = await asyncio.to_thread(self._runtime.load_graph, graph_directory)
            graph_context = await graph.context_for_editorial_project(approved_axis.brief)
            project = await self._runtime.editorial_planner.build_project(
                axis=approved_axis,
                graph_context=graph_context,
            )
            return await asyncio.to_thread(
                self._persistence.persist_editorial_project_for_axis,
                project,
                stored,
            )

    async def revise_editorial_project(
        self,
        editorial_project_id: UUID,
        feedback: str,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(
            self._persistence.get_editorial_project, editorial_project_id
        )
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is EditorialProjectStatus.APPROVED:
            raise ValueError("an approved Editorial Project cannot be revised")

        with TemporaryDirectory(prefix="hefesto-v4-project-revise-") as temporary:
            graph_directory = Path(temporary) / "graph"
            await asyncio.to_thread(
                self._persistence.restore_graph, stored, graph_directory
            )
            graph = await asyncio.to_thread(self._runtime.load_graph, graph_directory)
            graph_context = await graph.context_for_editorial_project(
                stored.editorial_project.brief
            )
            revised = await self._runtime.editorial_planner.revise_project(
                project=stored.editorial_project,
                feedback=feedback,
                graph_context=graph_context,
            )
            return await asyncio.to_thread(
                self._persistence.save_editorial_project, revised
            )

    async def approve_editorial_project(
        self,
        editorial_project_id: UUID,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(
            self._persistence.get_editorial_project, editorial_project_id
        )
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is EditorialProjectStatus.APPROVED:
            return stored
        approved = stored.editorial_project.approve()
        return await asyncio.to_thread(
            self._persistence.save_editorial_project, approved
        )

    async def produce_article(self, editorial_project_id: UUID) -> PublishedArticle:
        stored = await asyncio.to_thread(
            self._persistence.get_editorial_project, editorial_project_id
        )
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production requires an approved Editorial Project")

        with TemporaryDirectory(prefix="hefesto-v4-produce-") as temporary:
            graph_directory = Path(temporary) / "graph"
            await asyncio.to_thread(
                self._persistence.restore_graph, stored, graph_directory
            )
            graph = await asyncio.to_thread(self._runtime.load_graph, graph_directory)
            article = await run_editorial_production(
                self._runtime.production_workflow,
                project=stored.editorial_project,
                graph=graph,
            )
            return await asyncio.to_thread(
                self._persistence.publish_article, article
            )
