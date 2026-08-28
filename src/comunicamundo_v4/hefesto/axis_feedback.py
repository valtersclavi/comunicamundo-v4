"""Human-feedback intelligence between a proposed Narrative Axis and Axis approval."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid5, NAMESPACE_URL

from .axis_proposal import (
    apply_evidence_strength_review,
    validate_axis_statement,
    _understanding_prompt_fields,
)
from .contracts import (
    AXIS_FEEDBACK_CONTRACT_VERSION,
    AXIS_FEEDBACK_PROMPT_VERSION,
    NARRATIVE_AXIS_CONTRACT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    AxisAction,
    AxisFeedbackDecision,
    AxisFeedbackDecisionProposal,
    AxisFeedbackDecisionState,
    AxisFeedbackReviewResult,
    BoundedRecalibrationProposal,
    CarryForwardCommitment,
    EditorialBrief,
    EditorialUnderstanding,
    EnrichmentResolutionChoice,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    PersistedNarrativeAxis,
    PlanningRequest,
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SupportKind,
)
from .persistence import StoredNarrativeAxis
from .prompts import (
    AXIS_FEEDBACK_BOUNDED_RECALIBRATION_PROMPT,
    AXIS_FEEDBACK_DECISION_PROMPT,
    AXIS_FEEDBACK_REVISION_PROMPT,
)
from .runtime import AxisProposalRuntime

BOUNDED_REVISION_CONSTRAINT = (
    "The user refused additional sources. Recalibrate ambition to current sources plus safe model "
    "knowledge. Do not invent facts, numbers, strong causalities, rankings, contested or specialized "
    "precision, or evidence claims. Do not let model knowledge masquerade as source evidence."
)
UNBOUNDED_REVISION_CONSTRAINT = (
    "The contribution is already classified as sustainable from current sources or safe model knowledge. "
    "Do not invent evidence and do not silently upgrade source-supported claims."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _join_lines(values: list[str]) -> str:
    return "\n".join(values) if values else "(none)"


def _axis_uri(run: PlanningRun) -> str:
    uri = run.artifacts.narrative_axis_uri
    if uri is None:
        raise ValueError("PlanningRun does not have a persisted Narrative Axis")
    return uri


def fingerprint_feedback(feedback: str) -> str:
    normalized = " ".join(feedback.split())
    if not normalized:
        raise ValueError("feedback cannot be empty")
    return sha256(normalized.encode("utf-8")).hexdigest()


def feedback_decision_id(
    *,
    planning_run_id: UUID,
    narrative_axis_id: UUID,
    understanding_id: UUID,
    feedback_fingerprint: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"hefesto.axis-feedback:{planning_run_id}:{narrative_axis_id}:{understanding_id}:{feedback_fingerprint}",
    )


class AxisFeedbackDecider(Protocol):
    def decide(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
    ) -> AxisFeedbackDecisionProposal: ...


class AxisFeedbackReviser(Protocol):
    def revise(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
        decision: AxisFeedbackDecisionProposal | BoundedRecalibrationProposal,
        impact: str,
        affected_premises: list[str],
        editorial_consequences: list[str],
        bounded: bool,
    ) -> NarrativeAxisProposal: ...


class BoundedRecalibrator(Protocol):
    def recalibrate(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
        original: AxisFeedbackDecision,
    ) -> BoundedRecalibrationProposal: ...


class NarrativeAxisEvidenceReviewer(Protocol):
    def review(self, *, candidate: str, understanding: EditorialUnderstanding) -> NarrativeAxisEvidenceReview: ...


class AxisFeedbackPersistence(Protocol):
    def get_run(self, planning_run_id: UUID) -> PlanningRun | None: ...
    def save_run(self, run: PlanningRun) -> PlanningRun: ...
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def load_request(self, uri: str) -> PlanningRequest: ...
    def load_understanding(self, uri: str) -> EditorialUnderstanding: ...
    def load_narrative_axis(self, uri: str) -> PersistedNarrativeAxis: ...
    def persist_narrative_axis(self, run: PlanningRun, record: PersistedNarrativeAxis) -> str: ...
    def persist_axis_feedback_decision(self, run: PlanningRun, record: AxisFeedbackDecision) -> str: ...
    def load_axis_feedback_decision(self, uri: str) -> AxisFeedbackDecision: ...
    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis: ...
    def get_axis(self, axis_id: UUID) -> StoredNarrativeAxis | None: ...


class LlamaIndexAxisFeedbackDecider:
    def __init__(self, runtime: AxisProposalRuntime) -> None:
        if runtime.context_window <= 0 or runtime.axis_max_tokens <= 0:
            raise ValueError("axis feedback runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def decide(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
    ) -> AxisFeedbackDecisionProposal:
        return self._runtime.axis_llm.structured_predict(
            AxisFeedbackDecisionProposal,
            AXIS_FEEDBACK_DECISION_PROMPT,
            current_axis=current_axis.statement,
            feedback=feedback,
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            **_understanding_prompt_fields(understanding),
        )


class LlamaIndexAxisFeedbackReviser:
    def __init__(self, runtime: AxisProposalRuntime) -> None:
        if runtime.context_window <= 0 or runtime.axis_max_tokens <= 0:
            raise ValueError("axis feedback runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def revise(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
        decision: AxisFeedbackDecisionProposal | BoundedRecalibrationProposal,
        impact: str,
        affected_premises: list[str],
        editorial_consequences: list[str],
        bounded: bool,
    ) -> NarrativeAxisProposal:
        return self._runtime.axis_llm.structured_predict(
            NarrativeAxisProposal,
            AXIS_FEEDBACK_REVISION_PROMPT,
            current_axis=current_axis.statement,
            feedback=feedback,
            impact=impact,
            support=decision.support.value,
            treatment_scale=decision.treatment_scale.value,
            affected_premises=_join_lines(affected_premises),
            editorial_consequences=_join_lines(editorial_consequences),
            decision_rationale=decision.decision_rationale,
            support_rationale=decision.support_rationale,
            bounded_constraint=BOUNDED_REVISION_CONSTRAINT if bounded else UNBOUNDED_REVISION_CONSTRAINT,
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            **_understanding_prompt_fields(understanding),
        )


class LlamaIndexBoundedRecalibrator:
    def __init__(self, runtime: AxisProposalRuntime) -> None:
        if runtime.context_window <= 0 or runtime.axis_max_tokens <= 0:
            raise ValueError("axis feedback runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def recalibrate(
        self,
        *,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
        original: AxisFeedbackDecision,
    ) -> BoundedRecalibrationProposal:
        return self._runtime.axis_llm.structured_predict(
            BoundedRecalibrationProposal,
            AXIS_FEEDBACK_BOUNDED_RECALIBRATION_PROMPT,
            current_axis=current_axis.statement,
            feedback=feedback,
            impact=original.impact.value,
            enrichment_need=original.enrichment_need or "(none)",
            original_support_rationale=original.support_rationale,
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            **_understanding_prompt_fields(understanding),
        )


class AxisFeedbackOrchestrator:
    """Review human feedback against a proposed Axis without leaving the Axis human-gate."""

    def __init__(
        self,
        persistence: AxisFeedbackPersistence,
        runtime: AxisProposalRuntime,
        decider: AxisFeedbackDecider,
        reviser: AxisFeedbackReviser,
        reviewer: NarrativeAxisEvidenceReviewer,
        recalibrator: BoundedRecalibrator,
        *,
        contract_version: str = AXIS_FEEDBACK_CONTRACT_VERSION,
        prompt_version: str = AXIS_FEEDBACK_PROMPT_VERSION,
        axis_contract_version: str = NARRATIVE_AXIS_CONTRACT_VERSION,
        axis_prompt_version: str = NARRATIVE_AXIS_PROMPT_VERSION,
    ) -> None:
        self._persistence = persistence
        self._runtime = runtime
        self._decider = decider
        self._reviser = reviser
        self._reviewer = reviewer
        self._recalibrator = recalibrator
        self._contract = contract_version
        self._prompt = prompt_version
        self._axis_contract = axis_contract_version
        self._axis_prompt = axis_prompt_version

    def review(self, planning_run_id: UUID, feedback: str) -> AxisFeedbackReviewResult:
        run, lineage, request, understanding, persisted_axis = self._load_gate(planning_run_id)
        current_axis = persisted_axis.narrative_axis
        digest = fingerprint_feedback(feedback)
        existing = self._load_current_decision(run)
        if existing is not None and self._same_submission(existing, understanding, current_axis, digest):
            if existing.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED or existing.state is AxisFeedbackDecisionState.DECIDED:
                return self._result(run, understanding, current_axis, existing)
            if existing.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED:
                return self._result(run, understanding, current_axis, existing)
            if existing.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED:
                active = self._active_axis(run, existing, current_axis)
                return self._result(run, understanding, active, existing)
        if existing is not None and existing.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED:
            raise ValueError("resolve the pending enrichment_required decision before submitting new feedback")
        if (
            existing is not None
            and existing.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED
            and existing.understanding_id == understanding.understanding_id
        ):
            raise ValueError(
                "accepted enrichment remains a research-needed hold until the corpus/understanding changes"
            )
        try:
            proposal = self._decider.decide(
                brief=request.brief,
                understanding=understanding,
                current_axis=current_axis,
                feedback=feedback,
            )
            decision_id = feedback_decision_id(
                planning_run_id=run.planning_run_id,
                narrative_axis_id=current_axis.narrative_axis_id,
                understanding_id=understanding.understanding_id,
                feedback_fingerprint=digest,
            )
            previous_id = None if existing is None else existing.feedback_decision_id
            previous_uri = None if existing is None else run.artifacts.axis_feedback_decision_uri
            if proposal.support is SupportKind.ENRICHMENT:
                decision = self._build_decision(
                    decision_id=decision_id,
                    run=run,
                    lineage=lineage,
                    understanding=understanding,
                    current_axis=current_axis,
                    feedback=feedback,
                    digest=digest,
                    proposal=proposal,
                    state=AxisFeedbackDecisionState.ENRICHMENT_REQUIRED,
                    resulting_axis_id=None,
                    previous_id=previous_id,
                    previous_uri=previous_uri,
                )
                return self._persist_decision(
                    run, understanding, current_axis, decision, axis_uri=_axis_uri(run)
                )
            resulting = None
            axis_uri = _axis_uri(run)
            if proposal.axis_action is AxisAction.REVISE:
                resulting, axis_uri = self._revise_axis(
                    run=run,
                    lineage=lineage,
                    request=request,
                    understanding=understanding,
                    persisted_axis=persisted_axis,
                    current_axis=current_axis,
                    feedback=feedback,
                    proposal=proposal,
                    impact=proposal.impact.value,
                    affected_premises=proposal.affected_premises,
                    editorial_consequences=proposal.editorial_consequences,
                    bounded=False,
                )
            decision = self._build_decision(
                decision_id=decision_id,
                run=run,
                lineage=lineage,
                understanding=understanding,
                current_axis=current_axis,
                feedback=feedback,
                digest=digest,
                proposal=proposal,
                state=AxisFeedbackDecisionState.DECIDED,
                resulting_axis_id=None if resulting is None else resulting.narrative_axis_id,
                previous_id=previous_id,
                previous_uri=previous_uri,
            )
            active = current_axis if resulting is None else resulting
            return self._persist_decision(run, understanding, active, decision, axis_uri=axis_uri)
        except Exception as exc:
            self._persistence.save_run(run.model_copy(update={"error": str(exc), "updated_at": _now()}))
            raise

    def resolve(self, planning_run_id: UUID, choice: EnrichmentResolutionChoice) -> AxisFeedbackReviewResult:
        run, lineage, request, understanding, persisted_axis = self._load_gate(planning_run_id)
        current_axis = persisted_axis.narrative_axis
        existing = self._load_current_decision(run)
        if existing is None:
            raise ValueError("PlanningRun has no axis feedback decision to resolve")
        if existing.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED and choice is EnrichmentResolutionChoice.ACCEPT:
            return self._result(run, understanding, current_axis, existing)
        if existing.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED and choice is EnrichmentResolutionChoice.DECLINE:
            active = self._active_axis(run, existing, current_axis)
            return self._result(run, understanding, active, existing)
        if existing.state is not AxisFeedbackDecisionState.ENRICHMENT_REQUIRED:
            raise ValueError("only an enrichment_required decision can be resolved")
        if existing.current_narrative_axis_id != current_axis.narrative_axis_id:
            raise ValueError("pending enrichment decision does not match the current Axis")
        try:
            if choice is EnrichmentResolutionChoice.ACCEPT:
                decision = existing.model_copy(
                    update={
                        "state": AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED,
                        "user_resolution": choice,
                        "search_performed": False,
                        "editorial_project_created": False,
                        "updated_at": _now(),
                    }
                )
                return self._persist_decision(
                    run, understanding, current_axis, decision, axis_uri=_axis_uri(run)
                )
            bounded = self._recalibrator.recalibrate(
                brief=request.brief,
                understanding=understanding,
                current_axis=current_axis,
                feedback=existing.feedback,
                original=existing,
            )
            resulting = None
            axis_uri = _axis_uri(run)
            if bounded.axis_action is AxisAction.REVISE:
                resulting, axis_uri = self._revise_axis(
                    run=run,
                    lineage=lineage,
                    request=request,
                    understanding=understanding,
                    persisted_axis=persisted_axis,
                    current_axis=current_axis,
                    feedback=existing.feedback,
                    proposal=bounded,
                    impact=existing.impact.value,
                    affected_premises=existing.affected_premises,
                    editorial_consequences=existing.editorial_consequences,
                    bounded=True,
                )
            decision = existing.model_copy(
                update={
                    "state": AxisFeedbackDecisionState.ENRICHMENT_DECLINED,
                    "user_resolution": choice,
                    "support": bounded.support,
                    "support_rationale": bounded.support_rationale,
                    "axis_action": bounded.axis_action,
                    "treatment_scale": bounded.treatment_scale,
                    "treatment_rationale": bounded.treatment_rationale,
                    "decision_rationale": bounded.decision_rationale,
                    "carry_forward": _carry_forward(bounded),
                    "resulting_narrative_axis_id": None if resulting is None else resulting.narrative_axis_id,
                    "search_performed": False,
                    "editorial_project_created": False,
                    "updated_at": _now(),
                }
            )
            active = current_axis if resulting is None else resulting
            return self._persist_decision(run, understanding, active, decision, axis_uri=axis_uri)
        except Exception as exc:
            self._persistence.save_run(run.model_copy(update={"error": str(exc), "updated_at": _now()}))
            raise

    def _load_gate(
        self, planning_run_id: UUID
    ) -> tuple[PlanningRun, SourceLineage, PlanningRequest, EditorialUnderstanding, PersistedNarrativeAxis]:
        run = self._persistence.get_run(planning_run_id)
        if run is None:
            raise KeyError(f"PlanningRun not found: {planning_run_id}")
        if run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("axis feedback requires a PlanningRun at axis_proposed")
        if run.artifacts.request_uri is None:
            raise ValueError("PlanningRun does not have a persisted request")
        if run.artifacts.understanding_uri is None:
            raise ValueError("PlanningRun does not have a persisted editorial understanding")
        if run.artifacts.narrative_axis_uri is None or run.artifacts.narrative_axis_id is None:
            raise ValueError("PlanningRun does not have a persisted Narrative Axis")
        lineage = self._persistence.get_source_lineage(run.source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {run.source_lineage_id}")
        request = self._persistence.load_request(run.artifacts.request_uri)
        understanding = self._persistence.load_understanding(run.artifacts.understanding_uri)
        persisted_axis = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        if understanding.planning_run_id != run.planning_run_id:
            raise ValueError("persisted understanding does not belong to this PlanningRun")
        if persisted_axis.planning_run_id != run.planning_run_id:
            raise ValueError("persisted Narrative Axis does not belong to this PlanningRun")
        if persisted_axis.understanding_id != understanding.understanding_id:
            raise ValueError("persisted Narrative Axis does not match the editorial understanding")
        if persisted_axis.narrative_axis.narrative_axis_id != run.artifacts.narrative_axis_id:
            raise ValueError("PlanningRun Narrative Axis id does not match the persisted Axis")
        if persisted_axis.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            raise ValueError("axis feedback requires a proposed Narrative Axis")
        if persisted_axis.narrative_axis.approved_at is not None:
            raise ValueError("axis feedback cannot run against an approved Narrative Axis")
        validate_axis_statement(persisted_axis.narrative_axis.statement)
        return run, lineage, request, understanding, persisted_axis

    def _load_current_decision(self, run: PlanningRun) -> AxisFeedbackDecision | None:
        uri = run.artifacts.axis_feedback_decision_uri
        if uri is None:
            return None
        try:
            return self._persistence.load_axis_feedback_decision(uri)
        except Exception as exc:
            raise ValueError(f"persisted axis feedback decision cannot be loaded: {uri}") from exc

    def _same_submission(
        self,
        stored: AxisFeedbackDecision,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        digest: str,
    ) -> bool:
        return (
            stored.planning_run_id == understanding.planning_run_id
            and stored.understanding_id == understanding.understanding_id
            and stored.feedback_fingerprint == digest
            and stored.contract_version == self._contract
            and stored.prompt_version == self._prompt
            and stored.model == self._runtime.axis_model
            and (
                stored.current_narrative_axis_id == current_axis.narrative_axis_id
                or stored.resulting_narrative_axis_id == current_axis.narrative_axis_id
            )
        )

    def _active_axis(
        self, run: PlanningRun, decision: AxisFeedbackDecision, fallback: NarrativeAxis
    ) -> NarrativeAxis:
        if decision.resulting_narrative_axis_id is None:
            return fallback
        if fallback.narrative_axis_id == decision.resulting_narrative_axis_id:
            return fallback
        if run.artifacts.narrative_axis_uri is None:
            return fallback
        stored = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        return stored.narrative_axis

    def _revise_axis(
        self,
        *,
        run: PlanningRun,
        lineage: SourceLineage,
        request: PlanningRequest,
        understanding: EditorialUnderstanding,
        persisted_axis: PersistedNarrativeAxis,
        current_axis: NarrativeAxis,
        feedback: str,
        proposal: AxisFeedbackDecisionProposal | BoundedRecalibrationProposal,
        impact: str,
        affected_premises: list[str],
        editorial_consequences: list[str],
        bounded: bool,
    ) -> tuple[NarrativeAxis, str]:
        revised_proposal = self._reviser.revise(
            brief=request.brief,
            understanding=understanding,
            current_axis=current_axis,
            feedback=feedback,
            decision=proposal,
            impact=impact,
            affected_premises=affected_premises,
            editorial_consequences=editorial_consequences,
            bounded=bounded,
        )
        candidate = validate_axis_statement(revised_proposal.statement)
        review = self._reviewer.review(candidate=candidate, understanding=understanding)
        statement = apply_evidence_strength_review(candidate, review)
        new_axis = NarrativeAxis(
            project_id=run.project_id,
            parent_axis_id=current_axis.narrative_axis_id,
            statement=statement,
            status=NarrativeAxisStatus.PROPOSED,
        )
        record = PersistedNarrativeAxis(
            contract_version=self._axis_contract,
            prompt_version=self._axis_prompt,
            model=self._runtime.axis_model,
            planning_run_id=run.planning_run_id,
            project_id=run.project_id,
            source_lineage_id=lineage.source_lineage_id,
            understanding_id=understanding.understanding_id,
            request_fingerprint=run.request_fingerprint,
            brief_fingerprint=run.brief_fingerprint,
            source_fingerprint=run.source_fingerprint,
            graph_artifact_id=persisted_axis.graph_artifact_id,
            graph_manifest_hash=persisted_axis.graph_manifest_hash,
            narrative_axis=new_axis,
        )
        axis_uri = self._persistence.persist_narrative_axis(run, record)
        stored = self._persistence.get_axis(current_axis.narrative_axis_id)
        if stored is not None:
            self._persistence.save_axis(
                stored.model_copy(update={"narrative_axis": stored.narrative_axis.supersede()})
            )
            self._persistence.save_axis(
                StoredNarrativeAxis(
                    narrative_axis=new_axis,
                    brief=request.brief,
                    graph_artifact=stored.graph_artifact,
                    graph_context=understanding.synthesis,
                    planning_run_id=run.planning_run_id,
                    updated_at=_now(),
                )
            )
        return new_axis, axis_uri

    def _build_decision(
        self,
        *,
        decision_id: UUID,
        run: PlanningRun,
        lineage: SourceLineage,
        understanding: EditorialUnderstanding,
        current_axis: NarrativeAxis,
        feedback: str,
        digest: str,
        proposal: AxisFeedbackDecisionProposal | BoundedRecalibrationProposal,
        state: AxisFeedbackDecisionState,
        resulting_axis_id: UUID | None,
        previous_id: UUID | None,
        previous_uri: str | None,
    ) -> AxisFeedbackDecision:
        impact = proposal.impact if isinstance(proposal, AxisFeedbackDecisionProposal) else None
        affected = proposal.affected_premises if isinstance(proposal, AxisFeedbackDecisionProposal) else None
        consequences = proposal.editorial_consequences if isinstance(proposal, AxisFeedbackDecisionProposal) else None
        impact_rationale = proposal.impact_rationale if isinstance(proposal, AxisFeedbackDecisionProposal) else None
        enrichment_need = proposal.enrichment_need if isinstance(proposal, AxisFeedbackDecisionProposal) else None
        assert impact is not None and affected is not None and consequences is not None and impact_rationale is not None
        return AxisFeedbackDecision(
            feedback_decision_id=decision_id,
            contract_version=self._contract,
            prompt_version=self._prompt,
            model=self._runtime.axis_model,
            planning_run_id=run.planning_run_id,
            project_id=run.project_id,
            source_lineage_id=lineage.source_lineage_id,
            understanding_id=understanding.understanding_id,
            current_narrative_axis_id=current_axis.narrative_axis_id,
            request_fingerprint=run.request_fingerprint,
            brief_fingerprint=run.brief_fingerprint,
            source_fingerprint=run.source_fingerprint,
            feedback=feedback,
            feedback_fingerprint=digest,
            state=state,
            impact=impact,
            affected_premises=affected,
            editorial_consequences=consequences,
            impact_rationale=impact_rationale,
            support=proposal.support,
            support_rationale=proposal.support_rationale,
            axis_action=proposal.axis_action,
            treatment_scale=proposal.treatment_scale,
            treatment_rationale=proposal.treatment_rationale,
            enrichment_need=enrichment_need,
            decision_rationale=proposal.decision_rationale,
            carry_forward=_carry_forward(proposal),
            resulting_narrative_axis_id=resulting_axis_id,
            previous_feedback_decision_id=previous_id,
            previous_feedback_decision_uri=previous_uri,
            human_approval_required=True,
            editorial_project_created=False,
            search_performed=False,
        )

    def _persist_decision(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        active_axis: NarrativeAxis,
        decision: AxisFeedbackDecision,
        *,
        axis_uri: str,
    ) -> AxisFeedbackReviewResult:
        uri = self._persistence.persist_axis_feedback_decision(run, decision)
        artifacts = run.artifacts.model_copy(
            update={
                "narrative_axis_id": active_axis.narrative_axis_id,
                "narrative_axis_uri": axis_uri,
                "axis_feedback_decision_id": decision.feedback_decision_id,
                "axis_feedback_decision_uri": uri,
            }
        )
        run = self._persistence.save_run(
            run.model_copy(
                update={
                    "state": PlanningRunState.AXIS_PROPOSED,
                    "artifacts": artifacts,
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )
        return self._result(run, understanding, active_axis, decision)

    def _result(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        axis: NarrativeAxis,
        decision: AxisFeedbackDecision,
    ) -> AxisFeedbackReviewResult:
        pending = decision.state in {
            AxisFeedbackDecisionState.ENRICHMENT_REQUIRED,
            AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED,
        }
        return AxisFeedbackReviewResult(
            planning_run=run,
            editorial_understanding=understanding,
            narrative_axis=axis,
            decision=decision,
            proposed_axis=axis.statement,
            human_approval_required=True,
            editorial_project_created=False,
            search_performed=False,
            research_needed=decision.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED,
            stopped_at="enrichment_required" if pending else "axis_proposed",
        )


def load_axis_feedback_history(persistence: AxisFeedbackPersistence, run: PlanningRun) -> tuple[AxisFeedbackDecision, ...]:
    """Walk previous-links from the latest decision so later stages can recover every commitment in order."""
    uri = run.artifacts.axis_feedback_decision_uri
    if uri is None:
        return ()
    chain: list[AxisFeedbackDecision] = []
    seen: set[UUID] = set()
    while uri is not None:
        decision = persistence.load_axis_feedback_decision(uri)
        if decision.feedback_decision_id in seen:
            raise ValueError("axis feedback history cycle")
        seen.add(decision.feedback_decision_id)
        chain.append(decision)
        uri = decision.previous_feedback_decision_uri
    return tuple(reversed(chain))


def _carry_forward(
    proposal: AxisFeedbackDecisionProposal | BoundedRecalibrationProposal,
) -> CarryForwardCommitment | None:
    if proposal.axis_action is not AxisAction.KEEP:
        return None
    intent = proposal.carry_forward_intent
    if not intent:
        raise ValueError("keeping the Axis requires a carry-forward commitment")
    return CarryForwardCommitment(
        intent=intent,
        treatment_scale=proposal.treatment_scale,
        rationale=proposal.treatment_rationale,
    )
