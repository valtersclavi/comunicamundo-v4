"""Narrative Axis and editorial-project generation."""

from __future__ import annotations

from uuid import UUID

from llama_index.core.llms import LLM

from .contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
)
from .prompts import (
    EDITORIAL_PROJECT_PROMPT,
    NARRATIVE_AXIS_PROMPT,
    NARRATIVE_AXIS_REVISION_PROMPT,
)


def _brief_prompt_args(brief: EditorialBrief, graph_context: str) -> dict[str, str | int]:
    return {
        "theme": brief.theme,
        "approach": brief.approach,
        "duration_minutes": brief.duration_minutes,
        "output_language": brief.output_language,
        "audience": brief.audience or "não especificado",
        "graph_context": graph_context,
    }


class NarrativeAxisPlanner:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def propose(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        graph_context: str,
    ) -> NarrativeAxis:
        proposal = await self._llm.astructured_predict(
            NarrativeAxisProposal,
            NARRATIVE_AXIS_PROMPT,
            **_brief_prompt_args(brief, graph_context),
        )
        return NarrativeAxis(project_id=project_id, statement=proposal.statement)

    async def revise(
        self,
        *,
        current: NarrativeAxis,
        brief: EditorialBrief,
        graph_context: str,
        feedback: str,
    ) -> NarrativeAxis:
        if current.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("approved Narrative Axis is immutable")
        if current.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot revise a superseded Narrative Axis")
        cleaned = feedback.strip()
        if not cleaned:
            raise ValueError("revision feedback is required")
        proposal = await self._llm.astructured_predict(
            NarrativeAxisProposal,
            NARRATIVE_AXIS_REVISION_PROMPT,
            **_brief_prompt_args(brief, graph_context),
            current_axis=current.statement,
            feedback=cleaned,
        )
        return NarrativeAxis(
            project_id=current.project_id,
            parent_axis_id=current.narrative_axis_id,
            statement=proposal.statement,
        )


class EditorialPlanner:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def build_project(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        graph_context: str,
        narrative_axis: NarrativeAxis,
    ) -> EditorialProject:
        if narrative_axis.status is not NarrativeAxisStatus.APPROVED:
            raise ValueError(
                "Editorial Project cannot be created from an unapproved Narrative Axis"
            )
        if narrative_axis.project_id != project_id:
            raise ValueError("narrative axis project ID does not match")
        proposal = await self._llm.astructured_predict(
            EditorialProjectProposal,
            EDITORIAL_PROJECT_PROMPT,
            **_brief_prompt_args(brief, graph_context),
            narrative_axis=narrative_axis.statement,
        )
        return EditorialProject(
            project_id=project_id,
            brief=brief,
            narrative_axis_id=narrative_axis.narrative_axis_id,
            narrative_axis=narrative_axis.statement,
            working_title=proposal.working_title,
            editorial_promise=proposal.editorial_promise,
            subjects=[
                EditorialSubject(**subject.model_dump()) for subject in proposal.subjects
            ],
        )

