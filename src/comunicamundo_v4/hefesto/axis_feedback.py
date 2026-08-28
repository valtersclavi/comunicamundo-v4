"""Inspectable Axis feedback decisions for a PlanningRun already at axis_proposed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from .axis_proposal import apply_evidence_strength_review, validate_axis_statement
from .contracts import (
    AXIS_FEEDBACK_DECISION_CONTRACT_VERSION,
    AXIS_FEEDBACK_DECISION_PROMPT_VERSION,
    CARRIED_EDITORIAL_DIRECTIVES_CONTRACT_VERSION,
    NARRATIVE_AXIS_CONTRACT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    AxisChangeIntent,
    AxisFeedbackDecision,
    AxisFeedbackDecisionResult,
    AxisFeedbackImpactAssessment,
    AxisFeedbackOutcome,
    AxisFeedbackRecalibration,
    AxisFeedbackSupportAssessment,
    AxisFeedbackUserAction,
    CarriedEditorialDirective,
    CarriedEditorialDirectiveSet,
    EditorialBrief,
    EditorialUnderstanding,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    PersistedNarrativeAxis,
    PlanningRequest,
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SourceLineageState,
    SupportBasis,
)
from .persistence import StoredNarrativeAxis
from .prompts import (
    AXIS_FEEDBACK_IMPACT_PROMPT,
    AXIS_FEEDBACK_RECALIBRATION_PROMPT,
    AXIS_FEEDBACK_SUPPORT_PROMPT,
    REVISE_AXIS_FROM_DECISION_PROMPT,
)
from .runtime import AxisFeedbackRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def _join_lines(values) -> str:
    return "\n".join(values) if values else "(none)"


def _understanding_prompt_fields(understanding: EditorialUnderstanding) -> dict[str, str]:
    return {
        "synthesis": understanding.synthesis,
        "central_ideas": _join_lines(understanding.central_ideas),
        "relations_and_tensions": _join_lines(understanding.relations_and_tensions),
        "nuances": _join_lines(understanding.nuances),
        "source_limits": _join_lines(understanding.source_limits),
        "editorial_opportunities": _join_lines(understanding.editorial_opportunities),
    }


def _brief_fields(brief: EditorialBrief) -> dict[str, object]:
    return {
        "theme": brief.theme,
        "approach": brief.approach,
        "audience": brief.audience or "not specified",
        "product": brief.product,
        "duration_minutes": brief.duration_minutes,
        "output_language": brief.output_language,
    }


def resolve_feedback_outcome(
    *,
    support_basis: SupportBasis,
    axis_if_supported: AxisChangeIntent,
) -> AxisFeedbackOutcome:
    if support_basis is SupportBasis.ENRICHMENT_REQUIRED:
        return AxisFeedbackOutcome.ENRICHMENT_REQUIRED
    if axis_if_supported is AxisChangeIntent.REVISED:
        return AxisFeedbackOutcome.AXIS_REVISED
    return AxisFeedbackOutcome.AXIS_PRESERVED


@dataclass(frozen=True, slots=True)
class AxisFeedbackContext:
    run: PlanningRun
    lineage: SourceLineage
    request: PlanningRequest
    understanding: EditorialUnderstanding
    persisted_axis: PersistedNarrativeAxis
    current_axis: NarrativeAxis
    directives: CarriedEditorialDirectiveSet


class AxisFeedbackImpactAssessor(Protocol):
    def assess(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
    ) -> AxisFeedbackImpactAssessment: ...


class AxisFeedbackSupportAssessor(Protocol):
    def assess(
        self,
        *,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        impact: AxisFeedbackImpactAssessment,
    ) -> AxisFeedbackSupportAssessment: ...


class AxisFeedbackRecalibrator(Protocol):
    def recalibrate(
        self,
        *,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        pending: AxisFeedbackDecision,
    ) -> AxisFeedbackRecalibration: ...


class AxisDecisionReviser(Protocol):
    def revise(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        impact: AxisFeedbackImpactAssessment,
        support_basis: SupportBasis,
        support_rationale: str,
        treatment_scale: str,
    ) -> NarrativeAxisProposal: ...


class AxisEvidenceReviewer(Protocol):
    def review(self, *, candidate: str, understanding: EditorialUnderstanding) -> NarrativeAxisEvidenceReview: ...


class AxisFeedbackPersistence(Protocol):
    def get_run(self, planning_run_id: UUID) -> PlanningRun | None: ...
    def save_run(self, run: PlanningRun) -> PlanningRun: ...
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def load_request(self, uri: str) -> PlanningRequest: ...
    def load_understanding(self, uri: str) -> EditorialUnderstanding: ...
    def load_narrative_axis(self, uri: str) -> PersistedNarrativeAxis: ...
    def persist_narrative_axis_revision(self, run: PlanningRun, record: PersistedNarrativeAxis) -> str: ...
    def persist_axis_feedback_decision(self, run: PlanningRun, decision: AxisFeedbackDecision) -> str: ...
    def load_axis_feedback_decision(self, uri: str) -> AxisFeedbackDecision: ...
    def persist_carried_directives(self, run: PlanningRun, directives: CarriedEditorialDirectiveSet) -> str: ...
    def load_carried_directives(self, uri: str) -> CarriedEditorialDirectiveSet: ...
    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis: ...
    def get_axis(self, axis_id: UUID) -> StoredNarrativeAxis | None: ...


class LlamaIndexImpactAssessor:
    def __init__(self, runtime: AxisFeedbackRuntime) -> None:
        _require_runtime(runtime)
        self._runtime = runtime

    def assess(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
    ) -> AxisFeedbackImpactAssessment:
        return self._runtime.axis_llm.structured_predict(
            AxisFeedbackImpactAssessment,
            AXIS_FEEDBACK_IMPACT_PROMPT,
            current_axis=current_axis,
            feedback=feedback,
            **_brief_fields(brief),
            **_understanding_prompt_fields(understanding),
        )


class LlamaIndexSupportAssessor:
    def __init__(self, runtime: AxisFeedbackRuntime) -> None:
        _require_runtime(runtime)
        self._runtime = runtime

    def assess(
        self,
        *,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        impact: AxisFeedbackImpactAssessment,
    ) -> AxisFeedbackSupportAssessment:
        return self._runtime.axis_llm.structured_predict(
            AxisFeedbackSupportAssessment,
            AXIS_FEEDBACK_SUPPORT_PROMPT,
            current_axis=current_axis,
            feedback=feedback,
            impact=impact.impact.value,
            affected_premise=impact.affected_premise,
            editorial_consequences=impact.editorial_consequences,
            axis_if_supported=impact.axis_if_supported.value,
            **_understanding_prompt_fields(understanding),
        )


class LlamaIndexRecalibrator:
    def __init__(self, runtime: AxisFeedbackRuntime) -> None:
        _require_runtime(runtime)
        self._runtime = runtime

    def recalibrate(
        self,
        *,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        pending: AxisFeedbackDecision,
    ) -> AxisFeedbackRecalibration:
        return self._runtime.axis_llm.structured_predict(
            AxisFeedbackRecalibration,
            AXIS_FEEDBACK_RECALIBRATION_PROMPT,
            current_axis=current_axis,
            feedback=feedback,
            enrichment_need=pending.enrichment_need or "(none)",
            previous_impact=pending.impact.value,
            affected_premise=pending.affected_premise,
            editorial_consequences=pending.editorial_consequences,
            **_understanding_prompt_fields(understanding),
        )


class LlamaIndexDecisionReviser:
    def __init__(self, runtime: AxisFeedbackRuntime) -> None:
        _require_runtime(runtime)
        self._runtime = runtime

    def revise(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: str,
        feedback: str,
        impact: AxisFeedbackImpactAssessment,
        support_basis: SupportBasis,
        support_rationale: str,
        treatment_scale: str,
    ) -> NarrativeAxisProposal:
        return self._runtime.axis_llm.structured_predict(
            NarrativeAxisProposal,
            REVISE_AXIS_FROM_DECISION_PROMPT,
            current_axis=current_axis,
            feedback=feedback,
            impact=impact.impact.value,
            affected_premise=impact.affected_premise,
            editorial_consequences=impact.editorial_consequences,
            support_basis=support_basis.value,
            support_rationale=support_rationale,
            treatment_scale=treatment_scale,
            **_brief_fields(brief),
            **_understanding_prompt_fields(understanding),
        )


class AxisFeedbackOrchestrator:
    """Decide what user feedback changes, on what support, without skipping to a rewrite."""

    terminal_state = PlanningRunState.AXIS_PROPOSED

    def __init__(
        self,
        persistence: AxisFeedbackPersistence,
        runtime: AxisFeedbackRuntime,
        impact_assessor: AxisFeedbackImpactAssessor,
        support_assessor: AxisFeedbackSupportAssessor,
        recalibrator: AxisFeedbackRecalibrator,
        reviser: AxisDecisionReviser,
        reviewer: AxisEvidenceReviewer,
        *,
        contract_version: str = AXIS_FEEDBACK_DECISION_CONTRACT_VERSION,
        prompt_version: str = AXIS_FEEDBACK_DECISION_PROMPT_VERSION,
        axis_contract_version: str = NARRATIVE_AXIS_CONTRACT_VERSION,
        axis_prompt_version: str = NARRATIVE_AXIS_PROMPT_VERSION,
    ) -> None:
        self._persistence = persistence
        self._runtime = runtime
        self._impact = impact_assessor
        self._support = support_assessor
        self._recalibrator = recalibrator
        self._reviser = reviser
        self._reviewer = reviewer
        self._contract = contract_version
        self._prompt = prompt_version
        self._axis_contract = axis_contract_version
        self._axis_prompt = axis_prompt_version

    def decide(self, planning_run_id: UUID, feedback: str) -> AxisFeedbackDecisionResult:
        text = feedback.strip()
        if not text:
            raise ValueError("feedback cannot be empty")
        context = self._load_context(planning_run_id)
        try:
            impact = self._impact.assess(
                brief=context.request.brief,
                understanding=context.understanding,
                current_axis=context.current_axis.statement,
                feedback=text,
            )
            support = self._support.assess(
                understanding=context.understanding,
                current_axis=context.current_axis.statement,
                feedback=text,
                impact=impact,
            )
            return self._commit(
                context,
                feedback=text,
                impact=impact,
                support_basis=support.support_basis,
                support_rationale=support.support_rationale,
                treatment_scale=support.treatment_scale,
                carried_directive=support.carried_directive,
                enrichment_need=support.enrichment_need,
                enrichment_refused=False,
                parent_decision_id=None,
                user_action=AxisFeedbackUserAction.SUBMIT_FEEDBACK,
            )
        except Exception as exc:
            self._fail(context.run, exc)
            raise

    def refuse_enrichment(self, planning_run_id: UUID) -> AxisFeedbackDecisionResult:
        context = self._load_context(planning_run_id)
        pending = self._require_pending_enrichment(context)
        try:
            recalibrated = self._recalibrator.recalibrate(
                understanding=context.understanding,
                current_axis=context.current_axis.statement,
                feedback=pending.feedback,
                pending=pending,
            )
            return self._commit(
                context,
                feedback=pending.feedback,
                impact=AxisFeedbackImpactAssessment(
                    impact=recalibrated.impact,
                    affected_premise=recalibrated.affected_premise,
                    editorial_consequences=recalibrated.editorial_consequences,
                    axis_if_supported=recalibrated.axis_if_supported,
                ),
                support_basis=SupportBasis(recalibrated.support_basis.value),
                support_rationale=recalibrated.support_rationale,
                treatment_scale=recalibrated.treatment_scale,
                carried_directive=recalibrated.carried_directive,
                enrichment_need=None,
                enrichment_refused=True,
                parent_decision_id=pending.decision_id,
                user_action=AxisFeedbackUserAction.REFUSE_ENRICHMENT,
            )
        except Exception as exc:
            self._fail(context.run, exc)
            raise

    def await_enrichment(self, planning_run_id: UUID) -> AxisFeedbackDecisionResult:
        context = self._load_context(planning_run_id)
        pending = self._require_pending_enrichment(context)
        updated = pending.model_copy(
            update={
                "user_action": AxisFeedbackUserAction.AWAIT_ENRICHMENT,
                "enrichment_awaited_at": _now(),
            }
        )
        uri = self._persistence.persist_axis_feedback_decision(context.run, updated)
        run = self._persistence.save_run(
            context.run.model_copy(
                update={
                    "artifacts": context.run.artifacts.model_copy(
                        update={
                            "axis_feedback_decision_id": updated.decision_id,
                            "axis_feedback_decision_uri": uri,
                        }
                    ),
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )
        return self._result(run, context.understanding, context.current_axis, updated, context.directives.directives)

    def _commit(
        self,
        context: AxisFeedbackContext,
        *,
        feedback: str,
        impact: AxisFeedbackImpactAssessment,
        support_basis: SupportBasis,
        support_rationale: str,
        treatment_scale,
        carried_directive: str | None,
        enrichment_need: str | None,
        enrichment_refused: bool,
        parent_decision_id: UUID | None,
        user_action: AxisFeedbackUserAction,
    ) -> AxisFeedbackDecisionResult:
        outcome = resolve_feedback_outcome(support_basis=support_basis, axis_if_supported=impact.axis_if_supported)
        current = context.current_axis
        resulting = current
        evidence_review = None
        if outcome is AxisFeedbackOutcome.AXIS_REVISED:
            proposal = self._reviser.revise(
                brief=context.request.brief,
                understanding=context.understanding,
                current_axis=current.statement,
                feedback=feedback,
                impact=impact,
                support_basis=support_basis,
                support_rationale=support_rationale,
                treatment_scale=treatment_scale.value,
            )
            candidate = validate_axis_statement(proposal.statement)
            evidence_review = self._reviewer.review(candidate=candidate, understanding=context.understanding)
            statement = apply_evidence_strength_review(candidate, evidence_review)
            resulting = NarrativeAxis(
                project_id=current.project_id,
                parent_axis_id=current.narrative_axis_id,
                statement=statement,
                status=NarrativeAxisStatus.PROPOSED,
            )
        directive_text = (carried_directive or "").strip() or None
        if outcome is AxisFeedbackOutcome.AXIS_PRESERVED and not directive_text:
            raise ValueError("preserving the Axis requires a carried editorial directive")
        decision_id = uuid4()
        decision = AxisFeedbackDecision(
            decision_id=decision_id,
            contract_version=self._contract,
            prompt_version=self._prompt,
            model=self._runtime.axis_model,
            planning_run_id=context.run.planning_run_id,
            project_id=context.run.project_id,
            source_lineage_id=context.lineage.source_lineage_id,
            understanding_id=context.understanding.understanding_id,
            current_axis_id=current.narrative_axis_id,
            resulting_axis_id=resulting.narrative_axis_id,
            parent_decision_id=parent_decision_id,
            user_action=user_action,
            feedback=feedback,
            impact=impact.impact,
            affected_premise=impact.affected_premise,
            editorial_consequences=impact.editorial_consequences,
            axis_if_supported=impact.axis_if_supported,
            support_basis=support_basis,
            support_rationale=support_rationale,
            treatment_scale=treatment_scale,
            outcome=outcome,
            enrichment_need=enrichment_need if outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED else None,
            enrichment_refused=enrichment_refused,
            carried_directive=directive_text if outcome is not AxisFeedbackOutcome.ENRICHMENT_REQUIRED else None,
            evidence_review=evidence_review,
        )
        directives = list(context.directives.directives)
        if directive_text and outcome is not AxisFeedbackOutcome.ENRICHMENT_REQUIRED:
            directives.append(
                CarriedEditorialDirective(
                    decision_id=decision_id,
                    text=directive_text,
                    treatment_scale=treatment_scale,
                    support_basis=support_basis,
                    impact=impact.impact,
                    origin_feedback=feedback,
                )
            )
        directive_set = context.directives.model_copy(update={"directives": directives, "updated_at": _now()})
        run = self._persist_commit(context, decision, resulting, directive_set, outcome)
        return self._result(run, context.understanding, resulting, decision, directives)

    def _persist_commit(
        self,
        context: AxisFeedbackContext,
        decision: AxisFeedbackDecision,
        resulting: NarrativeAxis,
        directive_set: CarriedEditorialDirectiveSet,
        outcome: AxisFeedbackOutcome,
    ) -> PlanningRun:
        axis_uri = context.run.artifacts.narrative_axis_uri
        stored_current = self._persistence.get_axis(context.current_axis.narrative_axis_id)
        if outcome is AxisFeedbackOutcome.AXIS_REVISED:
            if stored_current is None:
                raise ValueError("current Narrative Axis is missing from the axis store")
            self._persistence.save_axis(
                stored_current.model_copy(update={"narrative_axis": stored_current.narrative_axis.supersede()})
            )
            record = PersistedNarrativeAxis(
                contract_version=self._axis_contract,
                prompt_version=self._axis_prompt,
                model=context.persisted_axis.model,
                planning_run_id=context.run.planning_run_id,
                project_id=context.run.project_id,
                source_lineage_id=context.lineage.source_lineage_id,
                understanding_id=context.understanding.understanding_id,
                request_fingerprint=context.run.request_fingerprint,
                brief_fingerprint=context.run.brief_fingerprint,
                source_fingerprint=context.run.source_fingerprint,
                graph_artifact_id=context.lineage.graph_artifact_id,
                graph_manifest_hash=context.persisted_axis.graph_manifest_hash,
                narrative_axis=resulting,
            )
            axis_uri = self._persistence.persist_narrative_axis_revision(context.run, record)
            self._persistence.save_axis(
                stored_current.model_copy(
                    update={
                        "narrative_axis": resulting,
                        "editorial_project_id": None,
                        "carried_editorial_directives": directive_set.directives,
                        "graph_context": context.understanding.synthesis,
                    }
                )
            )
        elif stored_current is not None:
            self._persistence.save_axis(
                stored_current.model_copy(update={"carried_editorial_directives": directive_set.directives})
            )
        decision_uri = self._persistence.persist_axis_feedback_decision(context.run, decision)
        directives_uri = self._persistence.persist_carried_directives(context.run, directive_set)
        return self._persistence.save_run(
            context.run.model_copy(
                update={
                    "state": PlanningRunState.AXIS_PROPOSED,
                    "artifacts": context.run.artifacts.model_copy(
                        update={
                            "narrative_axis_id": resulting.narrative_axis_id,
                            "narrative_axis_uri": axis_uri,
                            "axis_feedback_decision_id": decision.decision_id,
                            "axis_feedback_decision_uri": decision_uri,
                            "carried_directives_uri": directives_uri,
                        }
                    ),
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )

    def _load_context(self, planning_run_id: UUID) -> AxisFeedbackContext:
        run = self._persistence.get_run(planning_run_id)
        if run is None:
            raise KeyError(f"PlanningRun not found: {planning_run_id}")
        if run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("axis feedback requires a PlanningRun stopped at axis_proposed")
        if run.artifacts.request_uri is None:
            raise ValueError("PlanningRun does not have a persisted request")
        if run.artifacts.understanding_uri is None:
            raise ValueError("PlanningRun does not have persisted editorial understanding")
        if run.artifacts.narrative_axis_uri is None or run.artifacts.narrative_axis_id is None:
            raise ValueError("PlanningRun does not have a persisted Narrative Axis")
        lineage = self._persistence.get_source_lineage(run.source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {run.source_lineage_id}")
        if lineage.graph_artifact_id is None or lineage.graph_manifest_uri is None:
            raise ValueError("SourceLineage is missing a persisted PropertyGraph")
        request = self._persistence.load_request(run.artifacts.request_uri)
        understanding = self._persistence.load_understanding(run.artifacts.understanding_uri)
        if understanding.planning_run_id != run.planning_run_id:
            raise ValueError("persisted understanding does not belong to this PlanningRun")
        if understanding.source_fingerprint != run.source_fingerprint:
            raise ValueError("persisted understanding source fingerprint mismatch")
        persisted_axis = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        current = persisted_axis.narrative_axis
        if current.narrative_axis_id != run.artifacts.narrative_axis_id:
            raise ValueError("PlanningRun Axis id does not match the persisted Axis")
        if current.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("approved Narrative Axis is immutable")
        if current.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot decide feedback against a superseded Narrative Axis")
        if current.approved_at is not None:
            raise ValueError("approved Narrative Axis is immutable")
        directives = self._load_directives(run)
        return AxisFeedbackContext(
            run=run,
            lineage=lineage,
            request=request,
            understanding=understanding,
            persisted_axis=persisted_axis,
            current_axis=current,
            directives=directives,
        )

    def _load_directives(self, run: PlanningRun) -> CarriedEditorialDirectiveSet:
        if run.artifacts.carried_directives_uri is None:
            return CarriedEditorialDirectiveSet(
                contract_version=CARRIED_EDITORIAL_DIRECTIVES_CONTRACT_VERSION,
                planning_run_id=run.planning_run_id,
                project_id=run.project_id,
            )
        return self._persistence.load_carried_directives(run.artifacts.carried_directives_uri)

    def _require_pending_enrichment(self, context: AxisFeedbackContext) -> AxisFeedbackDecision:
        uri = context.run.artifacts.axis_feedback_decision_uri
        if uri is None:
            raise ValueError("no inspectable axis feedback decision is pending")
        pending = self._persistence.load_axis_feedback_decision(uri)
        if pending.outcome is not AxisFeedbackOutcome.ENRICHMENT_REQUIRED:
            raise ValueError("enrichment response requires a pending enrichment_required decision")
        if pending.understanding_id != context.understanding.understanding_id:
            raise ValueError("pending decision does not match the persisted understanding")
        return pending

    def _fail(self, run: PlanningRun, exc: Exception) -> None:
        self._persistence.save_run(run.model_copy(update={"error": str(exc), "updated_at": _now()}))

    def _result(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        axis: NarrativeAxis,
        decision: AxisFeedbackDecision,
        directives: list[CarriedEditorialDirective],
    ) -> AxisFeedbackDecisionResult:
        stopped_at = (
            "enrichment_required"
            if decision.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED
            else "axis_proposed"
        )
        return AxisFeedbackDecisionResult(
            planning_run=run,
            editorial_understanding=understanding,
            narrative_axis=axis,
            decision=decision,
            carried_editorial_directives=directives,
            proposed_axis=axis.statement,
            enrichment_need=decision.enrichment_need,
            human_approval_required=True,
            editorial_project_created=False,
            enrichment_search_performed=False,
            graph_reextracted=False,
            understanding_rebuilt=False,
            stopped_at=stopped_at,
        )


def _require_runtime(runtime: AxisFeedbackRuntime) -> None:
    if runtime.context_window <= 0 or runtime.axis_max_tokens <= 0:
        raise ValueError("axis feedback runtime must declare explicit context and output budgets")
