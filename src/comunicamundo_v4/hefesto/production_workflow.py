"""Approved Editorial Project to final article."""

from __future__ import annotations

from llama_index.core.llms import LLM
from llama_index.core.workflow import Event, StartEvent, StopEvent, Workflow, step

from .contracts import (
    EditorialProject,
    EditorialProjectStatus,
    EditorialReview,
    FinalArticle,
    NarrativePlan,
    SubjectResearch,
)
from .graph import ProjectGraph
from .production import (
    ArticleEditor,
    ArticleWriter,
    EditorialCritic,
    NarrativeArchitect,
    SubjectResearcher,
)


class ProduceArticleRequest(StartEvent):
    project: EditorialProject
    graph: ProjectGraph


class ResearchCompleted(Event):
    project: EditorialProject
    research: list[SubjectResearch]


class NarrativePlanCompleted(Event):
    project: EditorialProject
    research: list[SubjectResearch]
    narrative_plan: NarrativePlan


class DraftCompleted(Event):
    project: EditorialProject
    narrative_plan: NarrativePlan
    draft: str
    revision_round: int = 0


class RevisionRequired(Event):
    project: EditorialProject
    narrative_plan: NarrativePlan
    draft: str
    review: EditorialReview
    revision_round: int


class ArticleAccepted(Event):
    project: EditorialProject
    draft: str
    review: EditorialReview
    revision_round: int


class EditorialProductionWorkflow(Workflow):
    def __init__(
        self,
        *,
        research_llm: LLM,
        architecture_llm: LLM,
        writing_llm: LLM,
        critic_llm: LLM,
        editing_llm: LLM,
        max_revision_rounds: int = 2,
        timeout: float | None = 1800,
    ) -> None:
        super().__init__(timeout=timeout)
        self._researcher = SubjectResearcher(research_llm)
        self._architect = NarrativeArchitect(architecture_llm)
        self._writer = ArticleWriter(writing_llm)
        self._critic = EditorialCritic(critic_llm)
        self._editor = ArticleEditor(editing_llm)
        self._max_revision_rounds = max_revision_rounds

    @step
    async def research(self, event: ProduceArticleRequest) -> ResearchCompleted:
        if event.project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production requires an approved Editorial Project")
        research = await self._researcher.research_all(
            project=event.project,
            graph=event.graph,
        )
        return ResearchCompleted(project=event.project, research=research)

    @step
    async def plan_narrative(self, event: ResearchCompleted) -> NarrativePlanCompleted:
        narrative_plan = await self._architect.plan(
            project=event.project,
            research=event.research,
        )
        return NarrativePlanCompleted(
            project=event.project,
            research=event.research,
            narrative_plan=narrative_plan,
        )

    @step
    async def write(self, event: NarrativePlanCompleted) -> DraftCompleted:
        draft = await self._writer.draft(
            project=event.project,
            research=event.research,
            narrative_plan=event.narrative_plan,
        )
        return DraftCompleted(
            project=event.project,
            narrative_plan=event.narrative_plan,
            draft=draft,
        )

    @step
    async def review(
        self,
        event: DraftCompleted,
    ) -> RevisionRequired | ArticleAccepted:
        review = await self._critic.review(project=event.project, draft=event.draft)
        if review.ready_to_publish or event.revision_round >= self._max_revision_rounds:
            return ArticleAccepted(
                project=event.project,
                draft=event.draft,
                review=review,
                revision_round=event.revision_round,
            )
        return RevisionRequired(
            project=event.project,
            narrative_plan=event.narrative_plan,
            draft=event.draft,
            review=review,
            revision_round=event.revision_round,
        )

    @step
    async def revise(self, event: RevisionRequired) -> DraftCompleted:
        revised = await self._editor.revise(
            project=event.project,
            narrative_plan=event.narrative_plan,
            draft=event.draft,
            review=event.review,
        )
        return DraftCompleted(
            project=event.project,
            narrative_plan=event.narrative_plan,
            draft=revised,
            revision_round=event.revision_round + 1,
        )

    @step
    async def finish(self, event: ArticleAccepted) -> StopEvent:
        article = FinalArticle(
            project_id=event.project.project_id,
            editorial_project_id=event.project.editorial_project_id,
            title=event.project.working_title,
            body_markdown=event.draft.strip(),
            word_count=len(event.draft.split()),
            quality_score=event.review.quality_score,
            revision_rounds=event.revision_round,
        )
        return StopEvent(result=article)


async def run_editorial_production(
    workflow: EditorialProductionWorkflow,
    *,
    project: EditorialProject,
    graph: ProjectGraph,
) -> FinalArticle:
    result = await workflow.run(
        start_event=ProduceArticleRequest(project=project, graph=graph)
    )
    if not isinstance(result, FinalArticle):
        raise TypeError(f"expected FinalArticle, received {type(result).__name__}")
    return result

