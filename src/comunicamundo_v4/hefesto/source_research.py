from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from collections.abc import Awaitable, Callable

from .contracts import (
    CallUsage,
    AcquiredSource,
    SourceCandidateJudgment,
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

class SourceResearchExhausted(ValueError):
    """The authorized search rounds found no source fulfilling the promise."""


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
        *,
        accept_acquired: Callable[[AcquisitionPayload], Awaitable[bool]] | None = None,
        record_usage: Callable[[CallUsage], Awaitable[None]] | None = None,
        load_progress: Callable[[], Awaitable[dict | None]] | None = None,
        save_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> SourceResearchResult:
        progress = await load_progress() if load_progress else None
        progress = progress or {"batches": {}, "candidates": {}, "judgments": {},
                                "payloads": {}, "outcomes": {}, "coverage": {}, "attempts": {}}
        usages: list[CallUsage] = []

        async def checkpoint():
            if save_progress:
                await save_progress(progress)

        async def record(result):
            physical = result.usages or (result.usage,)
            usages.extend(physical)
            if record_usage:
                for call in physical:
                    await record_usage(call)

        if "plan" not in progress:
            planned = await self._editor.search_plan(brief, project)
            await record(planned)
            progress["plan"] = planned.value.model_dump(mode="json")
            await checkpoint()
        plan = SourceSearchPlan.model_validate(progress["plan"])
        query_map = _query_map(plan, subject_count=len(project.subjects),
                               attempts_per_subject=self._max_search_rounds)
        coverage = progress["coverage"]
        for attempt_index in range(self._max_search_rounds):
            for subject_index in range(len(project.subjects)):
                if subject_index in _covered_subjects(coverage):
                    continue
                batch_key = f"{attempt_index}:{subject_index}"
                if batch_key not in progress["batches"]:
                    search_query = query_map[subject_index][attempt_index]
                    found = await asyncio.to_thread(self._discovery.discover_query,
                                                    search_query.query, search_query.provider)
                    progress["batches"][batch_key] = [c.candidate_id for c in found]
                    for candidate in found:
                        progress["candidates"][candidate.candidate_id] = candidate.model_dump(mode="json")
                    key = str(subject_index)
                    progress["attempts"][key] = progress["attempts"].get(key, 0) + 1
                    await checkpoint()
                for candidate_id in progress["batches"][batch_key]:
                    if candidate_id in progress["outcomes"]:
                        continue
                    candidate = SourceCandidate.model_validate(progress["candidates"][candidate_id])
                    eligible = sorted(set(range(len(project.subjects))) - _covered_subjects(coverage))
                    if not eligible:
                        break
                    if candidate_id not in progress["judgments"]:
                        judgment = await self._editor.judge_candidate(brief, project, candidate,
                                                                    eligible_subject_indexes=eligible)
                        await record(judgment)
                        progress["judgments"][candidate_id] = judgment.value.model_dump(mode="json")
                        await checkpoint()
                    judgment = SourceCandidateJudgment.model_validate(progress["judgments"][candidate_id])
                    covered = sorted(set(judgment.covered_subject_indexes) & set(eligible))
                    if judgment.decision == "skip" or not covered:
                        progress["outcomes"][candidate_id] = False
                        await checkpoint()
                        continue
                    if candidate_id not in progress["payloads"]:
                        payload = await asyncio.to_thread(self._acquirer.acquire, candidate)
                        progress["payloads"][candidate_id] = _pack_payload(payload)
                        await checkpoint()
                    payload = _unpack_payload(progress["payloads"][candidate_id])
                    if payload.record.status != "acquired":
                        progress["outcomes"][candidate_id] = False
                        await checkpoint()
                        continue
                    # A technical exception leaves the acquired payload and all
                    # preceding phases durable. Resume the callback, not acquisition.
                    accepted = accept_acquired is None or await accept_acquired(payload)
                    progress["outcomes"][candidate_id] = accepted
                    if accepted:
                        coverage[candidate_id] = covered
                    await checkpoint()
                    if subject_index in _covered_subjects(coverage):
                        break
            if _coverage_complete(coverage, len(project.subjects)):
                break
        acquired_ids = list(coverage)
        if not acquired_ids:
            if accept_acquired is not None:
                raise SourceResearchExhausted("No source fulfilled the selected contribution within the search rounds; promise and PE preserved")
            raise ValueError("source research did not acquire any usable source")
        return SourceResearchResult(
            search_plan=plan,
            candidates=tuple(SourceCandidate.model_validate(v) for v in progress["candidates"].values()),
            selection=SourceSelectionProposal(selected_candidate_ids=acquired_ids,
                subject_coverage=coverage, coverage_complete=_coverage_complete(coverage, len(project.subjects))),
            acquisitions=tuple(_unpack_payload(progress["payloads"][key]) for key in acquired_ids),
            usages=tuple(usages),
            attempts_by_subject={i: progress["attempts"].get(str(i), 0) for i in range(len(project.subjects))},
            failed_acquisition_count=sum(p["record"]["status"] != "acquired" for p in progress["payloads"].values()),
        )


def _pack_payload(payload: AcquisitionPayload) -> dict:
    return {"record": payload.record.model_dump(mode="json"),
            "original_bytes": base64.b64encode(payload.original_bytes).decode() if payload.original_bytes is not None else None,
            "original_content_type": payload.original_content_type}


def _unpack_payload(value: dict) -> AcquisitionPayload:
    return AcquisitionPayload(record=AcquiredSource.model_validate(value["record"]),
        original_bytes=base64.b64decode(value["original_bytes"]) if value.get("original_bytes") is not None else None,
        original_content_type=value.get("original_content_type"))


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
