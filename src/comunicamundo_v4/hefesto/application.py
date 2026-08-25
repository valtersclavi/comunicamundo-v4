"""Application service enforcing both human approval boundaries."""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from .contracts import EditorialBrief, EditorialProjectStatus, NarrativeAxisStatus, NormalizedSource
from .persistence import GcpEditorialPersistence, StoredEditorialProject, StoredNarrativeAxis
from .runtime import HefestoRuntime


class HefestoApplication:
    def __init__(self, *, runtime: HefestoRuntime, persistence: GcpEditorialPersistence) -> None:
        self._runtime = runtime
        self._persistence = persistence

    async def plan(self, *, project_id: UUID, brief: EditorialBrief, sources: list[NormalizedSource]) -> StoredNarrativeAxis:
        with TemporaryDirectory(prefix="hefesto-v4-rebuild-plan-") as temporary:
            graph_dir = Path(temporary) / "graph"
            graph = await asyncio.to_thread(self._runtime.build_graph, sources)
            graph_context = await graph.understand(brief)
            await asyncio.to_thread(graph.persist, graph_dir)
            axis = await self._runtime.director.propose_axis(project_id=project_id, brief=brief, graph_context=graph_context)
            return await asyncio.to_thread(
                self._persistence.persist_new_axis,
                axis=axis,
                brief=brief,
                graph_context=graph_context,
                graph_directory=graph_dir,
            )

    async def revise_axis(self, axis_id: UUID, feedback: str) -> StoredNarrativeAxis:
        stored = await asyncio.to_thread(self._persistence.get_axis, axis_id)
        if stored is None:
            raise KeyError(f"Narrative Axis not found: {axis_id}")
        if stored.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            raise ValueError("only a proposed Narrative Axis can be revised")
        revised = await self._runtime.director.revise_axis(
            current=stored.narrative_axis,
            brief=stored.brief,
            graph_context=stored.graph_context,
            feedback=feedback,
        )
        await asyncio.to_thread(self._persistence.save_axis, stored.model_copy(update={"narrative_axis": stored.narrative_axis.supersede()}))
        return await asyncio.to_thread(self._persistence.save_axis, stored.model_copy(update={"narrative_axis": revised, "editorial_project_id": None}))

    async def approve_axis(self, axis_id: UUID) -> StoredEditorialProject:
        stored = await asyncio.to_thread(self._persistence.get_axis, axis_id)
        if stored is None:
            raise KeyError(f"Narrative Axis not found: {axis_id}")
        if stored.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        approved = stored.narrative_axis.approve()
        stored = await asyncio.to_thread(self._persistence.save_axis, stored.model_copy(update={"narrative_axis": approved}))
        project = await self._runtime.director.build_project(axis=approved, brief=stored.brief, graph_context=stored.graph_context)
        saved = await asyncio.to_thread(self._persistence.save_project, StoredEditorialProject(
            editorial_project=project,
            graph_artifact=stored.graph_artifact,
            graph_context=stored.graph_context,
            updated_at=stored.updated_at,
        ))
        await asyncio.to_thread(self._persistence.save_axis, stored.model_copy(update={"editorial_project_id": project.editorial_project_id}))
        return saved

    async def revise_project(self, editorial_project_id: UUID, feedback: str) -> StoredEditorialProject:
        stored = await asyncio.to_thread(self._persistence.get_project, editorial_project_id)
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is not EditorialProjectStatus.PROPOSED:
            raise ValueError("only a proposed Editorial Project can be revised")
        revised = await self._runtime.director.revise_project(current=stored.editorial_project, graph_context=stored.graph_context, feedback=feedback)
        await asyncio.to_thread(self._persistence.save_project, stored.model_copy(update={"editorial_project": stored.editorial_project.supersede()}))
        return await asyncio.to_thread(self._persistence.save_project, stored.model_copy(update={"editorial_project": revised}))

    async def approve_project(self, editorial_project_id: UUID) -> StoredEditorialProject:
        stored = await asyncio.to_thread(self._persistence.get_project, editorial_project_id)
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is EditorialProjectStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Editorial Project")
        return await asyncio.to_thread(self._persistence.save_project, stored.model_copy(update={"editorial_project": stored.editorial_project.approve()}))

    async def produce(self, editorial_project_id: UUID):
        stored = await asyncio.to_thread(self._persistence.get_project, editorial_project_id)
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production requires an approved Editorial Project")
        with TemporaryDirectory(prefix="hefesto-v4-rebuild-produce-") as temporary:
            graph_dir = Path(temporary) / "graph"
            await asyncio.to_thread(self._persistence.restore_graph, stored.graph_artifact, graph_dir)
            graph = await asyncio.to_thread(self._runtime.load_graph, graph_dir)
            article = await self._runtime.production.run(project=stored.editorial_project, graph=graph)
            return await asyncio.to_thread(self._persistence.publish_article, article)
