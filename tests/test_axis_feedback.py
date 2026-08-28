from __future__ import annotations

from pathlib import Path

import pytest

from comunicamundo_v4.hefesto.axis_feedback import (
    AxisFeedbackOrchestrator,
    LlamaIndexAxisFeedbackDecider,
    LlamaIndexAxisFeedbackReviser,
    LlamaIndexBoundedRecalibrator,
)
from comunicamundo_v4.hefesto.axis_proposal import LlamaIndexAxisEvidenceReviewer
from comunicamundo_v4.hefesto.contracts import (
    AXIS_FEEDBACK_CONTRACT_VERSION,
    AXIS_FEEDBACK_PROMPT_VERSION,
    AxisAction,
    AxisFeedbackDecisionProposal,
    AxisFeedbackDecisionState,
    AxisFeedbackReviewResult,
    BoundedRecalibrationProposal,
    EditorialImpactKind,
    EnrichmentResolutionChoice,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    PlanningRunState,
    SupportKind,
    TreatmentScale,
)
from comunicamundo_v4.hefesto.prompts import (
    AXIS_FEEDBACK_BOUNDED_RECALIBRATION_PROMPT,
    AXIS_FEEDBACK_DECISION_PROMPT,
    AXIS_FEEDBACK_REVISION_PROMPT,
)
from comunicamundo_v4.hefesto.prompts import NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT
from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime
from fakes import MemoryPlanningPersistence
from test_axis_proposal import (
    RecordingEvidenceReviewer,
    RecordingSynthesizer,
    _axis_runtime,
    _gcp_persistence,
    _prepare_graph_ready,
    _propose,
)
from test_narrative_axis_evidence_review import IndependentEvidenceReviewer, SequentialStructuredLlm

FEEDBACK_PY = Path("src/comunicamundo_v4/hefesto/axis_feedback.py").read_text()
WORKER_PY = Path("src/comunicamundo_v4/hefesto/worker.py").read_text()
PROMPTS_PY = Path("src/comunicamundo_v4/hefesto/prompts.py").read_text()
CANONICAL_FEEDBACK = "Não quero que esqueça que o adulto responsável pela criança também está vivendo o próprio luto."

COMPLEMENT_FEEDBACK = "Also keep in view that recovery needs ordinary rest, not only the central practice."
QUALIFY_FEEDBACK = "The existing practice only holds if it is interpreted as a shared relation, not a solo technique."
REFORMULATE_FEEDBACK = "The load-bearing tension is not concealment versus honesty; it is whether the work is done in relation or in isolation."
MODEL_FEEDBACK = "Generally known attention-and-sleep coupling should shape how the piece talks about daily capacity."
ENRICHMENT_FEEDBACK = "Include the unpublished 2024 longitudinal ranking of outcomes across the specialized cohort."
REVISED_QUALIFYING = "The practice only holds as a shared relation, and age-fit honesty turns protection into presence."
REVISED_REFORMULATING = "The story is worth telling through the tension between doing this work in relation and doing it alone."
BOUNDED_REVISED = "The piece can name the adult's own grief as a bound on what the current sources support."


class RecordingDecider:
    def __init__(self, proposal: AxisFeedbackDecisionProposal) -> None:
        self.proposal = proposal
        self.calls: list[dict] = []

    def decide(self, **kwargs):
        self.calls.append(kwargs)
        return self.proposal


class RecordingReviser:
    def __init__(self, statement: str) -> None:
        self.statement = statement
        self.calls: list[dict] = []

    def revise(self, **kwargs) -> NarrativeAxisProposal:
        self.calls.append(kwargs)
        return NarrativeAxisProposal(statement=self.statement)


class RecordingRecalibrator:
    def __init__(self, proposal: BoundedRecalibrationProposal) -> None:
        self.proposal = proposal
        self.calls: list[dict] = []

    def recalibrate(self, **kwargs) -> BoundedRecalibrationProposal:
        self.calls.append(kwargs)
        return self.proposal


class FailingDecider:
    def decide(self, **kwargs):
        raise RuntimeError("decision failed")


def _decision_proposal(**overrides) -> AxisFeedbackDecisionProposal:
    payload = {
        "impact": EditorialImpactKind.COMPLEMENTS,
        "affected_premises": ["The Axis already covers the central practice."],
        "editorial_consequences": ["The extra material can sit beside the Axis without rewriting it."],
        "impact_rationale": "The contribution adds useful adjacent material without moving a load-bearing premise.",
        "support": SupportKind.SOURCES,
        "support_rationale": "The persisted understanding already supports this adjacent material from the sources.",
        "axis_action": AxisAction.KEEP,
        "treatment_scale": TreatmentScale.BRIEF,
        "treatment_rationale": "A short later mention is enough; the Axis need not grow.",
        "decision_rationale": "Keep the Axis and carry the contribution forward at a brief scale.",
        "carry_forward_intent": "Later, mention ordinary rest as a brief supporting note beside the central practice.",
        **overrides,
    }
    return AxisFeedbackDecisionProposal.model_validate(payload)


def _bounded_proposal(**overrides) -> BoundedRecalibrationProposal:
    payload = {
        "support": SupportKind.MODEL,
        "support_rationale": "Without new sources, only a bounded model-safe mention is responsible.",
        "axis_action": AxisAction.KEEP,
        "treatment_scale": TreatmentScale.BRIEF,
        "treatment_rationale": "Importance does not justify a section; one later sentence is the honest scale.",
        "decision_rationale": "Keep the Axis and carry the intention forward as a brief disclaimer.",
        "carry_forward_intent": "Preserve the user's intention as a brief later caveat, not as a sourced claim.",
        **overrides,
    }
    return BoundedRecalibrationProposal.model_validate(payload)


def _orchestrator(store, *, decider, reviser=None, reviewer=None, recalibrator=None, runtime=None):
    return AxisFeedbackOrchestrator(
        store,
        runtime or _axis_runtime(),
        decider,
        reviser if reviser is not None else RecordingReviser(REVISED_QUALIFYING),
        reviewer if reviewer is not None else RecordingEvidenceReviewer(),
        recalibrator if recalibrator is not None else RecordingRecalibrator(_bounded_proposal()),
    )


def _ready(store: MemoryPlanningPersistence | None = None):
    store = store if store is not None else MemoryPlanningPersistence()
    run, lineage, extractor, request = _prepare_graph_ready(store, "primeiro bloco.", "segundo bloco.")
    proposed, synth, prop = _propose(store, run)
    return store, proposed, lineage, extractor, request, synth, prop


def _prompt_text(prompt) -> str:
    template = getattr(prompt, "template", None)
    if isinstance(template, str) and template.strip():
        return template
    raise AssertionError("expected a PromptTemplate with a template string")


def test_feedback_module_does_not_search_rebuild_or_create_project() -> None:
    forbidden = (
        "SimpleLLMPathExtractor",
        "from_documents",
        "build_vertex_runtime",
        "build_graph_extraction_runtime",
        "LlamaIndexChunkGraphExtractor",
        "GraphExtractionOrchestrator",
        "GraphAssemblyOrchestrator",
        "restore_source_lineage_graph",
        "build_project",
        "EditorialProject(",
        "save_project",
        ".approve(",
        "web_search",
        "Google Search",
        "retrieve(",
    )
    for token in forbidden:
        assert token not in FEEDBACK_PY
    assert "review-axis-feedback" in WORKER_PY
    assert "resolve-axis-feedback" in WORKER_PY
    assert "--planning-run-id" in WORKER_PY
    assert "LlamaIndexAxisEvidenceReviewer" in FEEDBACK_PY or "NarrativeAxisEvidenceReviewer" in FEEDBACK_PY
    assert "build_axis_proposal_runtime" in WORKER_PY
    assert CANONICAL_FEEDBACK not in FEEDBACK_PY
    assert CANONICAL_FEEDBACK not in PROMPTS_PY
    assert "adulto responsável pela criança" not in PROMPTS_PY


def test_feedback_prompts_require_impact_support_and_independent_treatment() -> None:
    decision = _prompt_text(AXIS_FEEDBACK_DECISION_PROMPT).lower()
    revision = _prompt_text(AXIS_FEEDBACK_REVISION_PROMPT).lower()
    bounded = _prompt_text(AXIS_FEEDBACK_BOUNDED_RECALIBRATION_PROMPT).lower()
    for text in (decision, revision, bounded):
        assert "do not search" in text
        assert "do not create an editorial project" in text or "no editorial project" in text
    assert "complements" in decision
    assert "qualifies" in decision
    assert "reformulates" in decision
    assert "not keywords" in decision
    assert "structural impact and textual space are independent" in decision
    assert "sources" in decision and "enrichment" in decision
    assert "never masquerade as source evidence" in decision
    assert "as automatically sufficient" in decision
    assert "not automatically another subject" in decision
    assert "hold_for_enrichment" in decision
    assert "treatment_scale" in decision
    assert "refused additional sources" in bounded
    assert "do not erase the user's intention" in bounded
    assert "do not fabricate support" in bounded
    assert "importance does not imply section length" in bounded


def test_complement_with_source_support_keeps_axis_and_carries_forward() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    axis_writes = store.axis_writes
    axis_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(_decision_proposal())
    reviser = RecordingReviser(REVISED_QUALIFYING)
    reviewer = RecordingEvidenceReviewer()
    result = _orchestrator(store, decider=decider, reviser=reviser, reviewer=reviewer).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.stopped_at == "axis_proposed"
    assert result.narrative_axis.narrative_axis_id == axis_id
    assert result.narrative_axis.statement == proposed.proposed_axis
    assert result.decision.impact is EditorialImpactKind.COMPLEMENTS
    assert result.decision.support is SupportKind.SOURCES
    assert result.decision.axis_action is AxisAction.KEEP
    assert result.decision.carry_forward is not None
    assert result.decision.resulting_narrative_axis_id is None
    assert result.editorial_project_created is False
    assert result.search_performed is False
    assert result.human_approval_required is True
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert len(decider.calls) == 1
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert len(synth.materials) == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
    assert store.axis_writes == axis_writes
    assert store.feedback_writes == 1
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    stored_axis = store.get_axis(axis_id)
    assert stored_axis.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert stored_axis.editorial_project_id is None


def test_qualifying_feedback_can_revise_axis() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    old_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.QUALIFIES,
            axis_action=AxisAction.REVISE,
            treatment_scale=TreatmentScale.INTEGRATED,
            carry_forward_intent=None,
            impact_rationale="The contribution changes how the existing practice must be applied.",
            decision_rationale="Revise the Axis so the practice is read as relational.",
        )
    )
    reviewer = RecordingEvidenceReviewer()
    result = _orchestrator(
        store, decider=decider, reviser=RecordingReviser(REVISED_QUALIFYING), reviewer=reviewer
    ).review(proposed.planning_run.planning_run_id, QUALIFY_FEEDBACK)
    assert result.decision.impact is EditorialImpactKind.QUALIFIES
    assert result.decision.axis_action is AxisAction.REVISE
    assert result.narrative_axis.narrative_axis_id != old_id
    assert result.narrative_axis.parent_axis_id == old_id
    assert result.narrative_axis.statement == REVISED_QUALIFYING
    assert result.decision.resulting_narrative_axis_id == result.narrative_axis.narrative_axis_id
    assert result.planning_run.artifacts.narrative_axis_id == result.narrative_axis.narrative_axis_id
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert store.get_axis(old_id).narrative_axis.status is NarrativeAxisStatus.SUPERSEDED
    assert store.get_axis(result.narrative_axis.narrative_axis_id).editorial_project_id is None
    assert reviewer.candidates == [REVISED_QUALIFYING]


def test_reformulating_feedback_can_revise_axis() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    old_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.REFORMULATES,
            axis_action=AxisAction.REVISE,
            treatment_scale=TreatmentScale.INTEGRATED,
            carry_forward_intent=None,
            impact_rationale="A load-bearing tension of the Axis must be reconsidered.",
            decision_rationale="Revise the Axis around the new load-bearing tension.",
        )
    )
    result = _orchestrator(store, decider=decider, reviser=RecordingReviser(REVISED_REFORMULATING)).review(
        proposed.planning_run.planning_run_id, REFORMULATE_FEEDBACK
    )
    assert result.decision.impact is EditorialImpactKind.REFORMULATES
    assert result.decision.axis_action is AxisAction.REVISE
    assert result.narrative_axis.narrative_axis_id != old_id
    assert result.narrative_axis.statement == REVISED_REFORMULATING
    assert result.stopped_at == "axis_proposed"
    assert result.editorial_project_created is False


def test_model_supported_contribution_is_distinct_from_source_support() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    rationale = (
        "The sources do not cover this coupling; stable model knowledge is enough for a cautious editorial mention, "
        "and it must not be presented as source evidence."
    )
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.COMPLEMENTS,
            support=SupportKind.MODEL,
            support_rationale=rationale,
            treatment_scale=TreatmentScale.BRIEF,
        )
    )
    result = _orchestrator(store, decider=decider).review(proposed.planning_run.planning_run_id, MODEL_FEEDBACK)
    assert result.decision.support is SupportKind.MODEL
    assert result.decision.support is not SupportKind.SOURCES
    assert "not be presented as source evidence" in result.decision.support_rationale
    assert result.decision.axis_action is AxisAction.KEEP
    assert result.narrative_axis.narrative_axis_id == proposed.narrative_axis.narrative_axis_id
    assert result.search_performed is False


def test_enrichment_required_does_not_revise_or_search_or_create_project() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    axis_writes = store.axis_writes
    old_id = proposed.narrative_axis.narrative_axis_id
    old_statement = proposed.proposed_axis
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.QUALIFIES,
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            treatment_scale=TreatmentScale.SUBSTANTIAL,
            carry_forward_intent=None,
            enrichment_need="Responsible use needs the unpublished longitudinal ranking and its methods.",
            support_rationale="Current sources and safe model knowledge cannot sustain that specialized ranking.",
            decision_rationale="Hold the Axis until the user chooses whether to seek that evidence.",
        )
    )
    reviser = RecordingReviser(REVISED_QUALIFYING)
    reviewer = RecordingEvidenceReviewer()
    recalibrator = RecordingRecalibrator(_bounded_proposal())
    result = _orchestrator(
        store, decider=decider, reviser=reviser, reviewer=reviewer, recalibrator=recalibrator
    ).review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    assert result.decision.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED
    assert result.stopped_at == "enrichment_required"
    assert result.research_needed is False
    assert result.narrative_axis.narrative_axis_id == old_id
    assert result.narrative_axis.statement == old_statement
    assert result.decision.resulting_narrative_axis_id is None
    assert result.decision.enrichment_need
    assert result.search_performed is False
    assert result.editorial_project_created is False
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert recalibrator.calls == []
    assert store.axis_writes == axis_writes
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
    assert len(synth.materials) == 1
    assert store.get_axis(old_id).narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    persisted = store.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert persisted.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED


def test_declining_enrichment_can_keep_axis_and_bound_treatment() -> None:
    store, proposed, _lineage, _extractor, _request, synth, _prop = _ready()
    old_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.REFORMULATES,
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            treatment_scale=TreatmentScale.SUBSTANTIAL,
            carry_forward_intent=None,
            enrichment_need="The load-bearing claim needs a source that the current corpus does not provide.",
            support_rationale="Neither sources nor safe model knowledge can sustain the requested precision.",
            decision_rationale="Hold pending enrichment.",
        )
    )
    orchestrator = _orchestrator(
        store,
        decider=decider,
        reviser=RecordingReviser(BOUNDED_REVISED),
        reviewer=RecordingEvidenceReviewer(),
        recalibrator=RecordingRecalibrator(_bounded_proposal()),
    )
    held = orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    understanding_writes = store.understanding_writes
    extract_writes = store.extraction_writes
    result = orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.DECLINE)
    assert result.decision.feedback_decision_id == held.decision.feedback_decision_id
    assert result.decision.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED
    assert result.decision.user_resolution is EnrichmentResolutionChoice.DECLINE
    assert result.decision.impact is EditorialImpactKind.REFORMULATES
    assert result.decision.support is SupportKind.MODEL
    assert result.decision.axis_action is AxisAction.KEEP
    assert result.decision.treatment_scale is TreatmentScale.BRIEF
    assert result.decision.carry_forward is not None
    assert "Preserve the user's intention" in result.decision.carry_forward.intent
    assert result.narrative_axis.narrative_axis_id == old_id
    assert result.stopped_at == "axis_proposed"
    assert result.search_performed is False
    assert result.editorial_project_created is False
    assert result.research_needed is False
    assert result.decision.enrichment_need
    assert store.understanding_writes == understanding_writes
    assert store.extraction_writes == extract_writes
    assert len(synth.materials) == 1
    assert store.get_axis(old_id).narrative_axis.status is NarrativeAxisStatus.PROPOSED


def test_declining_enrichment_can_revise_when_bounded_support_still_warrants_it() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    old_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.QUALIFIES,
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            treatment_scale=TreatmentScale.SUBSTANTIAL,
            carry_forward_intent=None,
            enrichment_need="Specialized evidence is missing for a full treatment.",
            support_rationale="Current material cannot sustain a substantial treatment.",
            decision_rationale="Hold pending enrichment.",
        )
    )
    reviewer = RecordingEvidenceReviewer()
    recalibrator = RecordingRecalibrator(
        _bounded_proposal(
            support=SupportKind.SOURCES,
            support_rationale="At a bounded level the current sources still warrant a modest Axis revision.",
            axis_action=AxisAction.REVISE,
            treatment_scale=TreatmentScale.BRIEF,
            carry_forward_intent=None,
            decision_rationale="Revise the Axis only as far as current sources can sustain.",
        )
    )
    orchestrator = _orchestrator(
        store,
        decider=decider,
        reviser=RecordingReviser(BOUNDED_REVISED),
        reviewer=reviewer,
        recalibrator=recalibrator,
    )
    orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    result = orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.DECLINE)
    assert result.decision.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED
    assert result.decision.axis_action is AxisAction.REVISE
    assert result.decision.support is SupportKind.SOURCES
    assert result.decision.treatment_scale is TreatmentScale.BRIEF
    assert result.narrative_axis.narrative_axis_id != old_id
    assert result.narrative_axis.statement == BOUNDED_REVISED
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert reviewer.candidates == [BOUNDED_REVISED]
    assert store.get_axis(old_id).narrative_axis.status is NarrativeAxisStatus.SUPERSEDED
    assert result.stopped_at == "axis_proposed"
    assert result.search_performed is False


def test_accepting_enrichment_persists_research_needed_and_stops_without_search() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    old_id = proposed.narrative_axis.narrative_axis_id
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.QUALIFIES,
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            carry_forward_intent=None,
            enrichment_need="Additional reliable sources are required before this claim can be used.",
            support_rationale="Current understanding cannot sustain the contribution.",
            decision_rationale="Hold pending enrichment.",
        )
    )
    reviser = RecordingReviser(REVISED_QUALIFYING)
    reviewer = RecordingEvidenceReviewer()
    recalibrator = RecordingRecalibrator(_bounded_proposal())
    orchestrator = _orchestrator(
        store, decider=decider, reviser=reviser, reviewer=reviewer, recalibrator=recalibrator
    )
    orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    result = orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.ACCEPT)
    assert result.decision.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED
    assert result.decision.user_resolution is EnrichmentResolutionChoice.ACCEPT
    assert result.research_needed is True
    assert result.stopped_at == "enrichment_required"
    assert result.search_performed is False
    assert result.editorial_project_created is False
    assert result.narrative_axis.narrative_axis_id == old_id
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert recalibrator.calls == []
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert len(synth.materials) == 1
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    assert store.get_axis(old_id).editorial_project_id is None


def test_revised_axis_always_goes_through_v3_evidence_strength_review() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.QUALIFIES,
            axis_action=AxisAction.REVISE,
            carry_forward_intent=None,
            decision_rationale="Revise now.",
        )
    )
    reviewer = IndependentEvidenceReviewer()
    result = _orchestrator(
        store, decider=decider, reviser=RecordingReviser(REVISED_QUALIFYING), reviewer=reviewer
    ).review(proposed.planning_run.planning_run_id, QUALIFY_FEEDBACK)
    assert reviewer.candidates == [REVISED_QUALIFYING]
    assert reviewer.reviews
    assert result.narrative_axis.statement == REVISED_QUALIFYING
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.prompt_version == "hefesto.narrative-axis.v3"


def test_graph_and_understanding_are_reused_without_regeneration() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    understanding_id = proposed.editorial_understanding.understanding_id
    restore_calls = {"n": 0}
    original_restore = store.restore_source_lineage_graph

    def counting_restore(*args, **kwargs):
        restore_calls["n"] += 1
        return original_restore(*args, **kwargs)

    store.restore_source_lineage_graph = counting_restore  # type: ignore[method-assign]
    _orchestrator(store, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    assert restore_calls["n"] == 0
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
    assert store.get_run(proposed.planning_run.planning_run_id).artifacts.understanding_uri == (
        proposed.planning_run.artifacts.understanding_uri
    )
    assert store.load_understanding(proposed.planning_run.artifacts.understanding_uri).understanding_id == understanding_id
    assert store.get_source_lineage(lineage.source_lineage_id).state.value == "graph_ready"
    assert len(synth.materials) == 1
    assert extractor.calls


def test_structural_impact_and_treatment_scale_can_differ() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    decider = RecordingDecider(
        _decision_proposal(
            impact=EditorialImpactKind.REFORMULATES,
            axis_action=AxisAction.REVISE,
            treatment_scale=TreatmentScale.BRIEF,
            carry_forward_intent=None,
            impact_rationale="A load-bearing premise changes.",
            treatment_rationale="The later text only needs a brief disclaimer, not a section.",
            decision_rationale="Revise the Axis; keep later treatment brief.",
        )
    )
    result = _orchestrator(store, decider=decider, reviser=RecordingReviser(REVISED_REFORMULATING)).review(
        proposed.planning_run.planning_run_id, REFORMULATE_FEEDBACK
    )
    assert result.decision.impact is EditorialImpactKind.REFORMULATES
    assert result.decision.treatment_scale is TreatmentScale.BRIEF
    assert result.decision.axis_action is AxisAction.REVISE


def test_no_auto_approval_and_no_editorial_project() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    result = _orchestrator(store, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    assert result.human_approval_required is True
    assert result.editorial_project_created is False
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert store.get_axis(result.narrative_axis.narrative_axis_id).editorial_project_id is None
    with pytest.raises(ValueError, match="human approval"):
        AxisFeedbackReviewResult(
            planning_run=result.planning_run,
            editorial_understanding=result.editorial_understanding,
            narrative_axis=result.narrative_axis,
            decision=result.decision,
            proposed_axis=result.proposed_axis,
            human_approval_required=False,
            editorial_project_created=False,
            search_performed=False,
            stopped_at="axis_proposed",
        )


def test_identical_review_is_idempotent() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    decider = RecordingDecider(_decision_proposal())
    reviser = RecordingReviser(REVISED_QUALIFYING)
    reviewer = RecordingEvidenceReviewer()
    orchestrator = _orchestrator(store, decider=decider, reviser=reviser, reviewer=reviewer)
    first = orchestrator.review(proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK)
    writes = store.feedback_writes
    second = orchestrator.review(proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK)
    assert second.decision.feedback_decision_id == first.decision.feedback_decision_id
    assert len(decider.calls) == 1
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert store.feedback_writes == writes


def test_identical_accept_and_decline_resolutions_are_idempotent() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    decider = RecordingDecider(
        _decision_proposal(
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            carry_forward_intent=None,
            enrichment_need="Need a source we do not have.",
            support_rationale="Insufficient current support.",
            decision_rationale="Hold.",
        )
    )
    recalibrator = RecordingRecalibrator(_bounded_proposal())
    orchestrator = _orchestrator(store, decider=decider, recalibrator=recalibrator)
    orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    first = orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.ACCEPT)
    second = orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.ACCEPT)
    assert second.decision.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED
    assert second.decision.feedback_decision_id == first.decision.feedback_decision_id
    assert recalibrator.calls == []

    store2, proposed2, *_rest = _ready()
    orchestrator2 = _orchestrator(
        store2,
        decider=RecordingDecider(
            _decision_proposal(
                support=SupportKind.ENRICHMENT,
                axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
                carry_forward_intent=None,
                enrichment_need="Need a source we do not have.",
                support_rationale="Insufficient current support.",
                decision_rationale="Hold.",
            )
        ),
        recalibrator=RecordingRecalibrator(_bounded_proposal()),
    )
    orchestrator2.review(proposed2.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    declined = orchestrator2.resolve(proposed2.planning_run.planning_run_id, EnrichmentResolutionChoice.DECLINE)
    again = orchestrator2.resolve(proposed2.planning_run.planning_run_id, EnrichmentResolutionChoice.DECLINE)
    assert again.decision.feedback_decision_id == declined.decision.feedback_decision_id
    assert again.decision.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED


def test_decision_failure_keeps_axis_proposed_and_does_not_write_feedback() -> None:
    store, proposed, lineage, _extractor, _request, synth, _prop = _ready()
    with pytest.raises(RuntimeError, match="decision failed"):
        _orchestrator(store, decider=FailingDecider()).review(
            proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
        )
    updated = store.get_run(proposed.planning_run.planning_run_id)
    assert updated.state is PlanningRunState.AXIS_PROPOSED
    assert updated.artifacts.narrative_axis_id == proposed.narrative_axis.narrative_axis_id
    assert updated.artifacts.axis_feedback_decision_uri is None
    assert store.feedback_writes == 0
    assert store.get_source_lineage(lineage.source_lineage_id).state.value == "graph_ready"
    assert len(synth.materials) == 1


def test_structured_passes_use_existing_axis_llm_and_v3_reviewer() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    proposal = _decision_proposal(
        impact=EditorialImpactKind.QUALIFIES,
        axis_action=AxisAction.REVISE,
        carry_forward_intent=None,
        decision_rationale="Revise now.",
    )
    axis_llm = SequentialStructuredLlm(
        [
            proposal,
            NarrativeAxisProposal(statement=REVISED_QUALIFYING),
            NarrativeAxisEvidenceReview(
                candidate_statement=REVISED_QUALIFYING,
                revision_required=False,
                unsupported_upgrades=[],
                final_statement=REVISED_QUALIFYING,
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
    result = AxisFeedbackOrchestrator(
        store,
        runtime,
        LlamaIndexAxisFeedbackDecider(runtime),
        LlamaIndexAxisFeedbackReviser(runtime),
        LlamaIndexAxisEvidenceReviewer(runtime),
        LlamaIndexBoundedRecalibrator(runtime),
    ).review(proposed.planning_run.planning_run_id, QUALIFY_FEEDBACK)
    assert result.narrative_axis.statement == REVISED_QUALIFYING
    assert len(axis_llm.calls) == 3
    assert axis_llm.calls[0]["prompt"] is AXIS_FEEDBACK_DECISION_PROMPT
    assert axis_llm.calls[1]["prompt"] is AXIS_FEEDBACK_REVISION_PROMPT
    assert axis_llm.calls[2]["prompt"] is NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT
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
        assert field in axis_llm.calls[2]
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert len(synth.materials) == 1
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    assert result.decision.contract_version == AXIS_FEEDBACK_CONTRACT_VERSION
    assert result.decision.prompt_version == AXIS_FEEDBACK_PROMPT_VERSION


def test_gcp_persists_feedback_under_the_planning_run() -> None:
    persistence, storage, firestore = _gcp_persistence()
    store_run, lineage, extractor, _request = _prepare_graph_ready(
        persistence, "A criança percebe a ausência.", "O adulto acompanha."
    )
    proposed, _synth, _prop = _propose(persistence, store_run)
    result = _orchestrator(persistence, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    names = list(storage.bucket("test-bucket").blobs)
    assert any("/planning/" in name and "/feedback/" in name and name.endswith(".json") for name in names)
    loaded = persistence.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert loaded.feedback_decision_id == result.decision.feedback_decision_id
    assert loaded.planning_run_id == proposed.planning_run.planning_run_id
    project_docs = [key for key in firestore._store if key[0] == "hefesto_v4_rebuild_projects"]
    assert project_docs == []
    assert persistence.get_axis(result.narrative_axis.narrative_axis_id).editorial_project_id is None
    assert extractor.calls
    assert lineage.graph_artifact_id is not None


def test_new_feedback_is_blocked_while_enrichment_is_pending() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    orchestrator = _orchestrator(
        store,
        decider=RecordingDecider(
            _decision_proposal(
                support=SupportKind.ENRICHMENT,
                axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
                carry_forward_intent=None,
                enrichment_need="Need more evidence.",
                support_rationale="Not currently sustainable.",
                decision_rationale="Hold.",
            )
        ),
    )
    orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    with pytest.raises(ValueError, match="pending enrichment_required"):
        orchestrator.review(proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK)


def test_worker_review_and_resolve_use_axis_runtime_without_rebuild(monkeypatch, capsys) -> None:
    import base64

    from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime
    from comunicamundo_v4.hefesto.worker import main
    from test_axis_proposal import RecordingEmbedding

    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    decider = RecordingDecider(
        _decision_proposal(
            support=SupportKind.ENRICHMENT,
            axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
            carry_forward_intent=None,
            enrichment_need="Need additional reliable sources.",
            support_rationale="Current material cannot sustain the contribution.",
            decision_rationale="Hold.",
        )
    )
    reviser = RecordingReviser(BOUNDED_REVISED)
    reviewer = RecordingEvidenceReviewer()
    recalibrator = RecordingRecalibrator(_bounded_proposal())

    def make_orchestrator(persistence, runtime, *_args, **_kwargs):
        assert runtime.axis_model == "fake-axis"
        assert persistence is store
        return _orchestrator(
            store, decider=decider, reviser=reviser, reviewer=reviewer, recalibrator=recalibrator, runtime=runtime
        )

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("full Vertex runtime must not be built")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph extraction runtime must not be built")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_indexing_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph indexing runtime must not be built")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._axis_proposal_runtime",
        lambda: AxisProposalRuntime(
            embed_model=RecordingEmbedding(),
            understanding_llm=object(),
            understanding_model="fake-understanding",
            understanding_max_tokens=4_096,
            axis_llm=object(),
            axis_model="fake-axis",
            axis_max_tokens=1_200,
            context_window=1_048_576,
        ),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.axis_feedback.AxisFeedbackOrchestrator", make_orchestrator)
    encoded = base64.urlsafe_b64encode(ENRICHMENT_FEEDBACK.encode()).decode()
    main(["review-axis-feedback", "--planning-run-id", str(proposed.planning_run.planning_run_id), "--feedback-b64", encoded])
    reviewed = AxisFeedbackReviewResult.model_validate_json(capsys.readouterr().out)
    assert reviewed.stopped_at == "enrichment_required"
    assert reviewed.search_performed is False
    assert reviewed.editorial_project_created is False
    main(["resolve-axis-feedback", "--planning-run-id", str(proposed.planning_run.planning_run_id), "--choice", "accept"])
    accepted = AxisFeedbackReviewResult.model_validate_json(capsys.readouterr().out)
    assert accepted.research_needed is True
    assert accepted.search_performed is False
    assert accepted.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
    assert len(synth.materials) == 1
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert recalibrator.calls == []
