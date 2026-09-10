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
    duration_minutes: int = Field(ge=1)
    output_language: str = Field(min_length=2)
    audience: str = Field(min_length=1)
    product: str = Field(min_length=1)


class SourceInput(StrictModel):
    uri: str = Field(pattern=r"^(gs://|https://)", min_length=8)
    title: str | None = None
    language: str = Field(default="und", min_length=2)


class EditorialInputRequest(StrictModel):
    project_id: UUID = Field(default_factory=uuid4)
    brief: EditorialBrief
    sources: list[SourceInput] = Field(min_length=1, max_length=64)


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


class IngestedSource(StrictModel):
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    input_uri: str
    original_uri: str = Field(pattern=r"^gs://")
    content_type: str
    bytes: int = Field(ge=1)
    pages: int = Field(ge=1)
    characters: int = Field(ge=1)
    paragraphs: int = Field(ge=1)
    sentences: int = Field(ge=1)
    binary_sha256: Sha256
    normalized_text_sha256: Sha256


class IngestionReceipt(StrictModel):
    status: str
    project_id: UUID
    normalized_request_uri: str = Field(pattern=r"^gs://")
    source_count: int = Field(ge=1)
    paragraph_count: int = Field(ge=1)
    sentence_count: int = Field(ge=1)
    model_invocations: int = Field(default=0, ge=0)
    sources: list[IngestedSource] = Field(min_length=1)


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


class Passage(StrictModel):
    passage_id: Sha256
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    paragraph_ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)


class SelectionFlags(StrictModel):
    relevant: list[int] = Field(default_factory=list)
    central: list[int] = Field(default_factory=list)
    uncertain: list[int] = Field(default_factory=list)
    include_previous: list[int] = Field(default_factory=list)
    include_next: list[int] = Field(default_factory=list)


class SelectionBatch(StrictModel):
    batch_id: Sha256
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    first_passage_ordinal: int = Field(ge=1)
    last_passage_ordinal: int = Field(ge=1)
    input_tokens: int = Field(ge=1)
    flags: SelectionFlags


class EditorialCondensationProposal(StrictModel):
    reading: str = Field(min_length=1)


class EditorialCondensation(StrictModel):
    unit_id: Sha256
    level: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    source_ordinals: list[int] = Field(min_length=1)
    input_tokens: int = Field(ge=1)
    reading: str = Field(min_length=1)


class NarrativeAxisProposal(StrictModel):
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class EditorialSubject(StrictModel):
    title: str = Field(min_length=1)
    editorial_role: str = Field(min_length=1)
    depth: str = Field(min_length=1)
    allocated_minutes: float = Field(ge=0.01)


class EditorialProjectProposal(StrictModel):
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    narrative_axis: NarrativeAxisProposal
    duration_minutes: int = Field(ge=1)
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
    proposal: EditorialProjectProposal
    approved: bool = False


class FinalTextProposal(StrictModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


class FinalTextRecord(StrictModel):
    final_text_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    proposal: FinalTextProposal
    word_count: int = Field(ge=1)


class RunState(StrEnum):
    SOURCE_STRUCTURE_READY = "source_structure_ready"
    SELECTING = "selecting"
    COMPREHENDING = "comprehending"
    PROJECT_PROPOSED = "project_proposed"
    PROJECT_APPROVED = "project_approved"
    PRODUCING_TEXT = "producing_text"
    TEXT_READY = "text_ready"
    FAILED = "failed"


class ProductRun(StrictModel):
    execution_run_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    state: RunState = RunState.SOURCE_STRUCTURE_READY
    request: PlanningRequest
    editorial_project: EditorialProjectRecord | None = None
    final_text: FinalTextRecord | None = None
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
        if self.final_text is not None:
            if self.editorial_project is None or not self.editorial_project.approved:
                raise ValueError("final text requires an approved Editorial Project")
            if self.final_text.editorial_project_id != self.editorial_project.editorial_project_id:
                raise ValueError("final text and Editorial Project disagree")
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
    error_message: str | None = None


def content_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
