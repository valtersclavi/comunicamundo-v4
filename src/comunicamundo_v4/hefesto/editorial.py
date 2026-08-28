"""Human-gated editorial decisions built on top of graph understanding."""
from __future__ import annotations

from uuid import UUID

from .contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisProposal,
)
from .prompts import EDITORIAL_PROJECT_PROMPT, NARRATIVE_AXIS_PROMPT, REVISE_AXIS_PROMPT, REVISE_PROJECT_PROMPT


class EditorialDirector:
    def __init__(self, *, axis_llm, project_llm) -> None:
        self._axis_llm = axis_llm
        self._project_llm = project_llm

    async def propose_axis(self, *, project_id: UUID, brief: EditorialBrief, graph_context: str) -> NarrativeAxis:
        proposal = await self._axis_llm.astructured_predict(
            NarrativeAxisProposal,
            NARRATIVE_AXIS_PROMPT,
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            graph_context=graph_context,
        )
        return NarrativeAxis(project_id=project_id, statement=proposal.statement)

    async def revise_axis(self, *, current: NarrativeAxis, brief: EditorialBrief, graph_context: str, feedback: str) -> NarrativeAxis:
        proposal = await self._axis_llm.astructured_predict(
            NarrativeAxisProposal,
            REVISE_AXIS_PROMPT,
            current_axis=current.statement,
            feedback=feedback,
            brief=brief.model_dump_json(indent=2),
            graph_context=graph_context,
        )
        return NarrativeAxis(project_id=current.project_id, parent_axis_id=current.narrative_axis_id, statement=proposal.statement)

    async def build_project(self, *, axis: NarrativeAxis, brief: EditorialBrief, graph_context: str) -> EditorialProject:
        if axis.status.value != "approved":
            raise ValueError("Editorial Project requires an approved Narrative Axis")
        proposal = await self._project_llm.astructured_predict(
            EditorialProjectProposal,
            EDITORIAL_PROJECT_PROMPT,
            narrative_axis=axis.statement,
            brief=brief.model_dump_json(indent=2),
            graph_context=graph_context,
            synthesis=graph_context,
            central_ideas="(none)",
            relations_and_tensions="(none)",
            nuances="(none)",
            source_limits="(none)",
            editorial_opportunities="(none)",
            axis_feedback_history="(none)",
            carry_forwards="(none)",
            output_language=brief.output_language,
        )
        return self._project_from_proposal(proposal, axis=axis, brief=brief)

    async def revise_project(self, *, current: EditorialProject, graph_context: str, feedback: str) -> EditorialProject:
        if current.status.value == "approved":
            raise ValueError("approved Editorial Project is immutable")
        proposal = await self._project_llm.astructured_predict(
            EditorialProjectProposal,
            REVISE_PROJECT_PROMPT,
            narrative_axis=current.narrative_axis,
            current_project=current.model_dump_json(indent=2),
            feedback=feedback,
            graph_context=graph_context,
        )
        revised = self._project_from_proposal(
            proposal,
            axis=NarrativeAxis(
                narrative_axis_id=current.narrative_axis_id,
                project_id=current.project_id,
                statement=current.narrative_axis,
                status="approved",
            ),
            brief=current.brief,
        )
        return revised.model_copy(update={"parent_project_id": current.editorial_project_id})

    @staticmethod
    def _project_from_proposal(proposal: EditorialProjectProposal, *, axis: NarrativeAxis, brief: EditorialBrief) -> EditorialProject:
        return EditorialProject(
            project_id=axis.project_id,
            narrative_axis_id=axis.narrative_axis_id,
            narrative_axis=axis.statement,
            brief=brief,
            working_title=proposal.working_title,
            editorial_promise=proposal.editorial_promise,
            core_subjects=[EditorialSubject(**subject.model_dump()) for subject in proposal.core_subjects],
            llm_subjects=proposal.llm_subjects,
            enrichment_opportunities=proposal.enrichment_opportunities,
        )
