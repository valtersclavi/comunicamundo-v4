from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .contracts import (
    CallUsage,
    EditorialBrief,
    ProvisionalEditorialProject,
    SourceCandidateJudgment,
    SourceCandidate,
    SourceSearchPlan,
    SourceSelectionProposal,
)
from .gateway import ModelGateway, ModelResult
from .prompts import (
    SYSTEM,
    provisional_project_prompt,
    provisional_revision_prompt,
    source_candidate_judgment_prompt,
    source_search_plan_prompt,
)


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
        selected: list[str] = []
        usages: list[CallUsage] = []
        complete_if_acquired = False
        failed = set(failed_candidate_ids)
        acquired = set(acquired_candidate_ids)
        for candidate in candidates:
            if candidate.candidate_id in failed or candidate.candidate_id in acquired:
                continue
            result = await self._gateway.generate(
                task_type="source_candidate_judgment",
                prompt=source_candidate_judgment_prompt(
                    brief,
                    project,
                    candidate,
                    acquired_candidate_ids=acquired_candidate_ids,
                    failed_candidate_ids=failed_candidate_ids,
                ),
                schema=SourceCandidateJudgment,
                system_instruction=SYSTEM,
                max_output_tokens=768,
                thinking_tokens=500,
            )
            usages.append(result.usage)
            judgment = result.value
            if judgment.decision == "acquire" and candidate.candidate_id not in selected:
                selected.append(candidate.candidate_id)
            if judgment.coverage_complete_if_acquired and selected:
                complete_if_acquired = True
                break

        coverage_complete = bool(selected) and complete_if_acquired
        return ModelResult(
            value=SourceSelectionProposal(
                selected_candidate_ids=selected,
                coverage_judgment=_selection_status(selected, coverage_complete),
                coverage_complete=coverage_complete,
                follow_up_queries=[] if coverage_complete else [
                    "buscar mais candidatos para os assuntos ainda sem fonte"
                ],
            ),
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


def _selection_status(selected: Sequence[str], coverage_complete: bool) -> str:
    if coverage_complete:
        return "selected candidate completes source coverage"
    if selected:
        return "selected candidate contributes to source coverage"
    return "no candidate selected"
