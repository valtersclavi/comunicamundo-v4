"""Application service enforcing both human approval boundaries."""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Sequence
from uuid import UUID

from .contracts import EditorialProjectStatus, NarrativeAxisStatus, PlanningRunState
from .persistence import GcpEditorialPersistence, StoredEditorialProject, StoredNarrativeAxis

if TYPE_CHECKING:
    from .runtime import HefestoRuntime


class HefestoApplication:
    def __init__(self, *, runtime: HefestoRuntime, persistence: GcpEditorialPersistence) -> None:
        self._runtime = runtime
        self._persistence = persistence

    async def revise_axis(self, axis_id: UUID, feedback: str) -> StoredNarrativeAxis:
        raise ValueError("proposed Axis feedback must go through review-axis-feedback")

    async def approve_axis(self, axis_id: UUID) -> StoredEditorialProject:
        stored = await asyncio.to_thread(self._persistence.get_axis, axis_id)
        if stored is None:
            raise KeyError(f"Narrative Axis not found: {axis_id}")
        if stored.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        await asyncio.to_thread(self._reject_editorial_project_if_feedback_unconsumed, stored)
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

    async def incorporate_hefesto_tips(
        self,
        editorial_project_id: UUID,
        selected_tips: Sequence[str] | None = None,
    ) -> StoredEditorialProject:
        stored = await asyncio.to_thread(self._persistence.get_project, editorial_project_id)
        if stored is None:
            raise KeyError(f"Editorial Project not found: {editorial_project_id}")
        if stored.editorial_project.status is not EditorialProjectStatus.PROPOSED:
            raise ValueError("only a proposed Editorial Project can incorporate Dicas do Hefesto")
        selected = list(stored.editorial_project.llm_subjects if selected_tips is None else selected_tips)
        if not selected:
            raise ValueError("Editorial Project has no selected Dicas do Hefesto")
        revised = await self._runtime.director.incorporate_hefesto_tips(
            current=stored.editorial_project,
            graph_context=stored.graph_context,
            selected_tips=selected,
        )
        return await asyncio.to_thread(self._persist_tip_revision, stored, revised)

    def _persist_tip_revision(self, stored: StoredEditorialProject, revised) -> StoredEditorialProject:
        """Persist a tip-expanded Project and keep its PlanningRun pointer canonical.

        The clean PlanningRun flow persists both a Firestore operational project and a
        durable planning artifact. Tip incorporation must advance both, otherwise the
        run would continue pointing at the superseded Project.
        """
        run = None
        persisted = None
        if stored.planning_run_id is not None:
            run = self._persistence.get_run(stored.planning_run_id)
            if run is None:
                raise ValueError("Editorial Project points to a missing PlanningRun")
            if run.state is not PlanningRunState.PROJECT_PROPOSED:
                raise ValueError("Dicas do Hefesto require a PlanningRun stopped at project_proposed")
            if run.artifacts.editorial_project_id != stored.editorial_project.editorial_project_id:
                raise ValueError("PlanningRun does not point to the Editorial Project being revised")
            if run.artifacts.editorial_project_uri is None:
                raise ValueError("PlanningRun is missing the persisted Editorial Project artifact")
            persisted = self._persistence.load_editorial_project(run.artifacts.editorial_project_uri)
            if persisted.editorial_project.editorial_project_id != stored.editorial_project.editorial_project_id:
                raise ValueError("persisted Editorial Project does not match the PlanningRun")
            if persisted.planning_run_id != run.planning_run_id:
                raise ValueError("persisted Editorial Project belongs to another PlanningRun")

        # Write the new operational project first. Until the run pointer advances, the
        # old project remains the canonical proposed project and a partial write is recoverable.
        saved = self._persistence.save_project(stored.model_copy(update={"editorial_project": revised}))

        if run is not None and persisted is not None:
            revised_record = persisted.model_copy(update={"editorial_project": revised})
            project_uri = self._persistence.artifacts.persist_planning_json(
                project_id=run.project_id,
                planning_run_id=run.planning_run_id,
                relative_path=f"editorial-projects/{revised.editorial_project_id}.json",
                payload=revised_record.model_dump(mode="json"),
            )
            self._persistence.save_run(
                run.model_copy(
                    update={
                        "artifacts": run.artifacts.model_copy(
                            update={
                                "editorial_project_id": revised.editorial_project_id,
                                "editorial_project_uri": project_uri,
                            }
                        ),
                        "error": None,
                    }
                )
            )

        # The predecessor remains addressable by id but can no longer be approved.
        self._persistence.save_project(
            stored.model_copy(update={"editorial_project": stored.editorial_project.supersede()})
        )
        return saved

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

    def _reject_editorial_project_if_feedback_unconsumed(self, stored: StoredNarrativeAxis) -> None:
        if stored.planning_run_id is None:
            return
        run = self._persistence.get_run(stored.planning_run_id)
        if run is None:
            return
        if run.artifacts.axis_feedback_decision_uri is None:
            return
        raise ValueError(
            "cannot create an Editorial Project while a durable Axis feedback decision exists; "
            "the next delivery must consume feedback history and carry-forward"
        )
