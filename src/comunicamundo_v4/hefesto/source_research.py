from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .contracts import (
    CallUsage,
    EditorialBrief,
    ProvisionalEditorialProject,
    SourceCandidate,
    SourceSearchPlan,
    SubjectSearchQuery,
    SourceSelectionProposal,
)
from .source_editor import EditorialSourceEditor
from .source_pipeline import AcquisitionPayload, SourceAcquirer, SourceDiscovery


@dataclass(frozen=True, slots=True)
class SourceResearchResult:
    search_plan: SourceSearchPlan
    candidates: tuple[SourceCandidate, ...]
    selection: SourceSelectionProposal
    acquisitions: tuple[AcquisitionPayload, ...]
    usages: tuple[CallUsage, ...]
    attempts_by_subject: dict[int, int]
    failed_acquisition_count: int

    @property
    def acquired(self) -> tuple[AcquisitionPayload, ...]:
        return tuple(item for item in self.acquisitions if item.record.status == "acquired")

class SourceResearch:
    def __init__(
        self,
        editor: EditorialSourceEditor,
        discovery: SourceDiscovery,
        acquirer: SourceAcquirer,
        *,
        max_search_rounds: int = 4,
    ) -> None:
        if max_search_rounds < 1:
            raise ValueError("source research limits must be positive")
        self._editor = editor
        self._discovery = discovery
        self._acquirer = acquirer
        self._max_search_rounds = max_search_rounds

    async def run(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
    ) -> SourceResearchResult:
        planned = await self._editor.search_plan(brief, project)
        query_map = _query_map(
            planned.value,
            subject_count=len(project.subjects),
            attempts_per_subject=self._max_search_rounds,
        )
        usages = [planned.usage]
        candidates: list[SourceCandidate] = []
        candidate_ids: set[str] = set()
        judged_ids: set[str] = set()
        acquisitions: list[AcquisitionPayload] = []
        failed_acquisition_count = 0
        acquired_ids: list[str] = []
        subject_coverage: dict[str, list[int]] = {}
        attempts_by_subject = {index: 0 for index in range(len(project.subjects))}

        for attempt_index in range(self._max_search_rounds):
            for subject_index in range(len(project.subjects)):
                if subject_index in _covered_subjects(subject_coverage):
                    continue
                search_query = query_map[subject_index][attempt_index]
                attempts_by_subject[subject_index] += 1
                found = await asyncio.to_thread(
                    self._discovery.discover_query,
                    search_query.query,
                    search_query.provider,
                )
                fresh: list[SourceCandidate] = []
                for candidate in found:
                    if candidate.candidate_id not in candidate_ids:
                        candidate_ids.add(candidate.candidate_id)
                        candidates.append(candidate)
                    if candidate.candidate_id not in judged_ids:
                        fresh.append(candidate)

                for candidate in fresh:
                    judged_ids.add(candidate.candidate_id)
                    eligible = sorted(
                        set(range(len(project.subjects)))
                        - _covered_subjects(subject_coverage)
                    )
                    if not eligible:
                        break
                    judgment = await self._editor.judge_candidate(
                        brief,
                        project,
                        candidate,
                        eligible_subject_indexes=eligible,
                    )
                    usages.append(judgment.usage)
                    if judgment.value.decision == "skip":
                        continue
                    covered = sorted(
                        set(judgment.value.covered_subject_indexes) & set(eligible)
                    )
                    if not covered:
                        continue
                    payload = await asyncio.to_thread(self._acquirer.acquire, candidate)
                    if payload.record.status != "acquired":
                        failed_acquisition_count += 1
                        continue
                    acquisitions.append(payload)
                    acquired_ids.append(candidate.candidate_id)
                    subject_coverage[candidate.candidate_id] = covered
                    if subject_index in _covered_subjects(subject_coverage):
                        break

            if _coverage_complete(subject_coverage, len(project.subjects)):
                break

        if not acquired_ids:
            raise ValueError("source research did not acquire any usable source")

        complete = _coverage_complete(subject_coverage, len(project.subjects))
        return SourceResearchResult(
            search_plan=planned.value,
            candidates=tuple(candidates),
            selection=SourceSelectionProposal(
                selected_candidate_ids=acquired_ids,
                subject_coverage=subject_coverage,
                coverage_complete=complete,
            ),
            acquisitions=tuple(acquisitions),
            usages=tuple(usages),
            attempts_by_subject=attempts_by_subject,
            failed_acquisition_count=failed_acquisition_count,
        )


def _query_map(
    plan: SourceSearchPlan,
    *,
    subject_count: int,
    attempts_per_subject: int,
) -> dict[int, list[SubjectSearchQuery]]:
    grouped: dict[int, list[SubjectSearchQuery]] = {
        index: [] for index in range(subject_count)
    }
    for item in plan.queries:
        if item.subject_index in grouped:
            grouped[item.subject_index].append(item)
    missing = [
        index
        for index, queries in grouped.items()
        if len(queries) < attempts_per_subject
    ]
    if missing:
        raise ValueError(
            "source search plan omitted four directed attempts for subjects: "
            + ", ".join(str(item) for item in missing)
        )
    return {
        index: queries[:attempts_per_subject]
        for index, queries in grouped.items()
    }


def _covered_subjects(subject_coverage: dict[str, list[int]]) -> set[int]:
    return {
        index
        for indexes in subject_coverage.values()
        for index in indexes
        if index >= 0
    }


def _coverage_complete(
    subject_coverage: dict[str, list[int]],
    subject_count: int,
) -> bool:
    return subject_count > 0 and _covered_subjects(subject_coverage) >= set(
        range(subject_count)
    )
