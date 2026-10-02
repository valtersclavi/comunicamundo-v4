from __future__ import annotations

from collections.abc import Sequence

from .atomic import generate_atomic
from .contracts import (
    EditorialBrief,
    ProvisionalEditorialProject,
    SourceCandidateJudgment,
    SourceCandidate,
    SourceSearchPlan,
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

    def __init__(self, gateway: ModelGateway, *, repository=None, execution_run_id=None) -> None:
        self._gateway = gateway
        self._repository = repository
        self._execution_id = execution_run_id

    async def _generate(self, **kwargs):
        checkpoint = {}
        if self._repository is not None and self._execution_id is not None:
            checkpoint = {
                "load": lambda key: self._repository.atomic_task(self._execution_id, key),
                "save": lambda key, state: self._repository.save_atomic_task(self._execution_id, key, state),
            }
        return await generate_atomic(self._gateway, **kwargs, **checkpoint)

    async def provisional_project(
        self, brief: EditorialBrief
    ) -> ModelResult[ProvisionalEditorialProject]:
        result = await self._generate(
            task_type="provisional_editorial_project",
            prompt=provisional_project_prompt(brief),
            schema=ProvisionalEditorialProject,
            system_instruction=SYSTEM,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )
        return ModelResult(
            value=_normalize_provisional_duration(result.value),
            usage=result.usage,
            usages=result.usages,
        )

    async def search_plan(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
    ) -> ModelResult[SourceSearchPlan]:
        return await self._generate(
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
        result = await self._generate(
            task_type="provisional_editorial_revision",
            prompt=provisional_revision_prompt(brief, project, instruction),
            schema=ProvisionalEditorialProject,
            system_instruction=SYSTEM,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )
        return ModelResult(
            value=_normalize_provisional_duration(result.value),
            usage=result.usage,
            usages=result.usages,
        )

    async def judge_candidate(
        self,
        brief: EditorialBrief,
        project: ProvisionalEditorialProject,
        candidate: SourceCandidate,
        *,
        eligible_subject_indexes: Sequence[int],
    ) -> ModelResult[SourceCandidateJudgment]:
        return await self._generate(
            task_type="source_candidate_judgment",
            prompt=source_candidate_judgment_prompt(
                brief,
                project,
                candidate,
                eligible_subject_indexes=eligible_subject_indexes,
            ),
            schema=SourceCandidateJudgment,
            system_instruction=SYSTEM,
            max_output_tokens=512,
            thinking_tokens=300,
        )


def _normalize_provisional_duration(
    project: ProvisionalEditorialProject,
) -> ProvisionalEditorialProject:
    total = sum(item.allocated_minutes for item in project.subjects)
    if total <= 0:
        values = [project.duration_minutes / len(project.subjects)] * len(project.subjects)
    else:
        values = [
            project.duration_minutes * item.allocated_minutes / total
            for item in project.subjects
        ]
    rounded = [round(value, 2) for value in values]
    rounded[-1] = round(rounded[-1] + project.duration_minutes - sum(rounded), 2)
    subjects = [
        item.model_copy(update={"allocated_minutes": max(value, 0.01)})
        for item, value in zip(project.subjects, rounded, strict=True)
    ]
    return project.model_copy(update={"subjects": subjects})

