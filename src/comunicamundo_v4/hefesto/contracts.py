from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from math import isclose
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditorialBrief(StrictModel):
    theme: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    duration_minutes: int = Field(gt=0)
    output_language: str = Field(min_length=2)
    audience: str = Field(min_length=1)
    product: str = Field(min_length=1)


class NormalizedSource(StrictModel):
    source_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    language: str = Field(min_length=2)
    title: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PlanningRequest(StrictModel):
    project_id: UUID = Field(default_factory=uuid4)
    brief: EditorialBrief
    sources: list[NormalizedSource] = Field(min_length=1)


class Sentence(StrictModel):
    sentence_id: Sha256
    ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)


class Paragraph(StrictModel):
    paragraph_id: Sha256
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)
    sentences: list[Sentence] = Field(min_length=1)


class ParagraphObservationProposal(StrictModel):
    observation: str = Field(min_length=1, max_length=900)
    tags: list[str] = Field(default_factory=list, max_length=12)
    evidence_sentence_ordinals: list[int] = Field(min_length=1)


class ParagraphNoteProposal(StrictModel):
    paragraph_number: int = Field(ge=1)
    observations: list[ParagraphObservationProposal] = Field(min_length=1)
    brief_relevance: str = Field(min_length=1, max_length=600)


class ParagraphBatchProposal(StrictModel):
    notes: list[ParagraphNoteProposal] = Field(min_length=1)


class ParagraphObservation(ParagraphObservationProposal):
    evidence_sentence_ids: list[Sha256] = Field(min_length=1)


class ParagraphNote(StrictModel):
    paragraph_id: Sha256
    observations: list[ParagraphObservation] = Field(min_length=1)
    brief_relevance: str = Field(min_length=1, max_length=600)
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    paragraph_ordinal: int = Field(ge=1)


class ParagraphCoordinate(StrictModel):
    source_ordinal: int = Field(ge=1)
    paragraph_ordinal: int = Field(ge=1)


class EditorialUnderstandingProposal(StrictModel):
    synthesis: str = Field(min_length=1)
    central_ideas: list[str] = Field(min_length=1)
    relations_and_tensions: list[str] = Field(default_factory=list)
    nuances_and_limits: list[str] = Field(default_factory=list)
    editorial_possibilities: list[str] = Field(min_length=1)
    supporting_paragraphs: list[ParagraphCoordinate] = Field(min_length=1)


class EditorialUnderstanding(EditorialUnderstandingProposal):
    understanding_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID


class NarrativeAxisProposal(StrictModel):
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    supporting_paragraphs: list[ParagraphCoordinate] = Field(min_length=1)


class NarrativeAxisRecord(StrictModel):
    narrative_axis_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    proposal: NarrativeAxisProposal
    approved: bool = False
    approved_at: datetime | None = None


class EditorialSubject(StrictModel):
    title: str = Field(min_length=1)
    editorial_role: str = Field(min_length=1)
    depth: str = Field(min_length=1)
    allocated_minutes: float = Field(gt=0)
    supporting_paragraphs: list[ParagraphCoordinate] = Field(default_factory=list)


class EditorialProjectProposal(StrictModel):
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    duration_minutes: int = Field(gt=0)
    source_supported_subjects: list[EditorialSubject] = Field(min_length=1)
    model_knowledge_suggestions: list[str] = Field(default_factory=list)
    source_enrichment_opportunities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def duration_is_fully_allocated(self) -> "EditorialProjectProposal":
        allocated = sum(item.allocated_minutes for item in self.source_supported_subjects)
        if not isclose(allocated, float(self.duration_minutes), rel_tol=0, abs_tol=0.01):
            raise ValueError("source-supported subjects must allocate the complete requested duration")
        return self


class EditorialProjectRecord(StrictModel):
    editorial_project_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    narrative_axis_id: UUID
    proposal: EditorialProjectProposal
    approved: bool = False


class RunState(StrEnum):
    DIGESTING = "digesting"
    UNDERSTANDING_READY = "understanding_ready"
    AXIS_PROPOSED = "axis_proposed"
    AXIS_APPROVED = "axis_approved"
    PROJECT_PROPOSED = "project_proposed"
    PROJECT_APPROVED = "project_approved"
    FAILED = "failed"


class ProductRun(StrictModel):
    execution_run_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    state: RunState = RunState.DIGESTING
    request: PlanningRequest
    understanding: EditorialUnderstanding | None = None
    axis: NarrativeAxisRecord | None = None
    editorial_project: EditorialProjectRecord | None = None
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def coherent(self) -> "ProductRun":
        if self.project_id != self.request.project_id:
            raise ValueError("run and request project IDs disagree")
        if self.state is RunState.FAILED and not self.error_class:
            raise ValueError("failed run requires an error")
        if self.state is not RunState.FAILED and (self.error_class or self.error_message):
            raise ValueError("a non-failed run cannot retain an error")
        return self


class CallUsage(StrictModel):
    task_type: str
    prompt_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    rated_cost_usd: str | None = None
    status: str


def content_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
