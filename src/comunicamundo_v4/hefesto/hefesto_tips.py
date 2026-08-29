"""Hefesto tips: optional model-knowledge scope with explicit time cost."""
from __future__ import annotations

from typing import Sequence

from llama_index.core.prompts import PromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from .contracts import EditorialBrief, EditorialProject, EditorialProjectProposal, EditorialUnderstanding, NarrativeAxis


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HefestoTipTiming(_StrictModel):
    incremental_minutes: int = Field(ge=1, le=120)


class HefestoTipTimingResult(_StrictModel):
    timings: list[HefestoTipTiming] = Field(default_factory=list, max_length=12)


class HefestoTipIncorporationDecision(_StrictModel):
    incremental_minutes: int = Field(ge=1, le=240)
    proposal: EditorialProjectProposal


HEFESTO_TIP_TIMING_PROMPT = PromptTemplate(
    """
You are Hefesto's editor-in-chief. The Editorial Project below is already complete for the brief's requested duration: its core subjects consume 100% of that editorial time budget.

The `llm_subjects` list is presented to the user as **Dicas do Hefesto**. These tips are NOT incorporated into the current project. For each tip, estimate how many additional whole minutes would be required to add it responsibly WITHOUT reducing, compressing, removing, or reprioritizing any existing core subject.

Return one timing per tip, in the exact same order. The estimate is editorial, not mathematical. A selected tip means added scope, therefore added duration.

Brief:
{brief}

Current Editorial Project proposal:
{project}

Return only the structured timing result.
""".strip()
)


INCORPORATE_HEFESTO_TIPS_PROMPT = PromptTemplate(
    """
You are Hefesto's editor-in-chief revising a proposed Editorial Project because the user explicitly selected one or more **Dicas do Hefesto**.

Canonical rule:
- the current core subjects already consume 100% of the current duration;
- selecting a tip is an explicit request to add scope;
- added scope increases duration;
- NEVER reduce, compress, remove, sacrifice, or reprioritize existing core scope in order to keep the old duration;
- keeping the old duration is not an option you may offer or infer. It is a separate exceptional constraint only when the user explicitly asks for it, and then the user must first choose which existing topic will be sacrificed. That exceptional flow is NOT being requested here.

Revise the project coherently so every selected tip is actually incorporated into the core editorial development. Preserve the full existing core scope and depth. You may reorganize how the expanded project is expressed, but do not silently lose any existing commitment. Remove incorporated tips from `llm_subjects`; leave any unselected tips available as Dicas do Hefesto. Do not search, add sources, or convert enrichment opportunities into supported material.

Estimate the TOTAL additional whole minutes required for the selected tips at the same editorial depth as the current project. This value will be added to the current brief duration after your decision.

Approved Narrative Axis:
{narrative_axis}

Current duration: {duration_minutes} minutes

Current Editorial Project:
{current_project}

Selected Dicas do Hefesto:
{selected_tips}

Internal source understanding:
{graph_context}

Return only the structured incorporation decision.
""".strip()
)


def _display_tip(tip: str, minutes: int) -> str:
    clean = tip.strip()
    if not clean:
        raise ValueError("Hefesto tip cannot be empty")
    return f"{clean} — +~{minutes} min"


class HefestoTipsEditorialProjectBuilder:
    """Decorate the proven canonical builder with time estimates for Dicas do Hefesto."""

    def __init__(self, runtime) -> None:
        if runtime.context_window <= 0 or runtime.project_max_tokens <= 0:
            raise ValueError("Editorial Project runtime must declare explicit context and output budgets")
        self._runtime = runtime
        # Construct the canonical builder at the same lifecycle boundary as before.
        # This preserves approve-axis idempotency: a reusable rerun constructs a builder
        # but never calls build().
        from .editorial_project import LlamaIndexEditorialProjectBuilder

        self._canonical_builder = LlamaIndexEditorialProjectBuilder(runtime)

    def build(
        self,
        *,
        axis: NarrativeAxis,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        axis_feedback_history: str,
        carry_forwards: str,
    ) -> EditorialProjectProposal:
        # Keep the already-proven project builder as the source of editorial content.
        # Dicas do Hefesto only annotate its optional model subjects with incremental time.
        proposal = self._canonical_builder.build(
            axis=axis,
            brief=brief,
            understanding=understanding,
            axis_feedback_history=axis_feedback_history,
            carry_forwards=carry_forwards,
        )
        if not proposal.llm_subjects:
            return proposal
        timing = self._runtime.project_llm.structured_predict(
            HefestoTipTimingResult,
            HEFESTO_TIP_TIMING_PROMPT,
            brief=brief.model_dump_json(indent=2),
            project=proposal.model_dump_json(indent=2),
        )
        if len(timing.timings) != len(proposal.llm_subjects):
            raise ValueError("Hefesto tip timing count does not match the proposed tips")
        tips = [
            _display_tip(tip, estimate.incremental_minutes)
            for tip, estimate in zip(proposal.llm_subjects, timing.timings, strict=True)
        ]
        return proposal.model_copy(update={"llm_subjects": tips})


async def incorporate_hefesto_tips(
    *,
    project_llm,
    current: EditorialProject,
    graph_context: str,
    selected_tips: Sequence[str],
) -> tuple[EditorialProjectProposal, int]:
    selected = [tip.strip() for tip in selected_tips if tip.strip()]
    if not selected:
        raise ValueError("select at least one Dica do Hefesto")
    available = set(current.llm_subjects)
    missing = [tip for tip in selected if tip not in available]
    if missing:
        raise ValueError(f"selected Dica do Hefesto is not available on this project: {missing[0]}")
    decision = await project_llm.astructured_predict(
        HefestoTipIncorporationDecision,
        INCORPORATE_HEFESTO_TIPS_PROMPT,
        narrative_axis=current.narrative_axis,
        duration_minutes=current.brief.duration_minutes,
        current_project=current.model_dump_json(indent=2),
        selected_tips="\n".join(f"- {tip}" for tip in selected),
        graph_context=graph_context,
    )
    remaining_selected = [tip for tip in selected if tip in decision.proposal.llm_subjects]
    if remaining_selected:
        raise ValueError("incorporated Dicas do Hefesto must leave the suggestion list")
    return decision.proposal, decision.incremental_minutes
