"""Stable product contracts for Hefesto V4."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditorialBrief(StrictModel):
    """The user's mandatory editorial intention."""

    theme: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    duration_minutes: int = Field(ge=1, le=240)
    output_language: str = Field(min_length=2, default="pt-BR")
    audience: str | None = Field(default=None, min_length=1)


class NormalizedSource(StrictModel):
    """Source text already captured and normalized before Hefesto intelligence."""

    source_id: UUID
    text: str = Field(min_length=1)
    language: str = Field(min_length=2)
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NarrativeAxisProposal(StrictModel):
    """One or two sentences that define the governing editorial reading."""

    statement: str = Field(min_length=1, max_length=480)


class NarrativeAxisStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"


class NarrativeAxis(StrictModel):
    """The first user-approved editorial decision, before subjects are chosen."""

    narrative_axis_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    brief: EditorialBrief
    statement: str = Field(min_length=1, max_length=480)
    version: int = Field(default=1, ge=1)
    status: NarrativeAxisStatus = NarrativeAxisStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    def approve(self) -> NarrativeAxis:
        if self.status is NarrativeAxisStatus.APPROVED:
            return self
        now = datetime.now(UTC)
        return self.model_copy(
            update={
                "status": NarrativeAxisStatus.APPROVED,
                "approved_at": now,
                "updated_at": now,
            }
        )

    def revise(self, statement: str) -> NarrativeAxis:
        if self.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("an approved Narrative Axis cannot be revised")
        payload = self.model_dump()
        payload.update(
            {
                "statement": statement,
                "version": self.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return NarrativeAxis.model_validate(payload)


class EditorialSubjectProposal(StrictModel):
    """Compact subject proposal before server-owned identity is assigned."""

    title: str = Field(min_length=1, max_length=160)
    development: str = Field(min_length=1, max_length=480)
    estimated_minutes: int = Field(ge=1, le=120)


class EditorialProjectProposal(StrictModel):
    """Compact structured output produced after Narrative Axis approval."""

    working_title: str = Field(min_length=1, max_length=200)
    subjects: list[EditorialSubjectProposal] = Field(min_length=1, max_length=20)


class EditorialSubject(StrictModel):
    """A user-visible editorial subject with only the development to approve."""

    subject_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=160)
    development: str = Field(min_length=1, max_length=480)
    estimated_minutes: int = Field(ge=1, le=120)


class EditorialProjectStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"


class EditorialProject(StrictModel):
    """Subjects proposed to realize one already-approved Narrative Axis."""

    editorial_project_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    brief: EditorialBrief
    narrative_axis: NarrativeAxis
    working_title: str = Field(min_length=1, max_length=200)
    subjects: list[EditorialSubject] = Field(min_length=1, max_length=20)
    version: int = Field(default=1, ge=1)
    status: EditorialProjectStatus = EditorialProjectStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_project(self) -> EditorialProject:
        if self.narrative_axis.project_id != self.project_id:
            raise ValueError("Narrative Axis and Editorial Project project IDs differ")
        if self.narrative_axis.status is not NarrativeAxisStatus.APPROVED:
            raise ValueError("Editorial Project requires an approved Narrative Axis")
        allocated = sum(subject.estimated_minutes for subject in self.subjects)
        tolerance = max(1, round(self.brief.duration_minutes * 0.15))
        if abs(allocated - self.brief.duration_minutes) > tolerance:
            raise ValueError(
                "subject duration allocation must remain within 15% of the requested duration"
            )
        return self

    def approve(self) -> EditorialProject:
        if self.status is EditorialProjectStatus.APPROVED:
            return self
        now = datetime.now(UTC)
        return self.model_copy(
            update={
                "status": EditorialProjectStatus.APPROVED,
                "approved_at": now,
                "updated_at": now,
            }
        )

    def revise(
        self,
        *,
        working_title: str,
        subjects: list[EditorialSubject],
    ) -> EditorialProject:
        if self.status is EditorialProjectStatus.APPROVED:
            raise ValueError("an approved Editorial Project cannot be revised")
        payload = self.model_dump()
        payload.update(
            {
                "working_title": working_title,
                "subjects": [subject.model_dump() for subject in subjects],
                "version": self.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return EditorialProject.model_validate(payload)


class WorkflowStage(StrEnum):
    RECEIVED = "received"
    BUILDING_GRAPH = "building_graph"
    BUILDING_NARRATIVE_AXIS = "building_narrative_axis"
    AWAITING_NARRATIVE_AXIS_APPROVAL = "awaiting_narrative_axis_approval"
    BUILDING_EDITORIAL_PROJECT = "building_editorial_project"
    AWAITING_EDITORIAL_APPROVAL = "awaiting_editorial_approval"
    RESEARCHING = "researching"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    EDITING = "editing"
    COMPLETED = "completed"
    FAILED = "failed"


class SubjectResearch(StrictModel):
    """Internal editorial research for one approved subject."""

    subject_id: UUID
    synthesis: str = Field(min_length=1)
    promising_angles: list[str] = Field(default_factory=list, max_length=8)
    tensions: list[str] = Field(default_factory=list, max_length=8)
    concrete_material: list[str] = Field(default_factory=list, max_length=12)


class NarrativeBeat(StrictModel):
    """One internal movement of the article; never shown for approval."""

    subject_id: UUID
    narrative_role: str = Field(min_length=1)
    transition: str = Field(min_length=1)


class NarrativePlan(StrictModel):
    """Internal plan used by the writer after editorial approval."""

    title: str = Field(min_length=1)
    opening_move: str = Field(min_length=1)
    central_thesis: str = Field(min_length=1)
    beats: list[NarrativeBeat] = Field(min_length=1, max_length=20)
    closing_move: str = Field(min_length=1)
    voice_notes: list[str] = Field(default_factory=list, max_length=8)


class EditorialReview(StrictModel):
    """Quality review of a complete draft."""

    ready_to_publish: bool
    quality_score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    problems: list[str] = Field(default_factory=list, max_length=12)
    revision_instructions: list[str] = Field(default_factory=list, max_length=12)


class FinalArticle(StrictModel):
    project_id: UUID
    editorial_project_id: UUID
    title: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    word_count: int = Field(ge=1)
    quality_score: int = Field(ge=0, le=100)
    revision_rounds: int = Field(ge=0, le=10)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
