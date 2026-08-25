"""Production after both human approval boundaries."""
from __future__ import annotations

import asyncio

from .contracts import EditorialProject, EditorialProjectStatus, EditorialReview, FinalArticle, NarrativePlan, SubjectResearch
from .graph import ProjectGraph
from .prompts import ARCHITECTURE_PROMPT, CRITIC_PROMPT, DRAFT_PROMPT, EDIT_PROMPT, SUBJECT_RESEARCH_PROMPT


class EditorialProduction:
    def __init__(self, *, research_llm, architecture_llm, writing_llm, critic_llm, editing_llm) -> None:
        self._research_llm = research_llm
        self._architecture_llm = architecture_llm
        self._writing_llm = writing_llm
        self._critic_llm = critic_llm
        self._editing_llm = editing_llm

    async def run(self, *, project: EditorialProject, graph: ProjectGraph) -> FinalArticle:
        if project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production requires an approved Editorial Project")
        research = await asyncio.gather(*(self._research_subject(project, graph, subject) for subject in project.core_subjects))
        rendered_research = "\n\n".join(f"## {s.title}\n{r.synthesis}" for s, r in zip(project.core_subjects, research, strict=True))
        architecture = await self._architecture_llm.astructured_predict(
            NarrativePlan,
            ARCHITECTURE_PROMPT,
            narrative_axis=project.narrative_axis,
            project=project.model_dump_json(indent=2),
            research=rendered_research,
        )
        draft = (await self._writing_llm.apredict(
            DRAFT_PROMPT,
            output_language=project.brief.output_language,
            narrative_axis=project.narrative_axis,
            project=project.model_dump_json(indent=2),
            architecture=architecture.model_dump_json(indent=2),
            research=rendered_research,
            duration_minutes=project.brief.duration_minutes,
        )).strip()
        review = await self._review(project, draft)
        rounds = 0
        while not review.ready_to_publish and rounds < 2:
            draft = (await self._editing_llm.apredict(
                EDIT_PROMPT,
                narrative_axis=project.narrative_axis,
                project=project.model_dump_json(indent=2),
                draft=draft,
                review=review.model_dump_json(indent=2),
                output_language=project.brief.output_language,
            )).strip()
            rounds += 1
            review = await self._review(project, draft)
        if not draft:
            raise ValueError("writer returned an empty article")
        return FinalArticle(
            project_id=project.project_id,
            editorial_project_id=project.editorial_project_id,
            title=architecture.title,
            body_markdown=draft,
            word_count=len(draft.split()),
            quality_score=review.quality_score,
            revision_rounds=rounds,
        )

    async def _research_subject(self, project: EditorialProject, graph: ProjectGraph, subject) -> SubjectResearch:
        source_context = await graph.context_for_subject(subject, narrative_axis=project.narrative_axis)
        synthesis = (await self._research_llm.apredict(
            SUBJECT_RESEARCH_PROMPT,
            narrative_axis=project.narrative_axis,
            subject=subject.title,
            development=subject.development,
            practical_dimension=subject.practical_dimension or "none specified",
            source_context=source_context,
            output_language=project.brief.output_language,
        )).strip()
        return SubjectResearch(subject_id=subject.subject_id, synthesis=synthesis)

    async def _review(self, project: EditorialProject, draft: str) -> EditorialReview:
        return await self._critic_llm.astructured_predict(
            EditorialReview,
            CRITIC_PROMPT,
            narrative_axis=project.narrative_axis,
            project=project.model_dump_json(indent=2),
            draft=draft,
        )
