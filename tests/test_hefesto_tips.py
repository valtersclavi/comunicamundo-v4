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
from comunicamundo_v4.hefesto.hefesto_tips import HefestoTipIncorporationDecision
from comunicamundo_v4.hefesto.worker import _parser


class TipExpansionLLM:
    async def astructured_predict(self, model, prompt, **kwargs):
        assert model is HefestoTipIncorporationDecision
        return HefestoTipIncorporationDecision(
            incremental_minutes=3,
            proposal=EditorialProjectProposal(
                working_title="Expanded",
                editorial_promise="Preserve the original scope and add the selected tip.",
                core_subjects=[
                    EditorialSubjectProposal(title="Original", development="Keep all original development."),
                    EditorialSubjectProposal(title="Selected tip", development="Develop the added scope."),
                ],
                llm_subjects=[],
                enrichment_opportunities=["Needs another source"],
            ),
        )


def _project() -> EditorialProject:
    return EditorialProject(
        project_id=uuid4(),
        narrative_axis_id=uuid4(),
        narrative_axis="A clear approved axis.",
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
        core_subjects=[EditorialSubject(title="Original", development="Keep all original development.")],
        llm_subjects=["Selected tip"],
        enrichment_opportunities=["Needs another source"],
    )


def test_selected_tip_expands_duration_and_keeps_human_gate():
    current = _project()
    director = EditorialDirector(axis_llm=None, project_llm=TipExpansionLLM())
    revised = asyncio.run(
        director.incorporate_hefesto_tips(
            current=current,
            graph_context="persisted understanding",
            selected_tips=current.llm_subjects,
        )
    )
    assert current.brief.duration_minutes == 8
    assert revised.brief.duration_minutes == 11
    assert revised.parent_project_id == current.editorial_project_id
    assert revised.status.value == "proposed"
    assert revised.llm_subjects == []
    assert [subject.title for subject in revised.core_subjects] == ["Original", "Selected tip"]


def test_normal_tip_command_has_no_keep_duration_option():
    parser = _parser()
    parsed = parser.parse_args(
        [
            "incorporate-hefesto-tips",
            "--editorial-project-id",
            str(uuid4()),
            "--all",
        ]
    )
    assert parsed.command == "incorporate-hefesto-tips"
    assert parsed.all is True
    assert not hasattr(parsed, "keep_duration")
