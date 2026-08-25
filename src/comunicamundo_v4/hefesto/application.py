"""Application service that joins workflows to the durable approval boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from .contracts import (
    EditorialBrief,
    EditorialProjectStatus,
    NarrativeAxisStatus,
    NormalizedSource,
)
from .persistence import (
    GcpEditorialPersistence,
    PublishedArticle,
    StoredEditorialProject,
    StoredNarrativeAxis,
)
from .production_workflow import run_editorial_production
from .runtime import HefestoRuntime
from .workflow import run_narrative_axis_planning


class HefestoApplication:
    """Runs either side of approval; it never waits for human interaction."""

    def __init__(
        self,
        *,
        runtime: HefestoRuntime,
        persistence: GcpEditorialPersistence,
    ) -> None:
        self._runtime = runtime
        self._persistence = persistence

    async def plan_narrative_axis(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        sources: list[NormalizedSource],
    ) -> StoredNarrativeAxis:
        with TemporaryDirectory(prefix="hefesto-v4-plan-") as temporary:
            graph_directory = Path(temporary) / "graph"
            outcome = await run_narrative_axis_planning(
                self._runtime.planning_workflow,
                project_id=project_id,
                brief=brief,
                sources=sources,
                graph_storage_path=graph_directory,
            )
            return await asyncio.to_thread(
                self._persistence.persist_axis_planning_result,
                outcome.narrative_axis,
                outcome.brief,
                outcome.graph_context,
                graph_directory,
            )

    async def revise_narrative_axis(
        self,
        narrative_axis_id: UUID,
        feedback: str,
    ) -> StoredNarrativeAxis:
        stored = await asyncio.to_thread(
            self._persistence.get_narrative_axis,
            narrative_axis_id,
        )
        if stored is None:
            raise KeyError(f"narrative axis not found: {narrative_axis_id}")
        if stored.narrative_axis.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("approved Narrative Axis is immutable")
        if stored.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot revise a superseded Narrative Axis")

        revised = await self._runtime.planning_workflow._axis_planner.revise(
            current=stored.narrative_axis,
            brief=stored.brief,
            graph_context=stored.graph_context,
            feedback=feedback,
        )
        await asyncio.to_thread(
            self._persistence.save_narrative_axis,
            stored.model_copy(
                update={"narrative_axis": stored.narrative_axis.supersede()}
            ),
        )
        return await asyncio.to_thread(
            self._persistence.save_narrative_axis,
            stored.model_copy(
                update={
                    "narrative_axis": revised,
                    "editorial_project_id": None,
                }
            ),
        )

    async def approve_narrative_axis(
        self,
        narrative_axis_id: UUID,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(
            self._persistence.get_narrative_axis,
            narrative_axis_id,
        )
        if stored is None:
            raise KeyError(f"narrative axis not found: {narrative_axis_id}")
        if stored.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        if stored.narrative_axis.status is NarrativeAxisStatus.APPROVED:
            if stored.editorial_project_id is not None:
                existing = await asyncio.to_thread(
                    self._persistence.get_editorial_project,
                    stored.editorial_project_id,
                )
                if existing is not None:
                    return existing
            approved = stored.narrative_axis
        else:
            approved = stored.narrative_axis.approve()
            stored = await asyncio.to_thread(
                self._persistence.save_narrative_axis,
                stored.model_copy(update={"narrative_axis": approved}),
            )

        project = await self._runtime.planning_workflow._planner.build_project(
            project_id=approved.project_id,
            brief=stored.brief,
            graph_context=stored.graph_context,
            narrative_axis=approved,
        )
        return await asyncio.to_thread(
            self._persistence.persist_editorial_project_from_axis,
            project,
            stored.model_copy(update={"narrative_axis": approved}),
        )

    async def approve_editorial_project(
        self,
        editorial_project_id: UUID,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(
            self._persistence.get_editorial_project,
            editorial_project_id,
        )
        if stored is None:
            raise KeyError(f"editorial project not found: {editorial_project_id}")
        if stored.editorial_project.status is EditorialProjectStatus.APPROVED:
            return stored
        approved = stored.editorial_project.approve()
        return await asyncio.to_thread(
            self._persistence.save_editorial_project,
            approved,
        )

    async def produce_article(self, editorial_project_id: UUID) -> PublishedArticle:
        stored = await asyncio.to_thread(
            self._persistence.get_editorial_project,
            editorial_project_id,
        )
        if stored is None:
            raise KeyError(f"editorial project not found: {editorial_project_id}")
        if stored.editorial_project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production requires an approved Editorial Project")

        with TemporaryDirectory(prefix="hefesto-v4-produce-") as temporary:
            graph_directory = Path(temporary) / "graph"
            await asyncio.to_thread(
                self._persistence.restore_graph,
                stored,
                graph_directory,
            )
            graph = await asyncio.to_thread(
                self._runtime.load_graph,
                graph_directory,
            )
            article = await run_editorial_production(
                self._runtime.production_workflow,
                project=stored.editorial_project,
                graph=graph,
            )
            return await asyncio.to_thread(
                self._persistence.publish_article,
                article,
            )
