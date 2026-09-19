from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .contracts import (
    AcquiredSource,
    CallUsage,
    EditorialBrief,
    ProvisionalEditorialProject,
    SourceCandidate,
    SourceSearchPlan,
    SourceSelectionProposal,
)
from .source_editor import EditorialSourceEditor
from .source_pipeline import AcquisitionPayload, SourceAcquirer, SourceDiscovery, deduplicate_candidates


@dataclass(frozen=True, slots=True)
class SourceResearchResult:
    search_plan: SourceSearchPlan
    candidates: tuple[SourceCandidate, ...]
    selection: SourceSelectionProposal
    acquisitions: tuple[AcquisitionPayload, ...]
    usages: tuple[CallUsage, ...]

    @property
    def acquired(self) -> tuple[AcquisitionPayload, ...]:
        return tuple(item for item in self.acquisitions if item.record.status == "acquired")

    @property
    def failures(self) -> tuple[AcquiredSource, ...]:
        return tuple(item.record for item in self.acquisitions if item.record.status != "acquired")


class SourceResearch:
    def __init__(
        self,
        editor: EditorialSourceEditor,
        discovery: SourceDiscovery,
        acquirer: SourceAcquirer,
        *,
        max_search_rounds: int = 4,
        acquisition_concurrency: int = 4,
    ) -> None:
        if max_search_rounds < 1 or acquisition_concurrency < 1:
            raise ValueError("source research limits must be positive")
        self._editor = editor
        self._discovery = discovery
        self._acquirer = acquirer
        self._max_search_rounds = max_search_rounds
        self._acquisition_concurrency = acquisition_concurrency

    async def run(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
    ) -> SourceResearchResult:
        planned = await self._editor.search_plan(brief, project)
        usages = [planned.usage]
        candidates: list[SourceCandidate] = []
        acquired: dict[str, AcquisitionPayload] = {}
        failed: dict[str, AcquisitionPayload] = {}
        queries = list(planned.value.queries)
        selection: SourceSelectionProposal | None = None

        for _ in range(self._max_search_rounds):
            if queries:
                found = await asyncio.to_thread(
                    self._discovery.discover,
                    SourceSearchPlan(
                        queries=queries,
                        editorial_coverage=planned.value.editorial_coverage,
                    ),
                )
                candidates = deduplicate_candidates([*candidates, *found])
            if not candidates:
                raise ValueError("source search returned no candidates")

            judged = await self._editor.select(
                brief,
                project,
                candidates,
                acquired_candidate_ids=tuple(acquired),
                failed_candidate_ids=tuple(failed),
            )
            usages.append(judged.usage)
            selection = judged.value
            by_id = {item.candidate_id: item for item in candidates}
            fresh = [
                by_id[item]
                for item in selection.selected_candidate_ids
                if item not in acquired and item not in failed
            ]
            if fresh:
                semaphore = asyncio.Semaphore(self._acquisition_concurrency)

                async def acquire(item: SourceCandidate) -> AcquisitionPayload:
                    async with semaphore:
                        return await asyncio.to_thread(self._acquirer.acquire, item)

                for payload in await asyncio.gather(*(acquire(item) for item in fresh)):
                    target = acquired if payload.record.status == "acquired" else failed
                    target[payload.record.candidate.candidate_id] = payload

            selected_is_available = bool(selection.selected_candidate_ids) and all(
                item in acquired for item in selection.selected_candidate_ids
            )
            if selection.coverage_complete and selected_is_available:
                return SourceResearchResult(
                    search_plan=planned.value,
                    candidates=tuple(candidates),
                    selection=selection,
                    acquisitions=tuple(acquired.values()),
                    usages=tuple(usages),
                )
            queries = list(selection.follow_up_queries)

        acquired_count = len(acquired)
        raise ValueError(
            "source editor did not reach adequate coverage after "
            f"{self._max_search_rounds} rounds ({acquired_count} sources acquired)"
        )
