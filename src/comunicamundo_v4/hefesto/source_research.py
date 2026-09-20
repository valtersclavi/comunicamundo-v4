from __future__ import annotations

import asyncio
from collections.abc import Sequence
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
        acquisition_concurrency: int = 2,
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
        subject_coverage: dict[str, list[int]] = {}
        queries = list(planned.value.queries)
        selection: SourceSelectionProposal | None = None

        for _ in range(self._max_search_rounds):
            active_queries = queries
            queries = []
            if not active_queries and not candidates:
                break

            for query in active_queries or [""]:
                if query:
                    found = await asyncio.to_thread(
                        self._discovery.discover,
                        SourceSearchPlan(
                            queries=[query],
                            editorial_coverage=planned.value.editorial_coverage,
                        ),
                    )
                    candidates = deduplicate_candidates([*candidates, *found])
                    if not candidates:
                        continue
                result = await self._judge_and_acquire(
                    brief,
                    project,
                    candidates,
                    acquired,
                    failed,
                    subject_coverage,
                )
                usages.append(result.usage)
                selection = result.value
                if acquired and _coverage_complete(
                    subject_coverage,
                    len(project.subjects),
                ):
                    return SourceResearchResult(
                        search_plan=planned.value,
                        candidates=tuple(candidates),
                        selection=selection,
                        acquisitions=tuple(acquired.values()),
                        usages=tuple(usages),
                    )

            if not candidates:
                raise ValueError("source search returned no candidates")

        acquired_count = len(acquired)
        if acquired and selection is not None:
            return SourceResearchResult(
                search_plan=planned.value,
                candidates=tuple(candidates),
                selection=selection,
                acquisitions=tuple(acquired.values()),
                usages=tuple(usages),
            )
        raise ValueError(
            "source editor did not acquire usable sources after "
            f"{self._max_search_rounds} rounds ({acquired_count} sources acquired)"
        )

    async def _judge_and_acquire(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
        candidates: Sequence[SourceCandidate],
        acquired: dict[str, AcquisitionPayload],
        failed: dict[str, AcquisitionPayload],
        subject_coverage: dict[str, list[int]],
    ) -> ModelResult[SourceSelectionProposal]:
        judged = await self._editor.select(
            brief,
            project,
            candidates,
            acquired_candidate_ids=tuple(acquired),
            failed_candidate_ids=tuple(failed),
            subject_coverage=subject_coverage,
        )
        by_id = {item.candidate_id: item for item in candidates}
        fresh = [
            by_id[item]
            for item in judged.value.selected_candidate_ids
            if item not in acquired and item not in failed
        ]
        if fresh:
            semaphore = asyncio.Semaphore(self._acquisition_concurrency)

            async def acquire(item: SourceCandidate) -> AcquisitionPayload:
                async with semaphore:
                    return await asyncio.to_thread(self._acquirer.acquire, item)

            for payload in await asyncio.gather(*(acquire(item) for item in fresh)):
                candidate_id = payload.record.candidate.candidate_id
                if payload.record.status == "acquired":
                    acquired[candidate_id] = payload
                    subject_coverage[candidate_id] = (
                        judged.value.subject_coverage[candidate_id]
                    )
                else:
                    failed[candidate_id] = payload
        complete = _coverage_complete(subject_coverage, len(project.subjects))
        return judged.model_copy(update={
            "value": SourceSelectionProposal(
                selected_candidate_ids=list(acquired),
                subject_coverage=dict(subject_coverage),
                coverage_complete=complete,
                follow_up_queries=[] if complete else [
                    "buscar mais candidatos para os assuntos ainda sem fonte"
                ],
            )
        })


def _coverage_complete(
    subject_coverage: dict[str, list[int]],
    subject_count: int,
) -> bool:
    if subject_count < 1:
        return False
    covered = {
        index
        for indexes in subject_coverage.values()
        for index in indexes
        if 0 <= index < subject_count
    }
    return all(index in covered for index in range(subject_count))
