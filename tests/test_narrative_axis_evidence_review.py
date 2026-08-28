from __future__ import annotations

import re

import pytest

from comunicamundo_v4.hefesto.axis_proposal import (
    AxisProposalOrchestrator,
    LlamaIndexAxisEvidenceReviewer,
    LlamaIndexAxisProposer,
    apply_evidence_strength_review,
    validate_axis_statement,
)
from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime
from comunicamundo_v4.hefesto.contracts import (
    EDITORIAL_UNDERSTANDING_PROMPT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    EditorialUnderstandingProposal,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    PlanningRunState,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.prompts import NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT
from fakes import MemoryPlanningPersistence
from test_axis_proposal import (
    FailingReviewer,
    RecordingEvidenceReviewer,
    RecordingProposer,
    RecordingSynthesizer,
    UnderstandingMaterial,
    _axis_runtime,
    _prepare_graph_ready,
)

_PREVIOUS_AXIS_PROMPT_VERSION = "hefesto.narrative-axis.v2"
_RANKING_CANDIDATE = (
    "A tentativa de poupar a criança da dor da morte é o que, paradoxalmente, mais a impede de elaborar a perda."
)
_CAUSAL_CANDIDATE = "Honesty is the only mechanism that always determines whether a child can grieve."
_NEUTRAL_CANDIDATE = (
    "Children and adults cross loss together, and age-appropriate honesty can turn protection into shared presence."
)
_HEDGES = (" may ", " could ", " perhaps ", " maybe ")
_RANKING_MARKERS = (
    "the most",
    "the main",
    "the greatest",
    "o principal",
    "a principal",
    "o que mais",
    "mais a impede",
)
_EXCLUSIVE_MARKERS = ("the only", "o único", "a única", "exclusively")
_UNIVERSAL_MARKERS = ("always", "never", "sempre", "nunca")
_CAUSAL_MARKERS = ("causes", "determines", "causa", "determina")
_RANKING_REWRITES = (
    (r"o que,\s*paradoxalmente,\s*mais\s+", "o que, paradoxalmente, "),
    (r"\bo que mais\b", "o que"),
    (r"\bmais a impede\b", "a impede"),
    (r"\bthe most\b", ""),
    (r"\bthe main\b", "a"),
    (r"\bthe greatest\b", "a"),
    (r"\bo principal\b", "um"),
    (r"\ba principal\b", "uma"),
)
_EXCLUSIVE_REWRITES = (
    (r"\bthe only\b", "a"),
    (r"\bo único\b", "um"),
    (r"\ba única\b", "uma"),
    (r"\bexclusively\b", ""),
)
_UNIVERSAL_REWRITES = (
    (r"\balways\b", ""),
    (r"\bnever\b", ""),
    (r"\bsempre\b", ""),
    (r"\bnunca\b", ""),
)
_CAUSAL_REWRITES = (
    (r"\bcauses\b", "is bound up with"),
    (r"\bdetermines\b", "shapes"),
    (r"\bcausa\b", "está ligada a"),
    (r"\bdetermina\b", "dá forma a"),
)


def _prompt_text(prompt) -> str:
    template = getattr(prompt, "template", None)
    if isinstance(template, str) and template.strip():
        return template
    raise AssertionError("expected a PromptTemplate with a template string")


def _corpus(understanding) -> str:
    return " ".join(
        [
            understanding.synthesis,
            *understanding.central_ideas,
            *understanding.relations_and_tensions,
            *understanding.nuances,
            *understanding.source_limits,
            *understanding.editorial_opportunities,
        ]
    ).lower()


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(marker)}\b", lowered) is not None for marker in markers)


def _rewrite(text: str, rules: tuple[tuple[str, str], ...]) -> str:
    updated = text
    for pattern, replacement in rules:
        updated = re.sub(pattern, replacement, updated, flags=re.IGNORECASE)
    return " ".join(updated.split())


def review_candidate_against_understanding(candidate: str, understanding) -> NarrativeAxisEvidenceReview:
    evidence = _corpus(understanding)
    revised = candidate
    upgrades: list[str] = []
    if _has_marker(candidate, _RANKING_MARKERS) and not _has_marker(evidence, _RANKING_MARKERS):
        upgrades.append("unsupported ranking/comparison")
        revised = _rewrite(revised, _RANKING_REWRITES)
    if _has_marker(candidate, _EXCLUSIVE_MARKERS) and not _has_marker(evidence, _EXCLUSIVE_MARKERS):
        upgrades.append("unsupported exclusivity")
        revised = _rewrite(revised, _EXCLUSIVE_REWRITES)
    if _has_marker(candidate, _UNIVERSAL_MARKERS) and not _has_marker(evidence, _UNIVERSAL_MARKERS):
        upgrades.append("unsupported universality")
        revised = _rewrite(revised, _UNIVERSAL_REWRITES)
    if _has_marker(candidate, _CAUSAL_MARKERS) and not _has_marker(evidence, _CAUSAL_MARKERS):
        upgrades.append("unsupported causal certainty")
        revised = _rewrite(revised, _CAUSAL_REWRITES)
    if not upgrades:
        return NarrativeAxisEvidenceReview(
            candidate_statement=candidate,
            revision_required=False,
            unsupported_upgrades=[],
            final_statement=candidate,
        )
    return NarrativeAxisEvidenceReview(
        candidate_statement=candidate,
        revision_required=True,
        unsupported_upgrades=upgrades,
        final_statement=validate_axis_statement(revised),
    )


class IndependentEvidenceReviewer:
    def __init__(self) -> None:
        self.candidates: list[str] = []
        self.understandings = []
        self.reviews: list[NarrativeAxisEvidenceReview] = []

    def review(self, *, candidate: str, understanding) -> NarrativeAxisEvidenceReview:
        self.candidates.append(candidate)
        self.understandings.append(understanding)
        review = review_candidate_against_understanding(candidate, understanding)
        self.reviews.append(review)
        return review


class ScriptedSynthesizer:
    def __init__(self, proposal: EditorialUnderstandingProposal) -> None:
        self.materials: list[UnderstandingMaterial] = []
        self.proposal = proposal

    def synthesize(self, material: UnderstandingMaterial) -> EditorialUnderstandingProposal:
        self.materials.append(material)
        return self.proposal


class SequentialStructuredLlm:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def structured_predict(self, model, prompt, **kwargs):
        self.calls.append({"model": model, "prompt": prompt, **kwargs})
        value = self.responses.pop(0)
        assert isinstance(value, model)
        return value


def _concealment_understanding(*, ranking_warranted: bool = False) -> EditorialUnderstandingProposal:
    ranking_limit = (
        "Concealment is the most important obstacle and the main barrier to elaborating the loss."
        if ranking_warranted
        else "The sources do not assign this factor a rank among the others."
    )
    return EditorialUnderstandingProposal(
        central_ideas=[
            "Concealment and euphemisms around death are harmful for children.",
            "They are associated with complicated grief.",
        ],
        relations_and_tensions=[
            "Adults try to spare the child from the pain of death, and that concealment can impede mourning."
        ],
        nuances=["Honesty must fit the child's age."],
        source_limits=[ranking_limit],
        editorial_opportunities=["Make shared presence practicable without inventing a ranking."],
        synthesis=(
            "Concealment and euphemisms are harmful, counterproductive, and associated with complicated grief. "
            "They impede elaboration of the loss. "
            + (
                "This is the most important obstacle among the factors the sources discuss."
                if ranking_warranted
                else "The understanding does not assign this factor a rank among the others."
            )
        ),
    )


def _honesty_understanding(*, upgrades_warranted: bool = False) -> EditorialUnderstandingProposal:
    if upgrades_warranted:
        claim = "Honesty is the only mechanism that always determines whether a child can grieve."
        limits = "The sources establish this exclusive, universal causal claim."
    else:
        claim = "Honesty helps a child grieve and is bound up with shared presence."
        limits = "The sources do not establish exclusivity, universality, or causal certainty."
    return EditorialUnderstandingProposal(
        central_ideas=[claim],
        relations_and_tensions=["The adult-child relation carries the loss together."],
        nuances=["Honesty has to fit the child's age."],
        source_limits=[limits],
        editorial_opportunities=["Keep the editorial force of honesty without upgrading the evidence."],
        synthesis=f"{claim} {limits}",
    )


def _execute(store, run, *, synthesizer, proposer, reviewer=None, runtime=None, **kwargs):
    return AxisProposalOrchestrator(
        store,
        runtime or _axis_runtime(),
        synthesizer,
        proposer,
        reviewer if reviewer is not None else IndependentEvidenceReviewer(),
        **kwargs,
    ).execute(run.planning_run_id)


def test_unsupported_ranking_candidate_is_revised_while_preserving_tension() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "primeiro bloco.", "segundo bloco.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    synth = ScriptedSynthesizer(_concealment_understanding())
    reviewer = IndependentEvidenceReviewer()
    result = _execute(
        store,
        run,
        synthesizer=synth,
        proposer=RecordingProposer(_RANKING_CANDIDATE),
        reviewer=reviewer,
    )
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.editorial_project_created is False
    assert result.human_approval_required is True
    assert reviewer.candidates == [_RANKING_CANDIDATE]
    assert reviewer.reviews[0].revision_required is True
    assert "ranking" in " ".join(reviewer.reviews[0].unsupported_upgrades)
    assert result.proposed_axis != _RANKING_CANDIDATE
    assert "mais" not in result.proposed_axis.lower()
    lowered = result.proposed_axis.lower()
    assert "poupar" in lowered
    assert "impede" in lowered
    assert "elaborar a perda" in lowered
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.narrative_axis.statement == result.proposed_axis
    assert persisted.prompt_version == NARRATIVE_AXIS_PROMPT_VERSION
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY
    assert extractor.calls


def test_unsupported_causal_universal_exclusive_upgrade_is_revised() -> None:
    store = MemoryPlanningPersistence()
    run, _lineage, _extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    reviewer = IndependentEvidenceReviewer()
    result = _execute(
        store,
        run,
        synthesizer=ScriptedSynthesizer(_honesty_understanding()),
        proposer=RecordingProposer(_CAUSAL_CANDIDATE),
        reviewer=reviewer,
    )
    assert reviewer.reviews[0].revision_required is True
    reasons = " ".join(reviewer.reviews[0].unsupported_upgrades)
    assert "exclusivity" in reasons
    assert "universality" in reasons
    assert "causal" in reasons
    assert result.proposed_axis != _CAUSAL_CANDIDATE
    lowered = result.proposed_axis.lower()
    assert "honesty" in lowered
    assert "grieve" in lowered
    assert "the only" not in lowered
    assert "always" not in lowered
    assert "determines" not in lowered
    for hedge in _HEDGES:
        assert hedge not in f" {lowered} "


def test_warranted_superlative_causal_absolute_candidate_is_preserved() -> None:
    store = MemoryPlanningPersistence()
    run, _lineage, _extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    reviewer = IndependentEvidenceReviewer()
    ranking = _execute(
        store,
        run,
        synthesizer=ScriptedSynthesizer(_concealment_understanding(ranking_warranted=True)),
        proposer=RecordingProposer(_RANKING_CANDIDATE),
        reviewer=reviewer,
    )
    assert ranking.proposed_axis == _RANKING_CANDIDATE
    assert reviewer.reviews[0].revision_required is False

    store2 = MemoryPlanningPersistence()
    run2, _lineage2, _extractor2, _request2 = _prepare_graph_ready(store2, "one.", "two.")
    reviewer2 = IndependentEvidenceReviewer()
    causal = _execute(
        store2,
        run2,
        synthesizer=ScriptedSynthesizer(_honesty_understanding(upgrades_warranted=True)),
        proposer=RecordingProposer(_CAUSAL_CANDIDATE),
        reviewer=reviewer2,
    )
    assert causal.proposed_axis == _CAUSAL_CANDIDATE
    assert reviewer2.reviews[0].revision_required is False


def test_neutral_candidate_is_not_made_timid_by_the_reviewer() -> None:
    store = MemoryPlanningPersistence()
    run, _lineage, _extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    reviewer = IndependentEvidenceReviewer()
    result = _execute(
        store,
        run,
        synthesizer=RecordingSynthesizer(),
        proposer=RecordingProposer(_NEUTRAL_CANDIDATE),
        reviewer=reviewer,
    )
    assert result.proposed_axis == _NEUTRAL_CANDIDATE
    assert reviewer.reviews[0].revision_required is False
    lowered = f" {result.proposed_axis.lower()} "
    for hedge in _HEDGES:
        assert hedge not in lowered
    timid_rewrite = "Children and adults may perhaps cross loss together, and honesty could turn protection into presence."
    preserved = apply_evidence_strength_review(
        _NEUTRAL_CANDIDATE,
        NarrativeAxisEvidenceReview(
            candidate_statement=_NEUTRAL_CANDIDATE,
            revision_required=False,
            unsupported_upgrades=[],
            final_statement=timid_rewrite,
        ),
    )
    assert preserved == _NEUTRAL_CANDIDATE


def test_review_failure_leaves_understanding_reusable_below_axis_proposed() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    synth = RecordingSynthesizer()
    with pytest.raises(RuntimeError, match="review failed"):
        _execute(store, run, synthesizer=synth, proposer=RecordingProposer(), reviewer=FailingReviewer())
    updated = store.get_run(run.planning_run_id)
    assert updated.state is PlanningRunState.UNDERSTANDING_READY
    assert updated.state is not PlanningRunState.AXIS_PROPOSED
    assert updated.artifacts.understanding_uri is not None
    assert updated.artifacts.narrative_axis_id is None
    stored = store.load_understanding(updated.artifacts.understanding_uri)
    assert stored.synthesis
    assert len(synth.materials) == 1
    assert store.understanding_writes == 1
    assert store.axis_writes == 0
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY
    assert extractor.calls

    reviewer = IndependentEvidenceReviewer()
    result = _execute(store, updated, synthesizer=synth, proposer=RecordingProposer(), reviewer=reviewer)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.editorial_understanding.understanding_id == stored.understanding_id
    assert len(synth.materials) == 1
    assert store.understanding_writes == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes


def test_v2_persisted_axis_is_invalidated_while_understanding_is_reused() -> None:
    assert NARRATIVE_AXIS_PROMPT_VERSION == "hefesto.narrative-axis.v3"
    assert EDITORIAL_UNDERSTANDING_PROMPT_VERSION == "hefesto.editorial-understanding.v1"
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    synth = RecordingSynthesizer()
    old = _execute(
        store,
        run,
        synthesizer=synth,
        proposer=RecordingProposer(_NEUTRAL_CANDIDATE),
        reviewer=RecordingEvidenceReviewer(),
        axis_prompt_version=_PREVIOUS_AXIS_PROMPT_VERSION,
    )
    understanding_writes = store.understanding_writes
    understanding_id = old.editorial_understanding.understanding_id
    old_axis_id = old.narrative_axis.narrative_axis_id
    reviewer = IndependentEvidenceReviewer()
    result = _execute(
        store,
        old.planning_run,
        synthesizer=synth,
        proposer=RecordingProposer(_NEUTRAL_CANDIDATE),
        reviewer=reviewer,
    )
    assert result.editorial_understanding.understanding_id == understanding_id
    assert result.editorial_understanding.prompt_version == EDITORIAL_UNDERSTANDING_PROMPT_VERSION
    assert result.narrative_axis.narrative_axis_id != old_axis_id
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.editorial_project_created is False
    assert len(synth.materials) == 1
    assert store.understanding_writes == understanding_writes
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.prompt_version == NARRATIVE_AXIS_PROMPT_VERSION
    assert len(reviewer.candidates) == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY
    assert extractor.calls


def test_two_pass_uses_existing_axis_llm_and_does_not_rebuild_graph_or_project() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "one.", "two.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    revised = "A tentativa de poupar a criança da dor da morte é o que, paradoxalmente, a impede de elaborar a perda."
    axis_llm = SequentialStructuredLlm(
        [
            NarrativeAxisProposal(statement=_RANKING_CANDIDATE),
            NarrativeAxisEvidenceReview(
                candidate_statement=_RANKING_CANDIDATE,
                revision_required=True,
                unsupported_upgrades=["unsupported ranking/comparison"],
                final_statement=revised,
            ),
        ]
    )
    base = _axis_runtime()
    runtime = AxisProposalRuntime(
        embed_model=base.embed_model,
        understanding_llm=base.understanding_llm,
        understanding_model=base.understanding_model,
        understanding_max_tokens=base.understanding_max_tokens,
        axis_llm=axis_llm,
        axis_model=base.axis_model,
        axis_max_tokens=base.axis_max_tokens,
        context_window=base.context_window,
    )
    result = AxisProposalOrchestrator(
        store,
        runtime,
        RecordingSynthesizer(),
        LlamaIndexAxisProposer(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
    ).execute(run.planning_run_id)
    assert result.proposed_axis == revised
    assert result.proposed_axis != _RANKING_CANDIDATE
    assert result.editorial_project_created is False
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert len(axis_llm.calls) == 2
    assert axis_llm.calls[0]["model"] is NarrativeAxisProposal
    assert axis_llm.calls[1]["model"] is NarrativeAxisEvidenceReview
    assert axis_llm.calls[1]["prompt"] is NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT
    assert axis_llm.calls[1]["candidate"] == _RANKING_CANDIDATE
    for field in (
        "synthesis",
        "central_ideas",
        "relations_and_tensions",
        "nuances",
        "source_limits",
        "editorial_opportunities",
    ):
        assert field in axis_llm.calls[0]
        assert field in axis_llm.calls[1]
        assert axis_llm.calls[0][field] == axis_llm.calls[1][field]
    assert "graph_entities" not in axis_llm.calls[1]
    assert "source_chunks" not in axis_llm.calls[1]
    review_prompt = _prompt_text(NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT).lower()
    assert "not a second writer" in review_prompt
    assert "not a generic style critic" in review_prompt
    assert "do not weaken warranted strong statements" in review_prompt
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    assert store.get_axis(result.narrative_axis.narrative_axis_id).editorial_project_id is None
