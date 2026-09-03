"""Graph-free, durable editorial gate contracts for Hefesto V2."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import (
    EditorialProject,
    EditorialProjectProposal,
    EditorialProjectStatus,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    StrictModel,
)
from .digestion_contracts import ArtifactPointer
from .execution_contracts import Sha256


EDITORIAL_V2_ENGINE_VERSION = "hefesto-editorial-v2.2"
AXIS_V2_PROMPT_VERSION = "narrative-axis-v2.1"
PROJECT_V2_PROMPT_VERSION = "editorial-project-v2.1"
TIP_TIMING_V2_PROMPT_VERSION = "hefesto-tip-timing-v2.1"


class EditorialV2State(StrEnum):
    UNDERSTANDING_READY = "understanding_ready"
    AXIS_PROPOSED = "axis_proposed"
    AXIS_APPROVED = "axis_approved"
    PROJECT_PROPOSED = "project_proposed"
    PROJECT_APPROVED = "project_approved"
    FAILED = "failed"


class EditorialV2ArtifactRefs(StrictModel):
    request: ArtifactPointer
    digestion_plan: ArtifactPointer
    evidence_matrix: ArtifactPointer
    editorial_understanding: ArtifactPointer
    axis_candidate: ArtifactPointer | None = None
    axis_evidence_review: ArtifactPointer | None = None
    proposed_axis: ArtifactPointer | None = None
    approved_axis: ArtifactPointer | None = None
    project_proposal: ArtifactPointer | None = None
    tip_timing: ArtifactPointer | None = None
    proposed_project: ArtifactPointer | None = None
    approved_project: ArtifactPointer | None = None


class EditorialV2ExecutionRun(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    source_fingerprint: Sha256
    brief_fingerprint: Sha256
    state: EditorialV2State
    completed_model_calls: int = Field(default=0, ge=0)
    artifacts: EditorialV2ArtifactRefs
    active_narrative_axis_id: UUID | None = None
    active_editorial_project_id: UUID | None = None
    resume_state: EditorialV2State | None = None
    failed_work_unit_ids: list[Sha256] = Field(default_factory=list)
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _state_matches_artifacts(self) -> "EditorialV2ExecutionRun":
        order = list(EditorialV2State)
        if self.state is not EditorialV2State.FAILED:
            index = order.index(self.state)
            if index >= order.index(EditorialV2State.AXIS_PROPOSED):
                if self.artifacts.proposed_axis is None or self.active_narrative_axis_id is None:
                    raise ValueError("axis_proposed requires its artifact and identity")
            if index >= order.index(EditorialV2State.AXIS_APPROVED) and self.artifacts.approved_axis is None:
                raise ValueError("axis_approved requires an approved Axis artifact")
            if index >= order.index(EditorialV2State.PROJECT_PROPOSED):
                if self.artifacts.proposed_project is None or self.active_editorial_project_id is None:
                    raise ValueError("project_proposed requires its artifact and identity")
            if index >= order.index(EditorialV2State.PROJECT_APPROVED) and self.artifacts.approved_project is None:
                raise ValueError("project_approved requires an approved Project artifact")
            if self.failed_work_unit_ids or self.error_class or self.error_message:
                raise ValueError("a non-failed editorial run cannot retain failure fields")
            if self.resume_state is not None:
                raise ValueError("a non-failed editorial run cannot retain a resume state")
        else:
            if not self.failed_work_unit_ids or not self.error_class or self.resume_state is None:
                raise ValueError("a failed editorial run requires failed work, an error, and a resume state")
            if self.resume_state is EditorialV2State.FAILED:
                raise ValueError("failed cannot be its own resume state")
        return self


class NarrativeAxisRecordV2(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    understanding_id: UUID
    evidence_matrix_id: UUID
    evidence_matrix_sha256: Sha256
    prompt_version: str = Field(default=AXIS_V2_PROMPT_VERSION, min_length=1)
    model: str = Field(min_length=1)
    candidate: NarrativeAxisProposal
    evidence_review: NarrativeAxisEvidenceReview
    narrative_axis: NarrativeAxis
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _review_governs_axis(self) -> "NarrativeAxisRecordV2":
        if self.evidence_review.candidate_statement != self.candidate.statement:
            raise ValueError("Axis evidence review must evaluate the persisted candidate")
        expected = (
            self.evidence_review.final_statement
            if self.evidence_review.revision_required
            else self.candidate.statement
        )
        if self.narrative_axis.statement != expected:
            raise ValueError("persisted Axis must reflect the evidence-strength decision")
        if self.narrative_axis.project_id != self.project_id:
            raise ValueError("Narrative Axis belongs to another project")
        return self


class HefestoTipV2(StrictModel):
    text: str = Field(min_length=1)
    incremental_minutes: int = Field(ge=1, le=120)
    display_text: str = Field(min_length=1)


class EditorialProjectRecordV2(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    understanding_id: UUID
    narrative_axis_id: UUID
    prompt_version: str = Field(default=PROJECT_V2_PROMPT_VERSION, min_length=1)
    model: str = Field(min_length=1)
    proposal: EditorialProjectProposal
    generated_tips: list[HefestoTipV2] = Field(default_factory=list)
    accepted_tips: list[str] = Field(default_factory=list)
    editorial_project: EditorialProject
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _project_preserves_gate_decisions(self) -> "EditorialProjectRecordV2":
        project = self.editorial_project
        if project.project_id != self.project_id or project.narrative_axis_id != self.narrative_axis_id:
            raise ValueError("Editorial Project identity disagrees with its V2 record")
        raw_tips = [item.text for item in self.generated_tips]
        displays = [item.display_text for item in self.generated_tips]
        if raw_tips != self.proposal.llm_subjects:
            raise ValueError("generated Dicas must preserve proposal order and text")
        if project.status is EditorialProjectStatus.PROPOSED:
            if self.accepted_tips:
                raise ValueError("a proposed Project cannot contain accepted Dicas")
            if project.llm_subjects != displays:
                raise ValueError("proposed Project must expose every generated Dica with timing")
        missing = [tip for tip in self.accepted_tips if tip not in displays]
        if missing:
            raise ValueError("accepted Dica was not generated for this Project")
        return self


class EditorialPlanningV2Result(StrictModel):
    execution_run: EditorialV2ExecutionRun
    narrative_axis_record: NarrativeAxisRecordV2
    editorial_project_record: EditorialProjectRecordV2 | None = None

    @model_validator(mode="after")
    def _result_matches_state(self) -> "EditorialPlanningV2Result":
        if self.execution_run.active_narrative_axis_id != self.narrative_axis_record.narrative_axis.narrative_axis_id:
            raise ValueError("result Axis does not match the execution run")
        if self.execution_run.state in {
            EditorialV2State.PROJECT_PROPOSED,
            EditorialV2State.PROJECT_APPROVED,
        } and self.editorial_project_record is None:
            raise ValueError("project state requires a Project record")
        return self


def approve_axis_record(record: NarrativeAxisRecordV2) -> NarrativeAxisRecordV2:
    if record.narrative_axis.status is NarrativeAxisStatus.APPROVED:
        return record
    return record.model_copy(update={"narrative_axis": record.narrative_axis.approve()})


def approve_project_record_without_tips(record: EditorialProjectRecordV2) -> EditorialProjectRecordV2:
    if record.accepted_tips:
        raise ValueError("direct no-tip approval requires accepted_tips to remain empty")
    if record.editorial_project.status is EditorialProjectStatus.APPROVED:
        return record
    return record.model_copy(
        update={
            "accepted_tips": [],
            "editorial_project": record.editorial_project.approve(),
        }
    )
