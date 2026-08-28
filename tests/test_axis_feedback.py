from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.axis_feedback import AxisFeedbackOrchestrator, resolve_feedback_outcome
from comunicamundo_v4.hefesto.contracts import (
    AXIS_FEEDBACK_DECISION_CONTRACT_VERSION,
    AXIS_FEEDBACK_DECISION_PROMPT_VERSION,
    AxisChangeIntent,
    AxisFeedbackImpactAssessment,
    AxisFeedbackOutcome,
    AxisFeedbackRecalibration,
    AxisFeedbackSupportAssessment,
    AxisFeedbackUserAction,
    EditorialImpactKind,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    PlanningRunState,
    ResidualSupportBasis,
    SourceLineageState,
    SupportBasis,
    TreatmentScale,
)
from comunicamundo_v4.hefesto.persistence import FirestoreEditorialRepository, GcsArtifactStore, GcpEditorialPersistence, GcpPersistenceSettings
from comunicamundo_v4.hefesto.prompts import (
    AXIS_FEEDBACK_IMPACT_PROMPT,
    AXIS_FEEDBACK_RECALIBRATION_PROMPT,
    AXIS_FEEDBACK_SUPPORT_PROMPT,
    EDITORIAL_PROJECT_PROMPT,
    REVISE_AXIS_FROM_DECISION_PROMPT,
)
from comunicamundo_v4.hefesto.runtime import AxisFeedbackRuntime
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence
from test_axis_proposal import (
    RecordingEvidenceReviewer,
    RecordingProposer,
    RecordingSynthesizer,
    _axis_runtime,
    _prepare_graph_ready,
    _propose,
)
from test_narrative_axis_evidence_review import IndependentEvidenceReviewer

FEEDBACK_PY = Path("src/comunicamundo_v4/hefesto/axis_feedback.py").read_text()
WORKER_PY = Path("src/comunicamundo_v4/hefesto/worker.py").read_text()
FAKE_AXIS = "fake-axis"
_COMPLEMENT_FEEDBACK = "Keep the secondary caretaker's own process in view."
_QUALIFY_FEEDBACK = "The relevant parties have to be read in relation, not as a one-sided task."
_REFRAME_FEEDBACK = "The governing premise is the shared process, not a single subject's isolation."
_CANONICAL_FEEDBACK = "Não quero que esqueça que o adulto que cuida da criança também está de luto."
_REVISED_AXIS = "The piece is told through a shared process, and care must treat more than one party's loss."
_GENERIC_AXIS = "A precise tension governs the piece. Consequence follows from that choice."


class ScriptedImpact:
    def __init__(self, assessment: AxisFeedbackImpactAssessment) -> None:
        self.assessment = assessment
        self.calls: list[dict] = []

    def assess(self, **kwargs) -> AxisFeedbackImpactAssessment:
        self.calls.append(kwargs)
        return self.assessment


class ScriptedSupport:
    def __init__(self, assessment: AxisFeedbackSupportAssessment) -> None:
        self.assessment = assessment
        self.calls: list[dict] = []

    def assess(self, **kwargs) -> AxisFeedbackSupportAssessment:
        self.calls.append(kwargs)
        return self.assessment


class ScriptedRecalibrator:
    def __init__(self, assessment: AxisFeedbackRecalibration) -> None:
        self.assessment = assessment
        self.calls: list[dict] = []

    def recalibrate(self, **kwargs) -> AxisFeedbackRecalibration:
        self.calls.append(kwargs)
        return self.assessment


class ScriptedReviser:
    def __init__(self, statement: str = _REVISED_AXIS) -> None:
        self.statement = statement
        self.calls: list[dict] = []

    def revise(self, **kwargs) -> NarrativeAxisProposal:
        self.calls.append(kwargs)
        return NarrativeAxisProposal(statement=self.statement)


class FailingImpact:
    def assess(self, **kwargs):
        raise RuntimeError("decision failed")


class FailingReviser:
    def revise(self, **kwargs):
        raise RuntimeError("revision failed")


class FailingRecalibrator:
    def recalibrate(self, **kwargs):
        raise RuntimeError("recalibration failed")


def _prompt_text(prompt) -> str:
    template = getattr(prompt, "template", None)
    if isinstance(template, str) and template.strip():
        return template
    raise AssertionError("expected a PromptTemplate with a template string")


def _feedback_runtime() -> AxisFeedbackRuntime:
    return AxisFeedbackRuntime(
        axis_llm=object(),
        axis_model=FAKE_AXIS,
        axis_max_tokens=10_000,
        context_window=1_048_576,
    )


def _impact(*, kind: EditorialImpactKind, change: AxisChangeIntent) -> AxisFeedbackImpactAssessment:
    return AxisFeedbackImpactAssessment(
        impact=kind,
        affected_premise="The current Axis does not yet treat this contribution as stated.",
        editorial_consequences="Later development must honor the contribution at a scale matching support.",
        axis_if_supported=change,
    )


def _support(
    *,
    basis: SupportBasis,
    scale: TreatmentScale,
    directive: str | None = None,
    need: str | None = None,
) -> AxisFeedbackSupportAssessment:
    return AxisFeedbackSupportAssessment(
        support_basis=basis,
        support_rationale="Support is classified from the persisted understanding, not from keywords.",
        treatment_scale=scale,
        carried_directive=directive,
        enrichment_need=need,
    )


def _recalibrate(
    *,
    kind: EditorialImpactKind,
    change: AxisChangeIntent,
    basis: ResidualSupportBasis,
    scale: TreatmentScale,
    directive: str | None = None,
) -> AxisFeedbackRecalibration:
    return AxisFeedbackRecalibration(
        impact=kind,
        affected_premise="The intention remains, at reduced ambition.",
        editorial_consequences="Treat the contribution only as far as residual support allows.",
        axis_if_supported=change,
        support_basis=basis,
        support_rationale="Residual sources or safe model knowledge can hold a reduced treatment.",
        treatment_scale=scale,
        carried_directive=directive,
    )


def _orchestrator(
    store,
    *,
    impact=None,
    support=None,
    recalibrator=None,
    reviser=None,
    reviewer=None,
):
    return AxisFeedbackOrchestrator(
        store,
        _feedback_runtime(),
        impact if impact is not None else ScriptedImpact(_impact(kind=EditorialImpactKind.COMPLEMENTS, change=AxisChangeIntent.PRESERVED)),
        support
        if support is not None
        else ScriptedSupport(
            _support(
                basis=SupportBasis.SOURCES,
                scale=TreatmentScale.BRIEF,
                directive="Keep the secondary party present in later development.",
            )
        ),
        recalibrator
        if recalibrator is not None
        else ScriptedRecalibrator(
            _recalibrate(
                kind=EditorialImpactKind.COMPLEMENTS,
                change=AxisChangeIntent.PRESERVED,
                basis=ResidualSupportBasis.MODEL,
                scale=TreatmentScale.MENTION_ONLY,
                directive="Mention the secondary party without inventing a hierarchy.",
            )
        ),
        reviser if reviser is not None else ScriptedReviser(),
        reviewer if reviewer is not None else RecordingEvidenceReviewer(),
    )


def _ready_axis(store: MemoryPlanningPersistence, *texts: str):
    run, lineage, extractor, request = _prepare_graph_ready(store, *texts)
    result, synth, prop = _propose(store, run, proposer=RecordingProposer(_GENERIC_AXIS))
    return result, lineage, extractor, request, synth, prop


def _snapshot(store, lineage, extractor, synth):
    return {
        "extraction_writes": store.extraction_writes,
        "graph_writes": store.graph_writes,
        "graph_manifest_writes": store.graph_manifest_writes,
        "understanding_writes": store.understanding_writes,
        "extract_calls": len(extractor.calls),
        "synth_calls": len(synth.materials),
        "artifact_id": lineage.graph_artifact_id,
        "lineage_state": store.get_source_lineage(lineage.source_lineage_id).state,
    }


def _assert_untouched(store, lineage, extractor, synth, before) -> None:
    assert store.extraction_writes == before["extraction_writes"]
    assert store.graph_writes == before["graph_writes"]
    assert store.graph_manifest_writes == before["graph_manifest_writes"]
    assert store.understanding_writes == before["understanding_writes"]
    assert len(extractor.calls) == before["extract_calls"]
    assert len(synth.materials) == before["synth_calls"]
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == before["artifact_id"]
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY


def test_module_is_atomic_and_has_no_case_specific_rules() -> None:
    forbidden = (
        "SimpleLLMPathExtractor",
        "from_documents",
        "build_vertex_runtime",
        "build_graph_extraction_runtime",
        "build_graph_indexing_runtime",
        "LlamaIndexChunkGraphExtractor",
        "GraphExtractionOrchestrator",
        "GraphAssemblyOrchestrator",
        "LlamaIndexUnderstandingSynthesizer",
        "persist_understanding",
        "restore_source_lineage_graph",
        "load_chunk(",
        "build_project",
        "EditorialProject(",
        ".approve(",
        "save_project",
        "web_search",
        "GoogleGenAIEmbedding",
    )
    for token in forbidden:
        assert token not in FEEDBACK_PY
    lowered = FEEDBACK_PY.lower()
    assert "luto" not in lowered
    assert "criança" not in lowered
    assert "adulto" not in lowered
    prompt_lower = (
        _prompt_text(AXIS_FEEDBACK_IMPACT_PROMPT)
        + _prompt_text(AXIS_FEEDBACK_SUPPORT_PROMPT)
        + _prompt_text(AXIS_FEEDBACK_RECALIBRATION_PROMPT)
        + _prompt_text(REVISE_AXIS_FROM_DECISION_PROMPT)
    ).lower()
    assert "luto" not in prompt_lower
    assert "keyword" in prompt_lower
    assert "do not search" in prompt_lower
    assert "carried_editorial_directives" in _prompt_text(EDITORIAL_PROJECT_PROMPT)
    assert "decide-axis-feedback" in WORKER_PY
    assert "refuse-axis-enrichment" in WORKER_PY
    assert "await-axis-enrichment" in WORKER_PY
    assert "build_axis_feedback_runtime" in WORKER_PY
    assert "REVISE_AXIS_PROMPT" not in FEEDBACK_PY
    assert "REVISE_AXIS_FROM_DECISION_PROMPT" in FEEDBACK_PY


def test_supported_complement_preserves_axis_and_carries_a_directive() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "primeiro bloco.", "segundo bloco.")
    before = _snapshot(store, lineage, extractor, synth)
    impact = ScriptedImpact(_impact(kind=EditorialImpactKind.COMPLEMENTS, change=AxisChangeIntent.PRESERVED))
    support = ScriptedSupport(
        _support(
            basis=SupportBasis.SOURCES,
            scale=TreatmentScale.BRIEF,
            directive="Carry the secondary party's process as a compact editorial directive.",
        )
    )
    reviser = ScriptedReviser()
    result = _orchestrator(store, impact=impact, support=support, reviser=reviser).decide(
        axis.planning_run.planning_run_id,
        _COMPLEMENT_FEEDBACK,
    )
    assert result.stopped_at == "axis_proposed"
    assert result.decision.outcome is AxisFeedbackOutcome.AXIS_PRESERVED
    assert result.decision.impact is EditorialImpactKind.COMPLEMENTS
    assert result.decision.support_basis is SupportBasis.SOURCES
    assert result.decision.treatment_scale is TreatmentScale.BRIEF
    assert result.narrative_axis.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    assert result.narrative_axis.statement == axis.proposed_axis
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.human_approval_required is True
    assert result.editorial_project_created is False
    assert result.carried_editorial_directives
    assert "secondary" in result.carried_editorial_directives[-1].text.lower()
    assert reviser.calls == []
    assert len(impact.calls) == 1
    assert len(support.calls) == 1
    assert support.calls[0]["impact"].impact is EditorialImpactKind.COMPLEMENTS
    persisted = store.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert persisted.decision_id == result.decision.decision_id
    directives = store.load_carried_directives(result.planning_run.artifacts.carried_directives_uri)
    assert directives.directives
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    _assert_untouched(store, lineage, extractor, synth, before)
    assert store.understanding_writes == before["understanding_writes"]


def test_supported_qualification_revises_axis_after_decision_and_evidence_review() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    before = _snapshot(store, lineage, extractor, synth)
    old_id = axis.narrative_axis.narrative_axis_id
    reviewer = IndependentEvidenceReviewer()
    reviser = ScriptedReviser(_REVISED_AXIS)
    result = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.QUALIFIES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(_support(basis=SupportBasis.SOURCES, scale=TreatmentScale.DEVELOPED)),
        reviser=reviser,
        reviewer=reviewer,
    ).decide(axis.planning_run.planning_run_id, _QUALIFY_FEEDBACK)
    assert result.stopped_at == "axis_proposed"
    assert result.decision.outcome is AxisFeedbackOutcome.AXIS_REVISED
    assert result.decision.impact is EditorialImpactKind.QUALIFIES
    assert result.narrative_axis.narrative_axis_id != old_id
    assert result.narrative_axis.parent_axis_id == old_id
    assert result.narrative_axis.statement == _REVISED_AXIS
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert result.editorial_project_created is False
    assert len(reviser.calls) == 1
    assert len(reviewer.candidates) == 1
    assert result.decision.evidence_review is not None
    superseded = store.get_axis(old_id)
    assert superseded.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED
    current = store.get_axis(result.narrative_axis.narrative_axis_id)
    assert current.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert current.editorial_project_id is None
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.narrative_axis.narrative_axis_id == result.narrative_axis.narrative_axis_id
    _assert_untouched(store, lineage, extractor, synth, before)


def test_supported_reframe_revises_axis_coherently_not_by_appending_feedback() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    before = _snapshot(store, lineage, extractor, synth)
    reviser = ScriptedReviser(_REVISED_AXIS)
    result = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.REFRAMES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(_support(basis=SupportBasis.SOURCES, scale=TreatmentScale.STRUCTURAL)),
        reviser=reviser,
    ).decide(axis.planning_run.planning_run_id, _REFRAME_FEEDBACK)
    assert result.decision.outcome is AxisFeedbackOutcome.AXIS_REVISED
    assert result.decision.impact is EditorialImpactKind.REFRAMES
    assert result.proposed_axis == _REVISED_AXIS
    assert _REFRAME_FEEDBACK not in result.proposed_axis
    assert result.proposed_axis != axis.proposed_axis + " " + _REFRAME_FEEDBACK
    assert reviser.calls[0]["feedback"] == _REFRAME_FEEDBACK
    assert reviser.calls[0]["impact"].impact is EditorialImpactKind.REFRAMES
    _assert_untouched(store, lineage, extractor, synth, before)


def test_safe_model_support_is_distinct_from_sources() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    before = _snapshot(store, lineage, extractor, synth)
    result = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.COMPLEMENTS, change=AxisChangeIntent.PRESERVED)),
        support=ScriptedSupport(
            _support(
                basis=SupportBasis.MODEL,
                scale=TreatmentScale.MENTION_ONLY,
                directive="Mention the adjacent generally known relation without pretending it is in the sources.",
            )
        ),
        reviser=ScriptedReviser(),
    ).decide(axis.planning_run.planning_run_id, "Hold the adjacent generally known relation in view.")
    assert result.decision.support_basis is SupportBasis.MODEL
    assert result.decision.support_basis is not SupportBasis.SOURCES
    assert result.decision.treatment_scale is TreatmentScale.MENTION_ONLY
    assert result.decision.outcome is AxisFeedbackOutcome.AXIS_PRESERVED
    assert "sources" not in result.carried_editorial_directives[-1].text.lower() or "without pretending" in result.carried_editorial_directives[-1].text.lower()
    assert result.carried_editorial_directives[-1].support_basis is SupportBasis.MODEL
    _assert_untouched(store, lineage, extractor, synth, before)


def test_enrichment_required_stops_before_rewrite_and_persists_the_need() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    before = _snapshot(store, lineage, extractor, synth)
    reviser = ScriptedReviser()
    reviewer = RecordingEvidenceReviewer()
    result = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.REFRAMES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(
            _support(
                basis=SupportBasis.ENRICHMENT_REQUIRED,
                scale=TreatmentScale.STRUCTURAL,
                need="Need evidence that can sustain the second party's process as a governing premise.",
            )
        ),
        reviser=reviser,
        reviewer=reviewer,
    ).decide(axis.planning_run.planning_run_id, _CANONICAL_FEEDBACK)
    assert result.stopped_at == "enrichment_required"
    assert result.decision.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    assert result.enrichment_need
    assert "governing premise" in result.enrichment_need.lower() or "second party" in result.enrichment_need.lower()
    assert result.narrative_axis.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    assert result.narrative_axis.statement == axis.proposed_axis
    assert reviser.calls == []
    assert reviewer.candidates == []
    assert result.editorial_project_created is False
    assert result.enrichment_search_performed is False
    persisted = store.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert persisted.enrichment_need == result.enrichment_need
    assert persisted.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    _assert_untouched(store, lineage, extractor, synth, before)


def test_await_enrichment_stays_stopped_without_search_or_rewrite() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    orch = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.QUALIFIES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(
            _support(
                basis=SupportBasis.ENRICHMENT_REQUIRED,
                scale=TreatmentScale.DEVELOPED,
                need="Need additional evidence before treating this as a qualification of the Axis.",
            )
        ),
        reviser=ScriptedReviser(),
    )
    pending = orch.decide(axis.planning_run.planning_run_id, _QUALIFY_FEEDBACK)
    before = _snapshot(store, lineage, extractor, synth)
    decision_writes = store.decision_writes
    waited = orch.await_enrichment(pending.planning_run.planning_run_id)
    assert waited.stopped_at == "enrichment_required"
    assert waited.decision.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    assert waited.decision.user_action is AxisFeedbackUserAction.AWAIT_ENRICHMENT
    assert waited.decision.enrichment_awaited_at is not None
    assert waited.enrichment_search_performed is False
    assert waited.narrative_axis.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    assert store.decision_writes == decision_writes + 1
    _assert_untouched(store, lineage, extractor, synth, before)


def test_refuse_enrichment_can_preserve_axis_with_a_limited_directive() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    reviser = ScriptedReviser()
    orch = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.REFRAMES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(
            _support(
                basis=SupportBasis.ENRICHMENT_REQUIRED,
                scale=TreatmentScale.STRUCTURAL,
                need="Need evidence to make the second party structurally central.",
            )
        ),
        recalibrator=ScriptedRecalibrator(
            _recalibrate(
                kind=EditorialImpactKind.COMPLEMENTS,
                change=AxisChangeIntent.PRESERVED,
                basis=ResidualSupportBasis.MODEL,
                scale=TreatmentScale.MENTION_ONLY,
                directive="Keep the second party present as a mention, without inventing a hierarchy.",
            )
        ),
        reviser=reviser,
    )
    pending = orch.decide(axis.planning_run.planning_run_id, _CANONICAL_FEEDBACK)
    before = _snapshot(store, lineage, extractor, synth)
    refused = orch.refuse_enrichment(pending.planning_run.planning_run_id)
    assert refused.stopped_at == "axis_proposed"
    assert refused.decision.outcome is AxisFeedbackOutcome.AXIS_PRESERVED
    assert refused.decision.enrichment_refused is True
    assert refused.decision.parent_decision_id == pending.decision.decision_id
    assert refused.decision.support_basis is SupportBasis.MODEL
    assert refused.decision.treatment_scale is TreatmentScale.MENTION_ONLY
    assert refused.narrative_axis.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    assert refused.carried_editorial_directives
    assert refused.carried_editorial_directives[-1].treatment_scale is TreatmentScale.MENTION_ONLY
    assert reviser.calls == []
    assert refused.human_approval_required is True
    assert refused.editorial_project_created is False
    _assert_untouched(store, lineage, extractor, synth, before)


def test_refuse_enrichment_can_still_revise_axis_when_residual_support_suffices() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    old_id = axis.narrative_axis.narrative_axis_id
    reviewer = RecordingEvidenceReviewer()
    reviser = ScriptedReviser(_REVISED_AXIS)
    orch = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.QUALIFIES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(
            _support(
                basis=SupportBasis.ENRICHMENT_REQUIRED,
                scale=TreatmentScale.STRUCTURAL,
                need="Need more evidence for a full structural qualification.",
            )
        ),
        recalibrator=ScriptedRecalibrator(
            _recalibrate(
                kind=EditorialImpactKind.QUALIFIES,
                change=AxisChangeIntent.REVISED,
                basis=ResidualSupportBasis.SOURCES,
                scale=TreatmentScale.BRIEF,
            )
        ),
        reviser=reviser,
        reviewer=reviewer,
    )
    pending = orch.decide(axis.planning_run.planning_run_id, _QUALIFY_FEEDBACK)
    before = _snapshot(store, lineage, extractor, synth)
    refused = orch.refuse_enrichment(pending.planning_run.planning_run_id)
    assert refused.decision.outcome is AxisFeedbackOutcome.AXIS_REVISED
    assert refused.decision.enrichment_refused is True
    assert refused.decision.support_basis is SupportBasis.SOURCES
    assert refused.decision.treatment_scale is TreatmentScale.BRIEF
    assert refused.narrative_axis.narrative_axis_id != old_id
    assert refused.narrative_axis.parent_axis_id == old_id
    assert refused.narrative_axis.statement == _REVISED_AXIS
    assert len(reviser.calls) == 1
    assert len(reviewer.candidates) == 1
    assert refused.decision.evidence_review is not None
    assert store.get_axis(old_id).narrative_axis.status is NarrativeAxisStatus.SUPERSEDED
    _assert_untouched(store, lineage, extractor, synth, before)


def test_revised_axis_goes_through_evidence_strength_review_v3() -> None:
    store = MemoryPlanningPersistence()
    axis, _lineage, _extractor, _request, _synth, _prop = _ready_axis(store, "one.", "two.")
    reviewer = RecordingEvidenceReviewer()
    result = _orchestrator(
        store,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.QUALIFIES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(_support(basis=SupportBasis.SOURCES, scale=TreatmentScale.DEVELOPED)),
        reviser=ScriptedReviser(_REVISED_AXIS),
        reviewer=reviewer,
    ).decide(axis.planning_run.planning_run_id, _QUALIFY_FEEDBACK)
    assert reviewer.candidates == [_REVISED_AXIS]
    assert result.decision.evidence_review is not None
    assert result.decision.evidence_review.candidate_statement == _REVISED_AXIS
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.prompt_version == "hefesto.narrative-axis.v3"


def test_decision_failure_does_not_lose_persisted_understanding() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    understanding_id = axis.editorial_understanding.understanding_id
    before = _snapshot(store, lineage, extractor, synth)
    with pytest.raises(RuntimeError, match="decision failed"):
        _orchestrator(store, impact=FailingImpact(), reviser=ScriptedReviser()).decide(
            axis.planning_run.planning_run_id,
            _COMPLEMENT_FEEDBACK,
        )
    updated = store.get_run(axis.planning_run.planning_run_id)
    assert updated.state is PlanningRunState.AXIS_PROPOSED
    assert updated.error == "decision failed"
    assert updated.artifacts.understanding_uri == axis.planning_run.artifacts.understanding_uri
    stored = store.load_understanding(updated.artifacts.understanding_uri)
    assert stored.understanding_id == understanding_id
    assert stored.synthesis
    assert store.understanding_writes == before["understanding_writes"]
    assert store.decision_writes == 0
    assert updated.artifacts.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    _assert_untouched(store, lineage, extractor, synth, before)


def test_revision_failure_does_not_lose_understanding_or_rewrite_axis() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    before = _snapshot(store, lineage, extractor, synth)
    with pytest.raises(RuntimeError, match="revision failed"):
        _orchestrator(
            store,
            impact=ScriptedImpact(_impact(kind=EditorialImpactKind.REFRAMES, change=AxisChangeIntent.REVISED)),
            support=ScriptedSupport(_support(basis=SupportBasis.SOURCES, scale=TreatmentScale.STRUCTURAL)),
            reviser=FailingReviser(),
        ).decide(axis.planning_run.planning_run_id, _REFRAME_FEEDBACK)
    updated = store.get_run(axis.planning_run.planning_run_id)
    assert updated.state is PlanningRunState.AXIS_PROPOSED
    assert updated.artifacts.narrative_axis_id == axis.narrative_axis.narrative_axis_id
    assert updated.artifacts.axis_feedback_decision_uri is None
    assert store.load_understanding(updated.artifacts.understanding_uri).understanding_id == axis.editorial_understanding.understanding_id
    _assert_untouched(store, lineage, extractor, synth, before)


def test_recalibration_failure_keeps_pending_enrichment_and_understanding() -> None:
    store = MemoryPlanningPersistence()
    axis, lineage, extractor, _request, synth, _prop = _ready_axis(store, "one.", "two.")
    orch = AxisFeedbackOrchestrator(
        store,
        _feedback_runtime(),
        ScriptedImpact(_impact(kind=EditorialImpactKind.REFRAMES, change=AxisChangeIntent.REVISED)),
        ScriptedSupport(
            _support(
                basis=SupportBasis.ENRICHMENT_REQUIRED,
                scale=TreatmentScale.STRUCTURAL,
                need="Need additional evidence.",
            )
        ),
        FailingRecalibrator(),
        ScriptedReviser(),
        RecordingEvidenceReviewer(),
    )
    pending = orch.decide(axis.planning_run.planning_run_id, _REFRAME_FEEDBACK)
    before = _snapshot(store, lineage, extractor, synth)
    with pytest.raises(RuntimeError, match="recalibration failed"):
        orch.refuse_enrichment(pending.planning_run.planning_run_id)
    updated = store.get_run(axis.planning_run.planning_run_id)
    assert updated.state is PlanningRunState.AXIS_PROPOSED
    loaded = store.load_axis_feedback_decision(updated.artifacts.axis_feedback_decision_uri)
    assert loaded.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    assert store.load_understanding(updated.artifacts.understanding_uri).understanding_id == axis.editorial_understanding.understanding_id
    _assert_untouched(store, lineage, extractor, synth, before)


def test_no_route_creates_editorial_project_or_auto_approves() -> None:
    store = MemoryPlanningPersistence()
    axis, _lineage, _extractor, _request, _synth, _prop = _ready_axis(store, "one.", "two.")
    preserved = _orchestrator(store).decide(axis.planning_run.planning_run_id, _COMPLEMENT_FEEDBACK)
    assert preserved.editorial_project_created is False
    assert preserved.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    store2 = MemoryPlanningPersistence()
    axis2, _l, _e, _r, _s, _p = _ready_axis(store2, "one.", "two.")
    revised = _orchestrator(
        store2,
        impact=ScriptedImpact(_impact(kind=EditorialImpactKind.QUALIFIES, change=AxisChangeIntent.REVISED)),
        support=ScriptedSupport(_support(basis=SupportBasis.SOURCES, scale=TreatmentScale.DEVELOPED)),
    ).decide(axis2.planning_run.planning_run_id, _QUALIFY_FEEDBACK)
    assert revised.editorial_project_created is False
    assert revised.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert revised.narrative_axis.approved_at is None


def test_prompts_separate_impact_from_textual_space_and_forbid_hallucination() -> None:
    impact = _prompt_text(AXIS_FEEDBACK_IMPACT_PROMPT).lower()
    support = _prompt_text(AXIS_FEEDBACK_SUPPORT_PROMPT).lower()
    recalc = _prompt_text(AXIS_FEEDBACK_RECALIBRATION_PROMPT).lower()
    revise = _prompt_text(REVISE_AXIS_FROM_DECISION_PROMPT).lower()
    assert "complements" in impact and "qualifies" in impact and "reframes" in impact
    assert "do not rewrite the axis" in impact
    assert "textual space" in impact or "word count" in impact
    assert "sources" in support and "model" in support and "enrichment_required" in support
    assert "not a license to hallucinate" in support
    assert "must not pretend it came from the sources" in support
    assert "refusing enrichment reduces possible depth" in recalc
    assert "does not automatically drop the intention" in recalc or "does not automatically" in recalc
    assert "do not mechanically append the feedback" in revise
    assert resolve_feedback_outcome(
        support_basis=SupportBasis.ENRICHMENT_REQUIRED,
        axis_if_supported=AxisChangeIntent.REVISED,
    ) is AxisFeedbackOutcome.ENRICHMENT_REQUIRED


def test_gcp_persists_inspectable_decision_under_the_planning_run() -> None:
    storage = FakeStorageClient()
    firestore = FakeFirestore()
    settings = GcpPersistenceSettings(project="test-project", bucket="test-bucket")
    persistence = GcpEditorialPersistence(
        artifacts=GcsArtifactStore(storage, bucket_name=settings.bucket, root_prefix=settings.root_prefix),
        repository=FirestoreEditorialRepository(
            firestore,
            axes_collection=settings.axes_collection,
            projects_collection=settings.projects_collection,
            articles_collection=settings.articles_collection,
            planning_runs_collection=settings.planning_runs_collection,
            planning_intents_collection=settings.planning_intents_collection,
            source_lineages_collection=settings.source_lineages_collection,
            source_lineage_keys_collection=settings.source_lineage_keys_collection,
        ),
    )
    from test_axis_proposal import RecordingExtractor, _indexing_runtime, _request
    from comunicamundo_v4.hefesto.graph_extraction import GraphExtractionOrchestrator
    from comunicamundo_v4.hefesto.graph_assembly import GraphAssemblyOrchestrator
    from comunicamundo_v4.hefesto.planning import PlanningOrchestrator
    from comunicamundo_v4.hefesto.axis_proposal import AxisProposalOrchestrator

    run = PlanningOrchestrator(persistence).execute(_request("primeiro bloco.", "segundo bloco."))
    extracted = GraphExtractionOrchestrator(persistence, RecordingExtractor(), model="fake-graph-llm").execute(run.source_lineage_id)
    GraphAssemblyOrchestrator(persistence, _indexing_runtime()).execute(extracted.source_lineage_id)
    proposed = AxisProposalOrchestrator(
        persistence,
        _axis_runtime(),
        RecordingSynthesizer(),
        RecordingProposer(_GENERIC_AXIS),
        RecordingEvidenceReviewer(),
    ).execute(run.planning_run_id)
    result = _orchestrator(persistence).decide(proposed.planning_run.planning_run_id, _COMPLEMENT_FEEDBACK)
    names = list(storage.bucket("test-bucket").blobs)
    assert any("/feedback-decisions/" in name for name in names)
    assert any(name.endswith("carried-directives.json") for name in names)
    loaded = persistence.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert loaded.contract_version == AXIS_FEEDBACK_DECISION_CONTRACT_VERSION
    assert loaded.prompt_version == AXIS_FEEDBACK_DECISION_PROMPT_VERSION
    assert loaded.outcome is AxisFeedbackOutcome.AXIS_PRESERVED
    project_docs = [key for key in firestore._store if key[0] == "hefesto_v4_rebuild_projects"]
    assert project_docs == []
    stored_axis = persistence.get_axis(result.narrative_axis.narrative_axis_id)
    assert stored_axis.editorial_project_id is None
    assert stored_axis.carried_editorial_directives
