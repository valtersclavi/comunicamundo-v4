"""Approve the active Narrative Axis and propose one Editorial Project for a PlanningRun."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Sequence
from uuid import UUID

from .axis_feedback import load_axis_feedback_history
from .axis_proposal import _understanding_prompt_fields
from .contracts import (
    EDITORIAL_PROJECT_CONTRACT_VERSION,
    EDITORIAL_PROJECT_PROMPT_VERSION,
    AxisAction,
    AxisFeedbackDecision,
    AxisFeedbackDecisionState,
    ConsumedCarryForward,
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialProjectProposalResult,
    EditorialSubject,
    EditorialUnderstanding,
    NarrativeAxis,
    NarrativeAxisStatus,
    PersistedEditorialProject,
    PersistedNarrativeAxis,
    PlanningRequest,
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SourceLineageState,
)
from .persistence import StoredEditorialProject, StoredNarrativeAxis
from .prompts import EDITORIAL_PROJECT_PROMPT
from .runtime import EditorialProjectRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def collect_carry_forwards(decisions: Sequence[AxisFeedbackDecision] | None) -> tuple[ConsumedCarryForward, ...]:
    items: list[ConsumedCarryForward] = []
    for decision in decisions or ():
        commitment = decision.carry_forward
        if commitment is None:
            continue
        items.append(
            ConsumedCarryForward(
                feedback_decision_id=decision.feedback_decision_id,
                previous_feedback_decision_id=decision.previous_feedback_decision_id,
                treatment_scale=commitment.treatment_scale,
                intent=commitment.intent,
                rationale=commitment.rationale,
            )
        )
    return tuple(items)


def format_carry_forwards(decisions: Sequence[AxisFeedbackDecision] | None) -> str:
    items = collect_carry_forwards(decisions)
    if not items:
        return "(none)"
    return "\n".join(
        f"- [{item.treatment_scale.value}] decision {item.feedback_decision_id} "
        f"(previous={item.previous_feedback_decision_id}): {item.intent} — {item.rationale}"
        for item in items
    )


def format_feedback_history(decisions: Sequence[AxisFeedbackDecision] | None) -> str:
    if not decisions:
        return "(none)"
    parts: list[str] = []
    for decision in decisions:
        carry = ""
        if decision.carry_forward is not None:
            carry = (
                f"\n  carry_forward: scale={decision.carry_forward.treatment_scale.value} "
                f"intent={decision.carry_forward.intent}"
            )
        parts.append(
            "\n".join(
                [
                    f"- decision {decision.feedback_decision_id} state={decision.state.value} "
                    f"action={decision.axis_action.value} support={decision.support.value} "
                    f"scale={decision.treatment_scale.value} "
                    f"previous={decision.previous_feedback_decision_id}",
                    f"  feedback: {decision.feedback}",
                    f"  premises: {'; '.join(decision.affected_premises)}",
                    f"  consequences: {'; '.join(decision.editorial_consequences)}{carry}",
                ]
            )
        )
    return "\n".join(parts)


def _project_prompt_fields(
    *,
    understanding: EditorialUnderstanding,
    axis_feedback_history: str,
    carry_forwards: str,
) -> dict[str, str]:
    return {
        "graph_context": understanding.synthesis,
        **_understanding_prompt_fields(understanding),
        "axis_feedback_history": axis_feedback_history or "(none)",
        "carry_forwards": carry_forwards or "(none)",
    }


@dataclass(frozen=True, slots=True)
class EditorialProjectContext:
    run: PlanningRun
    lineage: SourceLineage
    request: PlanningRequest
    understanding: EditorialUnderstanding
    persisted_axis: PersistedNarrativeAxis
    current_axis: NarrativeAxis
    stored_axis: StoredNarrativeAxis
    latest_decision: AxisFeedbackDecision | None
    history: tuple[AxisFeedbackDecision, ...]


class EditorialProjectBuilder(Protocol):
    def build(
        self,
        *,
        axis: NarrativeAxis,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        axis_feedback_history: str,
        carry_forwards: str,
    ) -> EditorialProjectProposal: ...


class EditorialProjectPersistence(Protocol):
    def get_run(self, planning_run_id: UUID) -> PlanningRun | None: ...
    def save_run(self, run: PlanningRun) -> PlanningRun: ...
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def load_request(self, uri: str) -> PlanningRequest: ...
    def load_understanding(self, uri: str) -> EditorialUnderstanding: ...
    def load_narrative_axis(self, uri: str) -> PersistedNarrativeAxis: ...
    def persist_narrative_axis(self, run: PlanningRun, record: PersistedNarrativeAxis) -> str: ...
    def load_axis_feedback_decision(self, uri: str) -> AxisFeedbackDecision: ...
    def persist_axis_feedback_decision(self, run: PlanningRun, decision: AxisFeedbackDecision) -> str: ...
    def persist_editorial_project(self, run: PlanningRun, record: PersistedEditorialProject) -> str: ...
    def load_editorial_project(self, uri: str) -> PersistedEditorialProject: ...
    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis: ...
    def get_axis(self, axis_id: UUID) -> StoredNarrativeAxis | None: ...
    def save_project(self, stored: StoredEditorialProject) -> StoredEditorialProject: ...
    def get_project(self, project_id: UUID) -> StoredEditorialProject | None: ...


class LlamaIndexEditorialProjectBuilder:
    def __init__(self, runtime: EditorialProjectRuntime) -> None:
        if runtime.context_window <= 0 or runtime.project_max_tokens <= 0:
            raise ValueError("Editorial Project runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def build(
        self,
        *,
        axis: NarrativeAxis,
        brief: EditorialBrief,
        understanding: EditorialUnderstanding,
        axis_feedback_history: str,
        carry_forwards: str,
    ) -> EditorialProjectProposal:
        return self._runtime.project_llm.structured_predict(
            EditorialProjectProposal,
            EDITORIAL_PROJECT_PROMPT,
            narrative_axis=axis.statement,
            brief=brief.model_dump_json(indent=2),
            output_language=brief.output_language,
            **_project_prompt_fields(
                understanding=understanding,
                axis_feedback_history=axis_feedback_history,
                carry_forwards=carry_forwards,
            ),
        )


class EditorialProjectOrchestrator:
    """Approve the active Axis for a PlanningRun and persist exactly one proposed Editorial Project."""

    terminal_state = PlanningRunState.PROJECT_PROPOSED

    def __init__(
        self,
        persistence: EditorialProjectPersistence,
        runtime: EditorialProjectRuntime,
        builder: EditorialProjectBuilder,
        *,
        contract_version: str = EDITORIAL_PROJECT_CONTRACT_VERSION,
        prompt_version: str = EDITORIAL_PROJECT_PROMPT_VERSION,
    ) -> None:
        self._persistence = persistence
        self._runtime = runtime
        self._builder = builder
        self._contract = contract_version
        self._prompt = prompt_version

    def execute(self, planning_run_id: UUID, *, narrative_axis_id: UUID | None = None) -> EditorialProjectProposalResult:
        existing = self._reusable(planning_run_id, narrative_axis_id)
        if existing is not None:
            return existing
        context = self._load_context(planning_run_id, narrative_axis_id)
        try:
            return self._commit(context)
        except Exception as exc:
            self._persistence.save_run(context.run.model_copy(update={"error": str(exc), "updated_at": _now()}))
            raise

    def _reusable(
        self, planning_run_id: UUID, narrative_axis_id: UUID | None
    ) -> EditorialProjectProposalResult | None:
        run = self._persistence.get_run(planning_run_id)
        if run is None:
            raise KeyError(f"PlanningRun not found: {planning_run_id}")
        if run.state is not PlanningRunState.PROJECT_PROPOSED:
            return None
        if run.artifacts.editorial_project_uri is None or run.artifacts.editorial_project_id is None:
            raise ValueError("PlanningRun is project_proposed but missing a persisted Editorial Project")
        record = self._persistence.load_editorial_project(run.artifacts.editorial_project_uri)
        if record.editorial_project.editorial_project_id != run.artifacts.editorial_project_id:
            raise ValueError("persisted Editorial Project does not match the PlanningRun")
        if narrative_axis_id is not None and record.narrative_axis_id != narrative_axis_id:
            raise ValueError("PlanningRun Axis does not match the requested Narrative Axis")
        if run.artifacts.understanding_uri is None or run.artifacts.narrative_axis_uri is None:
            raise ValueError("project_proposed is missing persisted understanding or Axis")
        understanding = self._persistence.load_understanding(run.artifacts.understanding_uri)
        persisted_axis = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        stored_axis = self._persistence.get_axis(record.narrative_axis_id)
        axis = stored_axis.narrative_axis if stored_axis is not None else persisted_axis.narrative_axis
        if axis.status is not NarrativeAxisStatus.APPROVED:
            raise ValueError("project_proposed requires an approved Narrative Axis")
        decision = None
        history: tuple[AxisFeedbackDecision, ...] = ()
        if run.artifacts.axis_feedback_decision_uri is not None:
            history = load_axis_feedback_history(self._persistence, run)
            decision = history[-1] if history else None
        return self._result(run, understanding, axis, record.editorial_project, decision, history)

    def _load_context(self, planning_run_id: UUID, narrative_axis_id: UUID | None) -> EditorialProjectContext:
        run = self._persistence.get_run(planning_run_id)
        if run is None:
            raise KeyError(f"PlanningRun not found: {planning_run_id}")
        if run.state is PlanningRunState.PROJECT_PROPOSED:
            raise ValueError("PlanningRun already has a proposed Editorial Project")
        if run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("Editorial Project requires a PlanningRun stopped at axis_proposed")
        if run.artifacts.request_uri is None:
            raise ValueError("PlanningRun does not have a persisted request")
        if run.artifacts.understanding_uri is None:
            raise ValueError("PlanningRun does not have persisted editorial understanding")
        if run.artifacts.narrative_axis_uri is None or run.artifacts.narrative_axis_id is None:
            raise ValueError("PlanningRun does not have a persisted Narrative Axis")
        lineage = self._persistence.get_source_lineage(run.source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {run.source_lineage_id}")
        if lineage.state is not SourceLineageState.GRAPH_READY:
            raise ValueError("SourceLineage is not graph_ready")
        if lineage.graph_artifact_id is None or lineage.graph_manifest_uri is None:
            raise ValueError("SourceLineage is missing a persisted PropertyGraph")
        request = self._persistence.load_request(run.artifacts.request_uri)
        if request.project_id != run.project_id:
            raise ValueError("persisted planning request does not belong to this PlanningRun")
        understanding = self._persistence.load_understanding(run.artifacts.understanding_uri)
        if understanding.planning_run_id != run.planning_run_id:
            raise ValueError("persisted understanding does not belong to this PlanningRun")
        if understanding.source_fingerprint != run.source_fingerprint:
            raise ValueError("persisted understanding source fingerprint mismatch")
        persisted_axis = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        current = persisted_axis.narrative_axis
        if current.narrative_axis_id != run.artifacts.narrative_axis_id:
            raise ValueError("PlanningRun Axis id does not match the persisted Axis")
        if current.project_id != run.project_id:
            raise ValueError("Narrative Axis does not belong to this PlanningRun")
        if persisted_axis.planning_run_id != run.planning_run_id:
            raise ValueError("persisted Narrative Axis does not belong to this PlanningRun")
        if persisted_axis.understanding_id != understanding.understanding_id:
            raise ValueError("persisted Narrative Axis does not match the editorial understanding")
        if narrative_axis_id is not None and current.narrative_axis_id != narrative_axis_id:
            raise ValueError("PlanningRun Axis does not match the requested Narrative Axis")
        if current.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        if current.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("active Narrative Axis is already approved without a proposed Editorial Project")
        stored_axis = self._persistence.get_axis(current.narrative_axis_id)
        if stored_axis is None:
            raise ValueError("current Narrative Axis is missing from the axis store")
        if stored_axis.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        latest, history = self._load_feedback(run, understanding, current)
        return EditorialProjectContext(
            run=run,
            lineage=lineage,
            request=request,
            understanding=understanding,
            persisted_axis=persisted_axis,
            current_axis=current,
            stored_axis=stored_axis,
            latest_decision=latest,
            history=history,
        )

    def _load_feedback(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        current: NarrativeAxis,
    ) -> tuple[AxisFeedbackDecision | None, tuple[AxisFeedbackDecision, ...]]:
        if run.artifacts.axis_feedback_decision_uri is None:
            return None, ()
        history = load_axis_feedback_history(self._persistence, run)
        if not history:
            raise ValueError("PlanningRun points at Axis feedback that could not be loaded")
        latest = history[-1]
        self._reject_stale_or_pending(run, understanding, current, latest, history)
        return latest, history

    def _reject_stale_or_pending(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        current: NarrativeAxis,
        latest: AxisFeedbackDecision,
        history: tuple[AxisFeedbackDecision, ...],
    ) -> None:
        if latest.planning_run_id != run.planning_run_id:
            raise ValueError("axis feedback decision does not belong to this PlanningRun")
        if latest.understanding_id != understanding.understanding_id:
            raise ValueError("stale Axis feedback lineage: understanding mismatch")
        if run.artifacts.axis_feedback_decision_id is not None and latest.feedback_decision_id != run.artifacts.axis_feedback_decision_id:
            raise ValueError("stale Axis feedback lineage: PlanningRun points at a different decision")
        for decision in history:
            if decision.planning_run_id != run.planning_run_id:
                raise ValueError("axis feedback decision does not belong to this PlanningRun")
            if decision.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED:
                raise ValueError("unresolved Axis enrichment blocks Editorial Project")
            if decision.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED:
                raise ValueError("accepted Axis enrichment still pending; Editorial Project cannot proceed")
        if latest.axis_action is AxisAction.REVISE:
            if latest.resulting_narrative_axis_id != current.narrative_axis_id:
                raise ValueError("stale Axis feedback lineage: decision does not match the active Axis")
        elif latest.current_narrative_axis_id != current.narrative_axis_id:
            raise ValueError("stale Axis feedback lineage: decision does not match the active Axis")

    def _commit(self, context: EditorialProjectContext) -> EditorialProjectProposalResult:
        approved = context.current_axis.approve()
        proposal = self._builder.build(
            axis=approved,
            brief=context.request.brief,
            understanding=context.understanding,
            axis_feedback_history=format_feedback_history(context.history),
            carry_forwards=format_carry_forwards(context.history),
        )
        project = EditorialProject(
            project_id=approved.project_id,
            narrative_axis_id=approved.narrative_axis_id,
            narrative_axis=approved.statement,
            brief=context.request.brief,
            working_title=proposal.working_title,
            editorial_promise=proposal.editorial_promise,
            core_subjects=[EditorialSubject(**subject.model_dump()) for subject in proposal.core_subjects],
            llm_subjects=proposal.llm_subjects,
            enrichment_opportunities=proposal.enrichment_opportunities,
        )
        consumed_at = _now()
        consumed_latest = None
        consumed_history: list[AxisFeedbackDecision] = []
        for decision in context.history:
            marked = decision.model_copy(
                update={
                    "consumed_at": consumed_at,
                    "consumed_by_editorial_project_id": project.editorial_project_id,
                    "updated_at": consumed_at,
                }
            )
            self._persistence.persist_axis_feedback_decision(context.run, marked)
            consumed_history.append(marked)
            consumed_latest = marked
        approved_record = context.persisted_axis.model_copy(update={"narrative_axis": approved})
        axis_uri = self._persistence.persist_narrative_axis(context.run, approved_record)
        self._persistence.save_axis(
            context.stored_axis.model_copy(
                update={
                    "narrative_axis": approved,
                    "editorial_project_id": project.editorial_project_id,
                    "graph_context": context.understanding.synthesis,
                }
            )
        )
        persisted = PersistedEditorialProject(
            contract_version=self._contract,
            prompt_version=self._prompt,
            model=self._runtime.project_model,
            planning_run_id=context.run.planning_run_id,
            project_id=context.run.project_id,
            source_lineage_id=context.lineage.source_lineage_id,
            understanding_id=context.understanding.understanding_id,
            narrative_axis_id=approved.narrative_axis_id,
            consumed_feedback_decision_id=None if consumed_latest is None else consumed_latest.feedback_decision_id,
            request_fingerprint=context.run.request_fingerprint,
            brief_fingerprint=context.run.brief_fingerprint,
            source_fingerprint=context.run.source_fingerprint,
            graph_artifact_id=context.lineage.graph_artifact_id,
            graph_manifest_hash=context.persisted_axis.graph_manifest_hash,
            editorial_project=project,
        )
        project_uri = self._persistence.persist_editorial_project(context.run, persisted)
        self._persistence.save_project(
            StoredEditorialProject(
                editorial_project=project,
                graph_artifact=context.stored_axis.graph_artifact,
                graph_context=context.understanding.synthesis,
                planning_run_id=context.run.planning_run_id,
                consumed_feedback_decision_id=None if consumed_latest is None else consumed_latest.feedback_decision_id,
                updated_at=_now(),
            )
        )
        run = self._persistence.save_run(
            context.run.model_copy(
                update={
                    "state": PlanningRunState.PROJECT_PROPOSED,
                    "artifacts": context.run.artifacts.model_copy(
                        update={
                            "narrative_axis_id": approved.narrative_axis_id,
                            "narrative_axis_uri": axis_uri,
                            "editorial_project_id": project.editorial_project_id,
                            "editorial_project_uri": project_uri,
                        }
                    ),
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )
        return self._result(run, context.understanding, approved, project, consumed_latest, tuple(consumed_history))

    def _result(
        self,
        run: PlanningRun,
        understanding: EditorialUnderstanding,
        axis: NarrativeAxis,
        project: EditorialProject,
        decision: AxisFeedbackDecision | None,
        history: tuple[AxisFeedbackDecision, ...],
    ) -> EditorialProjectProposalResult:
        return EditorialProjectProposalResult(
            planning_run=run,
            editorial_understanding=understanding,
            narrative_axis=axis,
            editorial_project=project,
            consumed_feedback_decision=decision,
            carry_forwards=list(collect_carry_forwards(history)),
            human_approval_required=True,
            article_created=False,
            search_performed=False,
            graph_reextracted=False,
            understanding_rebuilt=False,
            stopped_at="project_proposed",
        )
