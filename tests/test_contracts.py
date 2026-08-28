from uuid import uuid4
import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.contracts import (
    AxisChangeIntent,
    AxisFeedbackDecision,
    AxisFeedbackOutcome,
    AxisFeedbackRecalibration,
    AxisFeedbackSupportAssessment,
    AxisFeedbackUserAction,
    CarriedEditorialDirective,
    EditorialBrief,
    EditorialImpactKind,
    EditorialProject,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisStatus,
    ResidualSupportBasis,
    SupportBasis,
    TreatmentScale,
)
from comunicamundo_v4.hefesto.axis_feedback import resolve_feedback_outcome


def test_axis_requires_human_approval() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Uma direção intelectual específica.")
    assert axis.status is NarrativeAxisStatus.PROPOSED
    approved = axis.approve()
    assert approved.status is NarrativeAxisStatus.APPROVED
    assert approved.approved_at is not None
    assert axis.status is NarrativeAxisStatus.PROPOSED


def test_approved_axis_is_immutable_to_supersede() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Direção.").approve()
    with pytest.raises(ValueError):
        axis.supersede()


def test_project_carries_axis_and_requires_separate_approval() -> None:
    axis = NarrativeAxis(project_id=uuid4(), statement="Direção.").approve()
    project = EditorialProject(
        project_id=axis.project_id,
        narrative_axis_id=axis.narrative_axis_id,
        narrative_axis=axis.statement,
        brief=EditorialBrief(theme="Luto infantil", approach="sensível", duration_minutes=8),
        working_title="Título",
        editorial_promise="Promessa",
        core_subjects=[EditorialSubject(title="Assunto", development="Desenvolvimento")],
    )
    assert project.status.value == "proposed"
    assert project.approve().status.value == "approved"


def test_enrichment_support_requires_a_compact_need() -> None:
    with pytest.raises(ValidationError):
        AxisFeedbackSupportAssessment(
            support_basis=SupportBasis.ENRICHMENT_REQUIRED,
            support_rationale="The contribution would be load-bearing.",
            treatment_scale=TreatmentScale.STRUCTURAL,
        )


def test_preserved_decision_requires_a_carried_directive() -> None:
    axis_id = uuid4()
    with pytest.raises(ValidationError):
        AxisFeedbackDecision(
            contract_version="axis-feedback-decision.v1",
            prompt_version="hefesto.axis-feedback-decision.v1",
            model="fake",
            planning_run_id=uuid4(),
            project_id=uuid4(),
            source_lineage_id=uuid4(),
            understanding_id=uuid4(),
            current_axis_id=axis_id,
            resulting_axis_id=axis_id,
            user_action=AxisFeedbackUserAction.SUBMIT_FEEDBACK,
            feedback="Keep the secondary caretaker in view.",
            impact=EditorialImpactKind.COMPLEMENTS,
            affected_premise="The Axis currently names only one party.",
            editorial_consequences="The later project should keep the second party present.",
            axis_if_supported=AxisChangeIntent.PRESERVED,
            support_basis=SupportBasis.SOURCES,
            support_rationale="The understanding already supports this.",
            treatment_scale=TreatmentScale.BRIEF,
            outcome=AxisFeedbackOutcome.AXIS_PRESERVED,
        )


def test_enrichment_decision_cannot_rewrite_or_be_marked_refused() -> None:
    axis_id = uuid4()
    with pytest.raises(ValidationError):
        AxisFeedbackDecision(
            contract_version="axis-feedback-decision.v1",
            prompt_version="hefesto.axis-feedback-decision.v1",
            model="fake",
            planning_run_id=uuid4(),
            project_id=uuid4(),
            source_lineage_id=uuid4(),
            understanding_id=uuid4(),
            current_axis_id=axis_id,
            resulting_axis_id=uuid4(),
            user_action=AxisFeedbackUserAction.SUBMIT_FEEDBACK,
            feedback="Keep the secondary caretaker in view.",
            impact=EditorialImpactKind.REFRAMES,
            affected_premise="A governing premise would change.",
            editorial_consequences="The Axis would need reconsideration.",
            axis_if_supported=AxisChangeIntent.REVISED,
            support_basis=SupportBasis.ENRICHMENT_REQUIRED,
            support_rationale="Sources do not sustain that centrality.",
            treatment_scale=TreatmentScale.STRUCTURAL,
            outcome=AxisFeedbackOutcome.ENRICHMENT_REQUIRED,
            enrichment_need="Need evidence for the second party's own process.",
        )


def test_outcome_resolution_keeps_impact_and_support_as_separate_dimensions() -> None:
    assert (
        resolve_feedback_outcome(
            support_basis=SupportBasis.ENRICHMENT_REQUIRED,
            axis_if_supported=AxisChangeIntent.REVISED,
        )
        is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    )
    assert (
        resolve_feedback_outcome(
            support_basis=SupportBasis.SOURCES,
            axis_if_supported=AxisChangeIntent.PRESERVED,
        )
        is AxisFeedbackOutcome.AXIS_PRESERVED
    )
    assert (
        resolve_feedback_outcome(
            support_basis=SupportBasis.MODEL,
            axis_if_supported=AxisChangeIntent.REVISED,
        )
        is AxisFeedbackOutcome.AXIS_REVISED
    )


def test_recalibration_cannot_choose_enrichment_required() -> None:
    with pytest.raises(ValidationError):
        AxisFeedbackRecalibration(
            impact=EditorialImpactKind.QUALIFIES,
            affected_premise="A relevant part changes.",
            editorial_consequences="Treat it with care.",
            axis_if_supported=AxisChangeIntent.PRESERVED,
            support_basis="enrichment_required",
            support_rationale="Still want more sources.",
            treatment_scale=TreatmentScale.MENTION_ONLY,
            carried_directive="Mention the second party briefly.",
        )
    ok = AxisFeedbackRecalibration(
        impact=EditorialImpactKind.QUALIFIES,
        affected_premise="A relevant part changes.",
        editorial_consequences="Treat it with care.",
        axis_if_supported=AxisChangeIntent.PRESERVED,
        support_basis=ResidualSupportBasis.MODEL,
        support_rationale="Safe residual model knowledge supports a mention.",
        treatment_scale=TreatmentScale.MENTION_ONLY,
        carried_directive="Mention the second party briefly.",
    )
    assert ok.support_basis is ResidualSupportBasis.MODEL


def test_carried_directive_records_treatment_scale_for_later_project() -> None:
    item = CarriedEditorialDirective(
        decision_id=uuid4(),
        text="Keep the second party present without making them the governing premise.",
        treatment_scale=TreatmentScale.MENTION_ONLY,
        support_basis=SupportBasis.MODEL,
        impact=EditorialImpactKind.COMPLEMENTS,
        origin_feedback="Do not forget the second party.",
    )
    assert item.treatment_scale is TreatmentScale.MENTION_ONLY
    assert item.support_basis is SupportBasis.MODEL


def test_evidence_review_remains_inspectable_on_a_revised_decision() -> None:
    current = uuid4()
    resulting = uuid4()
    review = NarrativeAxisEvidenceReview(
        candidate_statement="A revised governing sentence.",
        revision_required=False,
        unsupported_upgrades=[],
        final_statement="A revised governing sentence.",
    )
    decision = AxisFeedbackDecision(
        contract_version="axis-feedback-decision.v1",
        prompt_version="hefesto.axis-feedback-decision.v1",
        model="fake",
        planning_run_id=uuid4(),
        project_id=uuid4(),
        source_lineage_id=uuid4(),
        understanding_id=uuid4(),
        current_axis_id=current,
        resulting_axis_id=resulting,
        user_action=AxisFeedbackUserAction.SUBMIT_FEEDBACK,
        feedback="Reframe around the shared process.",
        impact=EditorialImpactKind.REFRAMES,
        affected_premise="The current Axis omits a governing relation.",
        editorial_consequences="The Axis must be reconsidered coherently.",
        axis_if_supported=AxisChangeIntent.REVISED,
        support_basis=SupportBasis.SOURCES,
        support_rationale="The understanding already names the relation.",
        treatment_scale=TreatmentScale.STRUCTURAL,
        outcome=AxisFeedbackOutcome.AXIS_REVISED,
        evidence_review=review,
    )
    assert decision.evidence_review is review
    assert decision.resulting_axis_id != decision.current_axis_id
