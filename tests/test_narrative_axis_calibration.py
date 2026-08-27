from __future__ import annotations

from comunicamundo_v4.hefesto.axis_proposal import AxisProposalOrchestrator, validate_axis_statement
from comunicamundo_v4.hefesto.contracts import (
    EDITORIAL_UNDERSTANDING_PROMPT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    PlanningRunState,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.prompts import (
    NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT,
    NARRATIVE_AXIS_PROMPT,
    NARRATIVE_AXIS_STRENGTH_CALIBRATION,
    REVISE_AXIS_PROMPT,
)
from fakes import MemoryPlanningPersistence
from test_axis_proposal import RecordingProposer, RecordingSynthesizer, _axis_runtime, _brief, _prepare_graph_ready

_PREVIOUS_AXIS_PROMPT_VERSION = "hefesto.narrative-axis.v1"
_STRONG_FORM_EXAMPLES = (
    "the most",
    "the main",
    "the only",
    "always",
    "never",
    "causes",
    "determines",
)
_BANNED_WORD_FILTER_INSTRUCTIONS = (
    "never use \"the most\"",
    "do not use superlatives",
    "do not use the word",
    "banned words:",
    "forbidden words:",
)


def _prompt_text(prompt) -> str:
    template = getattr(prompt, "template", None)
    if isinstance(template, str) and template.strip():
        return template
    raise AssertionError("expected a PromptTemplate with a template string")


def _axis_prompt_texts() -> tuple[str, str, str]:
    return (
        _prompt_text(NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT),
        _prompt_text(NARRATIVE_AXIS_PROMPT),
        _prompt_text(REVISE_AXIS_PROMPT),
    )


def test_axis_prompts_require_strong_editorial_direction_not_summary_or_hedging() -> None:
    for text in _axis_prompt_texts():
        lowered = text.lower()
        assert "take a position" in lowered
        assert "strong editorial statement" in lowered
        assert "generic summary" in lowered
        assert "hedge soup" in lowered
        assert "strongest formulation that is warranted" in lowered
        assert "not the strongest wording available" in lowered
        assert "do not mechanically insert hedges" in lowered


def test_axis_prompts_permit_inference_and_synthesis() -> None:
    for text in _axis_prompt_texts():
        lowered = text.lower()
        assert "inference and synthesis are permitted" in lowered
        assert "editorial proposition that is not a literal sentence" in lowered
        assert "reasonable synthesis or inference from supported material" in lowered


def test_unsupported_evidence_upgrades_are_rejected_conceptually_not_by_word_filter() -> None:
    calibration = NARRATIVE_AXIS_STRENGTH_CALIBRATION.lower()
    assert "silently upgrading the evidence" in calibration
    assert "unsupported upgrade" in calibration
    assert "banned-word filtering" in calibration
    assert "not forbidden" in calibration
    for example in _STRONG_FORM_EXAMPLES:
        assert example in NARRATIVE_AXIS_STRENGTH_CALIBRATION
    for text in _axis_prompt_texts():
        lowered = text.lower()
        assert NARRATIVE_AXIS_STRENGTH_CALIBRATION in text
        assert "unsupported upgrade" in lowered
        for phrase in _BANNED_WORD_FILTER_INSTRUCTIONS:
            assert phrase not in lowered
    strong_form = "This is the most important exclusive mechanism that always determines the only outcome."
    assert validate_axis_statement(strong_form) == strong_form


def test_strong_forms_remain_allowed_when_the_understanding_warrants_them() -> None:
    calibration = NARRATIVE_AXIS_STRENGTH_CALIBRATION.lower()
    assert "remain allowed when the understanding actually warrants them" in calibration
    assert "superlatives" in calibration
    assert "rankings" in calibration
    assert "exclusivity" in calibration
    assert "universal quantifiers" in calibration
    assert "causal certainty" in calibration
    for text in _axis_prompt_texts():
        assert "remain allowed when the understanding actually warrants them" in text.lower()


def test_source_limits_and_nuances_constrain_the_axis() -> None:
    from_understanding = _prompt_text(NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT)
    assert "especially source_limits and nuances" in from_understanding
    assert "Those limits and nuances constrain the Axis." in from_understanding
    assert "pressure-test every load-bearing claim" in from_understanding
    assert "{source_limits}" in from_understanding
    assert "{nuances}" in from_understanding
    for text in (_prompt_text(NARRATIVE_AXIS_PROMPT), _prompt_text(REVISE_AXIS_PROMPT)):
        assert "pressure-test every load-bearing claim" in text
        assert "Those limits and nuances constrain the Axis." in text
    revise = _prompt_text(REVISE_AXIS_PROMPT)
    assert "User feedback must not turn revision into unsupported overclaiming." in revise


def test_axis_prompt_placeholders_remain_intact() -> None:
    from_understanding = _prompt_text(NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT).format(
        theme="theme",
        approach="approach",
        audience="audience",
        product="article",
        duration_minutes=8,
        output_language="en",
        synthesis="synthesis",
        central_ideas="idea",
        relations_and_tensions="tension",
        nuances="nuance",
        source_limits="limit",
        editorial_opportunities="opportunity",
    )
    assert "theme" in from_understanding
    assert "limit" in from_understanding
    legacy = _prompt_text(NARRATIVE_AXIS_PROMPT).format(
        theme="theme",
        approach="approach",
        audience="audience",
        product="article",
        duration_minutes=8,
        output_language="en",
        graph_context="understanding",
    )
    assert "understanding" in legacy
    revised = _prompt_text(REVISE_AXIS_PROMPT).format(
        current_axis="current",
        feedback="make it stronger",
        brief="brief",
        graph_context="understanding",
    )
    assert "make it stronger" in revised


def test_axis_prompt_version_bump_invalidates_only_the_old_axis() -> None:
    assert NARRATIVE_AXIS_PROMPT_VERSION != _PREVIOUS_AXIS_PROMPT_VERSION
    assert NARRATIVE_AXIS_PROMPT_VERSION.startswith("hefesto.narrative-axis.")
    assert EDITORIAL_UNDERSTANDING_PROMPT_VERSION == "hefesto.editorial-understanding.v1"

    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(
        store,
        "First source establishes a bounded mechanism.",
        "Second source notes a competing constraint.",
        brief=_brief(theme="Generic subject", approach="analytical", audience="specialists"),
    )
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    synth = RecordingSynthesizer()
    old_prop = RecordingProposer("A precise tension governs the piece. Consequence follows from that choice.")
    old = AxisProposalOrchestrator(
        store,
        _axis_runtime(),
        synth,
        old_prop,
        axis_prompt_version=_PREVIOUS_AXIS_PROMPT_VERSION,
    ).execute(run.planning_run_id)
    understanding_writes = store.understanding_writes
    understanding_id = old.editorial_understanding.understanding_id
    old_axis_id = old.narrative_axis.narrative_axis_id
    assert old.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert old.editorial_understanding.prompt_version == EDITORIAL_UNDERSTANDING_PROMPT_VERSION

    new_prop = RecordingProposer("A precise tension governs the piece. Consequence follows from that choice.")
    result = AxisProposalOrchestrator(store, _axis_runtime(), synth, new_prop).execute(run.planning_run_id)

    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.editorial_project_created is False
    assert result.editorial_understanding.understanding_id == understanding_id
    assert result.editorial_understanding.prompt_version == EDITORIAL_UNDERSTANDING_PROMPT_VERSION
    assert len(synth.materials) == 1
    assert store.understanding_writes == understanding_writes
    assert result.narrative_axis.narrative_axis_id != old_axis_id
    assert len(new_prop.understandings) == 1
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.prompt_version == NARRATIVE_AXIS_PROMPT_VERSION
    assert persisted.narrative_axis.narrative_axis_id == result.narrative_axis.narrative_axis_id
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY
    assert extractor.calls
    assert result.human_approval_required is True
