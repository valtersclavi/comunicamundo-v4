"""Narrative-axis and Editorial-Project generation."""

from __future__ import annotations

import json
from uuid import UUID

from llama_index.core.llms import LLM

from .contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisProposal,
)
from .prompts import (
    EDITORIAL_PROJECT_PROMPT,
    EDITORIAL_PROJECT_REVISION_PROMPT,
    NARRATIVE_AXIS_PROMPT,
    NARRATIVE_AXIS_REVISION_PROMPT,
)


def _json(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)


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
            theme=brief.theme,
            approach=brief.approach,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            audience=brief.audience or "não especificado",
            graph_context=graph_context,
        )
        return NarrativeAxis(
            project_id=project_id,
            brief=brief,
            statement=proposal.statement,
        )

    async def revise(
        self,
        *,
        axis: NarrativeAxis,
        feedback: str,
        graph_context: str,
    ) -> NarrativeAxis:
        proposal = await self._llm.astructured_predict(
            NarrativeAxisProposal,
            NARRATIVE_AXIS_REVISION_PROMPT,
            theme=axis.brief.theme,
            approach=axis.brief.approach,
            duration_minutes=axis.brief.duration_minutes,
            output_language=axis.brief.output_language,
            audience=axis.brief.audience or "não especificado",
            current_axis=axis.statement,
            feedback=feedback,
            graph_context=graph_context,
        )
        return axis.revise(proposal.statement)


class EditorialPlanner:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def build_project(
        self,
        *,
        axis: NarrativeAxis,
        graph_context: str,
    ) -> EditorialProject:
        proposal = await self._predict_project(
            axis=axis,
            graph_context=graph_context,
            prompt=EDITORIAL_PROJECT_PROMPT,
        )
        return EditorialProject(
            project_id=axis.project_id,
            brief=axis.brief,
            narrative_axis=axis,
            working_title=proposal.working_title,
            subjects=[
                EditorialSubject(**subject.model_dump()) for subject in proposal.subjects
            ],
        )

    async def revise_project(
        self,
        *,
        project: EditorialProject,
        feedback: str,
        graph_context: str,
    ) -> EditorialProject:
        proposal = await self._predict_project(
            axis=project.narrative_axis,
            graph_context=graph_context,
            prompt=EDITORIAL_PROJECT_REVISION_PROMPT,
            editorial_project=_json(project),
            feedback=feedback,
        )
        return project.revise(
            working_title=proposal.working_title,
            subjects=[
                EditorialSubject(**subject.model_dump()) for subject in proposal.subjects
            ],
        )

    async def _predict_project(
        self,
        *,
        axis: NarrativeAxis,
        graph_context: str,
        prompt,
        editorial_project: str | None = None,
        feedback: str | None = None,
    ) -> EditorialProjectProposal:
        args = {
            "theme": axis.brief.theme,
            "approach": axis.brief.approach,
            "duration_minutes": axis.brief.duration_minutes,
            "output_language": axis.brief.output_language,
            "audience": axis.brief.audience or "não especificado",
            "narrative_axis": axis.statement,
            "graph_context": graph_context,
        }
        if editorial_project is not None:
            args["editorial_project"] = editorial_project
        if feedback is not None:
            args["feedback"] = feedback
        return await self._llm.astructured_predict(
            EditorialProjectProposal,
            prompt,
            **args,
        )
