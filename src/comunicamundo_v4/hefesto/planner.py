"""Editorial-project generation."""

from __future__ import annotations

from uuid import UUID

from llama_index.core.llms import LLM

from .contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
)
from .prompts import EDITORIAL_PROJECT_PROMPT


class EditorialPlanner:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def build_project(
        self,
        *,
        project_id: UUID,
        brief: EditorialBrief,
        graph_context: str,
    ) -> EditorialProject:
        proposal = await self._llm.astructured_predict(
            EditorialProjectProposal,
            EDITORIAL_PROJECT_PROMPT,
            theme=brief.theme,
            approach=brief.approach,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            audience=brief.audience or "não especificado",
            graph_context=graph_context,
        )
        return EditorialProject(
            project_id=project_id,
            brief=brief,
            working_title=proposal.working_title,
            editorial_promise=proposal.editorial_promise,
            subjects=[
                EditorialSubject(**subject.model_dump()) for subject in proposal.subjects
            ],
        )

