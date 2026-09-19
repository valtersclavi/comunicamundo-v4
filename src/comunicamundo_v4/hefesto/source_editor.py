from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
import re

from .contracts import (
    CallUsage,
    EditorialBrief,
    ProvisionalEditorialProject,
    RawSourceSelectionProposal,
    SourceCandidate,
    SourceSearchPlan,
    SourceSelectionProposal,
)
from .gateway import ModelGateway, ModelResult
from .prompts import (
    SYSTEM,
    provisional_project_prompt,
    provisional_revision_prompt,
    source_search_plan_prompt,
    source_selection_prompt,
)

_SOURCE_SELECTION_BATCH_SIZE = 12


class EditorialSourceEditor:
    """Model-backed editorial judgments around the non-public source search."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def provisional_project(
        self, brief: EditorialBrief
    ) -> ModelResult[ProvisionalEditorialProject]:
        return await self._gateway.generate(
            task_type="provisional_editorial_project",
            prompt=provisional_project_prompt(brief),
            schema=ProvisionalEditorialProject,
            system_instruction=SYSTEM,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )

    async def search_plan(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
    ) -> ModelResult[SourceSearchPlan]:
        return await self._gateway.generate(
            task_type="source_search_plan",
            prompt=source_search_plan_prompt(brief, project),
            schema=SourceSearchPlan,
            system_instruction=SYSTEM,
            max_output_tokens=4_096,
            thinking_tokens=2_000,
        )

    async def revise_provisional_project(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
        instruction: str,
    ) -> ModelResult[ProvisionalEditorialProject]:
        return await self._gateway.generate(
            task_type="provisional_editorial_revision",
            prompt=provisional_revision_prompt(brief, project, instruction),
            schema=ProvisionalEditorialProject,
            system_instruction=SYSTEM,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )

    async def select(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
        candidates: Sequence[SourceCandidate],
        *,
        acquired_candidate_ids: Sequence[str] = (),
        failed_candidate_ids: Sequence[str] = (),
    ) -> ModelResult[SourceSelectionProposal]:
        if len(candidates) > _SOURCE_SELECTION_BATCH_SIZE:
            return await self._select_in_batches(
                brief,
                project,
                candidates,
                acquired_candidate_ids=acquired_candidate_ids,
                failed_candidate_ids=failed_candidate_ids,
            )

        result = await self._gateway.generate(
            task_type="source_editor_selection",
            prompt=source_selection_prompt(
                brief,
                project,
                candidates,
                acquired_candidate_ids=acquired_candidate_ids,
                failed_candidate_ids=failed_candidate_ids,
            ),
            schema=RawSourceSelectionProposal,
            system_instruction=SYSTEM,
            max_output_tokens=4_096,
            thinking_tokens=2_000,
        )
        selection = _sanitize_selection(
            result.value,
            candidates,
            failed_candidate_ids=failed_candidate_ids,
        )
        return ModelResult(value=selection, usage=result.usage)

    async def _select_in_batches(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
        candidates: Sequence[SourceCandidate],
        *,
        acquired_candidate_ids: Sequence[str] = (),
        failed_candidate_ids: Sequence[str] = (),
    ) -> ModelResult[SourceSelectionProposal]:
        proposals: list[SourceSelectionProposal] = []
        usages = []
        for index in range(0, len(candidates), _SOURCE_SELECTION_BATCH_SIZE):
            batch = candidates[index:index + _SOURCE_SELECTION_BATCH_SIZE]
            result = await self._gateway.generate(
                task_type="source_editor_selection_batch",
                prompt=source_selection_prompt(
                    brief,
                    project,
                    batch,
                    acquired_candidate_ids=acquired_candidate_ids,
                    failed_candidate_ids=failed_candidate_ids,
                ),
                schema=RawSourceSelectionProposal,
                system_instruction=SYSTEM,
                max_output_tokens=2_048,
                thinking_tokens=1_500,
            )
            selection = _sanitize_selection(
                result.value,
                batch,
                failed_candidate_ids=failed_candidate_ids,
            )
            proposals.append(selection)
            usages.append(result.usage)

        return ModelResult(
            value=_merge_batch_selections(proposals),
            usage=_combined_usage("source_editor_selection", usages),
        )


def _combined_usage(task_type: str, usages: Sequence[CallUsage]) -> CallUsage:
    rated_cost = sum(
        (Decimal(item.rated_cost_usd) for item in usages if item.rated_cost_usd),
        Decimal("0"),
    )
    return CallUsage(
        task_type=task_type,
        prompt_tokens=sum(item.prompt_tokens for item in usages),
        cached_tokens=sum(item.cached_tokens for item in usages),
        output_tokens=sum(item.output_tokens for item in usages),
        reasoning_tokens=sum(item.reasoning_tokens for item in usages),
        total_tokens=sum(item.total_tokens for item in usages),
        latency_ms=sum(item.latency_ms for item in usages),
        rated_cost_usd=f"{rated_cost:.9f}" if rated_cost else None,
        status="succeeded",
    )


def _sanitize_selection(
    selection: RawSourceSelectionProposal,
    candidates: Sequence[SourceCandidate],
    *,
    failed_candidate_ids: Sequence[str] = (),
) -> SourceSelectionProposal:
    known = {item.candidate_id for item in candidates}
    forbidden = set(failed_candidate_ids)
    selected: list[str] = []
    for item in selection.selected_candidate_ids:
        if not re.fullmatch(r"[0-9a-f]{64}", item):
            continue
        if item not in known or item in forbidden or item in selected:
            continue
        selected.append(item)

    coverage_complete = selection.coverage_complete and bool(selected)
    follow_up_queries = list(selection.follow_up_queries)
    if not coverage_complete and not follow_up_queries:
        follow_up_queries = [
            "buscar fontes adicionais para cobrir lacunas do eixo editorial"
        ]
    return SourceSelectionProposal(
        selected_candidate_ids=selected,
        coverage_judgment=selection.coverage_judgment,
        coverage_complete=coverage_complete,
        follow_up_queries=follow_up_queries,
    )


def _merge_batch_selections(
    proposals: Sequence[SourceSelectionProposal],
) -> SourceSelectionProposal:
    selected: list[str] = []
    follow_up_queries: list[str] = []
    judgments: list[str] = []
    coverage_complete = False
    for proposal in proposals:
        judgments.append(proposal.coverage_judgment)
        for candidate_id in proposal.selected_candidate_ids:
            if candidate_id not in selected:
                selected.append(candidate_id)
        for query in proposal.follow_up_queries:
            if query not in follow_up_queries:
                follow_up_queries.append(query)
        coverage_complete = coverage_complete or proposal.coverage_complete

    if coverage_complete and selected:
        follow_up_queries = []
    elif not follow_up_queries:
        follow_up_queries = [
            "buscar fontes adicionais para cobrir lacunas do eixo editorial"
        ]
    return SourceSelectionProposal(
        selected_candidate_ids=selected,
        coverage_judgment=" | ".join(judgments),
        coverage_complete=coverage_complete and bool(selected),
        follow_up_queries=follow_up_queries,
    )
