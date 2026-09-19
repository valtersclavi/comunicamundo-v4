from __future__ import annotations

from collections.abc import Sequence

from .contracts import (
    EditorialBrief,
    ProvisionalEditorialProject,
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
        result = await self._gateway.generate(
            task_type="source_editor_selection",
            prompt=source_selection_prompt(
                brief,
                project,
                candidates,
                acquired_candidate_ids=acquired_candidate_ids,
                failed_candidate_ids=failed_candidate_ids,
            ),
            schema=SourceSelectionProposal,
            system_instruction=SYSTEM,
            max_output_tokens=4_096,
            thinking_tokens=2_000,
        )
        known = {item.candidate_id for item in candidates}
        selected = result.value.selected_candidate_ids
        if len(selected) != len(set(selected)):
            raise ValueError("source editor selected a candidate more than once")
        unknown = sorted(set(selected) - known)
        if unknown:
            raise ValueError(f"source editor selected unknown candidate IDs: {unknown}")
        forbidden = set(failed_candidate_ids)
        if forbidden.intersection(selected):
            raise ValueError("source editor selected a previously failed candidate")
        return result
