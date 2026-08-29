from __future__ import annotations

import asyncio
from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
    EditorialSubjectProposal,
)
from comunicamundo_v4.hefesto.editorial import EditorialDirector
from comunicamundo_v4.hefesto.production import _production_project_json


class RevisionLLM:
    async def astructured_predict(self, model, prompt, **kwargs):
        assert model is EditorialProjectProposal
        return EditorialProjectProposal(
            working_title="Revised",
            editorial_promise="Revised promise",
            core_subjects=[
                EditorialSubjectProposal(title="Core", development="Keep the approved core scope."),
            ],
            llm_subjects=["A new optional tip"],
            enrichment_opportunities=["A newly noticed enrichment opportunity"],
        )


def _project() -> EditorialProject:
    return EditorialProject(
        project_id=uuid4(),
        narrative_axis_id=uuid4(),
        narrative_axis="Approved axis.",
        brief=EditorialBrief(
            theme="Theme",
            approach="Approach",
            duration_minutes=8,
            output_language="pt-BR",
            audience="Audience",
            product="article",
        ),
        working_title="Current",
        editorial_promise="Current promise",
        core_subjects=[EditorialSubject(title="Core", development="Keep the approved core scope.")],
        llm_subjects=["Existing optional tip"],
        enrichment_opportunities=["First-project enrichment opportunity"],
    )


def test_revised_project_never_repeats_enrichment_opportunities() -> None:
    current = _project()
    director = EditorialDirector(axis_llm=None, project_llm=RevisionLLM())
    revised = asyncio.run(
        director.revise_project(
            current=current,
            graph_context="persisted understanding",
            feedback="Revise the project.",
        )
    )

    assert current.enrichment_opportunities == ["First-project enrichment opportunity"]
    assert revised.parent_project_id == current.editorial_project_id
    assert revised.enrichment_opportunities == []


def test_production_context_contains_only_authorized_editorial_scope() -> None:
    approved = _project().approve()
    context = _production_project_json(approved)

    assert '"core_subjects"' in context
    assert "Keep the approved core scope." in context
    assert "llm_subjects" not in context
    assert "Existing optional tip" not in context
    assert "enrichment_opportunities" not in context
    assert "First-project enrichment opportunity" not in context
