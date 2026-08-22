"""Editorial research, writing, criticism, and revision."""

from __future__ import annotations

import asyncio
import json

from llama_index.core.llms import LLM

from .contracts import (
    EditorialProject,
    EditorialReview,
    NarrativePlan,
    SubjectResearch,
)
from .graph import ProjectGraph
from .prompts import (
    ARTICLE_DRAFT_PROMPT,
    ARTICLE_REVISION_PROMPT,
    EDITORIAL_REVIEW_PROMPT,
    NARRATIVE_PLAN_PROMPT,
    SUBJECT_RESEARCH_PROMPT,
)


def _json(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)


class SubjectResearcher:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def research_all(
        self,
        *,
        project: EditorialProject,
        graph: ProjectGraph,
    ) -> list[SubjectResearch]:
        return list(
            await asyncio.gather(
                *(
                    self._research_subject(project=project, graph=graph, subject=subject)
                    for subject in project.subjects
                )
            )
        )

    async def _research_subject(self, *, project, graph, subject) -> SubjectResearch:
        graph_context = await graph.context_for_subject(subject, project.brief)
        questions = "\n".join(f"- {question}" for question in subject.research_questions)
        synthesis = await self._llm.apredict(
            SUBJECT_RESEARCH_PROMPT,
            theme=project.brief.theme,
            approach=project.brief.approach,
            audience=project.brief.audience or "não especificado",
            subject_title=subject.title,
            subject_purpose=subject.purpose,
            research_questions=questions,
            graph_context=graph_context,
            output_language=project.brief.output_language,
        )
        return SubjectResearch(
            subject_id=subject.subject_id,
            synthesis=synthesis.strip(),
        )


class NarrativeArchitect:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def plan(
        self,
        *,
        project: EditorialProject,
        research: list[SubjectResearch],
    ) -> NarrativePlan:
        plan = await self._llm.astructured_predict(
            NarrativePlan,
            NARRATIVE_PLAN_PROMPT,
            theme=project.brief.theme,
            approach=project.brief.approach,
            duration_minutes=project.brief.duration_minutes,
            output_language=project.brief.output_language,
            audience=project.brief.audience or "não especificado",
            editorial_project=_json(project),
            subject_research=json.dumps(
                [item.model_dump(mode="json") for item in research],
                ensure_ascii=False,
                indent=2,
            ),
        )
        approved_subject_ids = {subject.subject_id for subject in project.subjects}
        planned_subject_ids = {beat.subject_id for beat in plan.beats}
        if planned_subject_ids != approved_subject_ids:
            raise ValueError("narrative plan must cover every approved subject exactly")
        return plan


class ArticleWriter:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def draft(
        self,
        *,
        project: EditorialProject,
        research: list[SubjectResearch],
        narrative_plan: NarrativePlan,
    ) -> str:
        return await self._llm.apredict(
            ARTICLE_DRAFT_PROMPT,
            theme=project.brief.theme,
            approach=project.brief.approach,
            duration_minutes=project.brief.duration_minutes,
            output_language=project.brief.output_language,
            audience=project.brief.audience or "não especificado",
            editorial_project=_json(project),
            narrative_plan=_json(narrative_plan),
            subject_research=json.dumps(
                [item.model_dump(mode="json") for item in research],
                ensure_ascii=False,
                indent=2,
            ),
        )


class EditorialCritic:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def review(self, *, project: EditorialProject, draft: str) -> EditorialReview:
        return await self._llm.astructured_predict(
            EditorialReview,
            EDITORIAL_REVIEW_PROMPT,
            brief=_json(project.brief),
            editorial_project=_json(project),
            draft=draft,
        )


class ArticleEditor:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def revise(
        self,
        *,
        project: EditorialProject,
        narrative_plan: NarrativePlan,
        draft: str,
        review: EditorialReview,
    ) -> str:
        return await self._llm.apredict(
            ARTICLE_REVISION_PROMPT,
            brief=_json(project.brief),
            editorial_project=_json(project),
            narrative_plan=_json(narrative_plan),
            review=_json(review),
            draft=draft,
        )
