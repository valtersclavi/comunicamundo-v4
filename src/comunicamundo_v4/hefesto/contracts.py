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


class EditorialSubjectProposal(StrictModel):
    """Model-generated subject before server-owned identity is assigned."""

    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    research_questions: list[str] = Field(min_length=1, max_length=8)
    estimated_minutes: int = Field(ge=1, le=120)


class EditorialProjectProposal(StrictModel):
    """Structured output produced by the editorial planner."""

    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    subjects: list[EditorialSubjectProposal] = Field(min_length=1, max_length=20)


class EditorialSubject(StrictModel):
    """A user-visible and independently editable editorial subject."""

    subject_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    research_questions: list[str] = Field(min_length=1, max_length=8)
    estimated_minutes: int = Field(ge=1, le=120)


class EditorialProjectStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"


class NarrativeAxisStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class NarrativeAxisProposal(StrictModel):
    """Model-generated Narrative Axis before server-owned identity is assigned."""

    statement: str = Field(min_length=1, max_length=800)


class NarrativeAxis(StrictModel):
    """1–2 sentence intellectual/editorial direction of the piece."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    narrative_axis_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    parent_axis_id: UUID | None = None
    statement: str = Field(min_length=1, max_length=800)
    status: NarrativeAxisStatus = NarrativeAxisStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    def approve(self) -> NarrativeAxis:
        if self.status is NarrativeAxisStatus.APPROVED:
            return self
        if self.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        return self.model_copy(
            update={
                "status": NarrativeAxisStatus.APPROVED,
                "approved_at": datetime.now(UTC),
            }
        )

    def supersede(self) -> NarrativeAxis:
        if self.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("approved Narrative Axis is immutable")
        if self.status is NarrativeAxisStatus.SUPERSEDED:
            return self
        return self.model_copy(update={"status": NarrativeAxisStatus.SUPERSEDED})


class EditorialProject(StrictModel):
    """The object presented for editorial approval after the Narrative Axis."""

    editorial_project_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    brief: EditorialBrief
    narrative_axis_id: UUID
    narrative_axis: str = Field(min_length=1, max_length=800)
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    subjects: list[EditorialSubject] = Field(min_length=1, max_length=20)
    status: EditorialProjectStatus = EditorialProjectStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_duration(self) -> EditorialProject:
        allocated = sum(subject.estimated_minutes for subject in self.subjects)
        tolerance = max(1, round(self.brief.duration_minutes * 0.15))
        if abs(allocated - self.brief.duration_minutes) > tolerance:
            raise ValueError(
                "subject duration allocation must remain within 15% of the requested duration"
            )
        return self

    def approve(self) -> EditorialProject:
        return self.model_copy(
            update={
                "status": EditorialProjectStatus.APPROVED,
                "approved_at": datetime.now(UTC),
            }
        )


class WorkflowStage(StrEnum):
    RECEIVED = "received"
    BUILDING_GRAPH = "building_graph"
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
