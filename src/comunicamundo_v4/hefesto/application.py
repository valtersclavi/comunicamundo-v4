"""Application service that joins workflows to the durable approval boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from .contracts import (
    EditorialBrief,
    EditorialProjectStatus,
    NormalizedSource,
)
from .persistence import (
    GcpEditorialPersistence,
    PublishedArticle,
    StoredEditorialProject,
)
from .production_workflow import run_editorial_production
from .runtime import HefestoRuntime
from .workflow import run_editorial_planning


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

    async def plan_project(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        sources: list[NormalizedSource],
    ) -> StoredEditorialProject:
        with TemporaryDirectory(prefix="hefesto-v4-plan-") as temporary:
            graph_directory = Path(temporary) / "graph"
            project = await run_editorial_planning(
                self._runtime.planning_workflow,
                project_id=project_id,
                brief=brief,
                sources=sources,
                graph_storage_path=graph_directory,
            )
            return await asyncio.to_thread(
                self._persistence.persist_planning_result,
                project,
                graph_directory,
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
