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


class SourceMode(StrEnum):
    PROVIDED = "provided"
    FIND_FOR_ME = "find_for_me"


class SourceInput(StrictModel):
    uri: str = Field(pattern=r"^(gs://|https://)", min_length=8)
    title: str | None = None
    language: str = Field(default="und", min_length=2)
    doi: str | None = None
    publisher: str | None = None


class EditorialInputRequest(StrictModel):
    project_id: UUID = Field(default_factory=uuid4)
    brief: EditorialBrief
    source_mode: SourceMode = SourceMode.PROVIDED
    sources: list[SourceInput] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def source_mode_matches_sources(self) -> "EditorialInputRequest":
        if self.source_mode is SourceMode.PROVIDED and not self.sources:
            raise ValueError("provided source mode requires at least one source")
        if self.source_mode is SourceMode.FIND_FOR_ME and self.sources:
            raise ValueError("find-for-me source mode starts with zero sources")
        return self


class SourceSearchPlan(StrictModel):
    queries: list[str] = Field(min_length=1, max_length=12)
    editorial_coverage: list[str] = Field(min_length=1)


class SourceCandidate(StrictModel):
    candidate_id: Sha256
    provider: str = Field(pattern=r"^(provided|you|openalex)$")
    query: str = Field(min_length=1)
    rank: int = Field(ge=1)
    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    acquisition_url: str = Field(pattern=r"^https://")
    publisher: str | None = None
    doi: str | None = None
    published_at: str | None = None
    description: str | None = None


class SourceSelectionProposal(StrictModel):
    selected_candidate_ids: list[Sha256] = Field(default_factory=list, max_length=64)
    coverage_judgment: str = Field(min_length=1)
    coverage_complete: bool
    follow_up_queries: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def completion_is_actionable(self) -> "SourceSelectionProposal":
        if self.coverage_complete and not self.selected_candidate_ids:
            raise ValueError("complete coverage requires at least one selected source")
        if not self.coverage_complete and not self.follow_up_queries:
            raise ValueError("incomplete coverage requires follow-up queries")
        return self


class RawSourceSelectionProposal(StrictModel):
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=64)
    coverage_judgment: str = Field(min_length=1)
    coverage_complete: bool
    follow_up_queries: list[str] = Field(default_factory=list, max_length=8)


class AcquisitionAttempt(StrictModel):
    route: str = Field(pattern=r"^(direct|you_contents|jina_reader|browser_use)$")
    ok: bool
    duration_ms: int = Field(ge=0)
    final_url: str | None = None
    content_type: str | None = None
    content_characters: int = Field(default=0, ge=0)
    error: str | None = None


class AcquiredSource(StrictModel):
    candidate: SourceCandidate
    status: str = Field(pattern=r"^(acquired|partial|human_action_required|failed)$")
    selected_route: str | None = Field(
        default=None,
        pattern=r"^(direct|you_contents|jina_reader|browser_use)$",
    )
    final_url: str | None = None
    normalized_text: str | None = None
    original_uri: str | None = None
    content_type: str | None = None
    binary_sha256: Sha256 | None = None
    normalized_text_sha256: Sha256 | None = None
    attempts: list[AcquisitionAttempt] = Field(default_factory=list)
    failure_reason: str | None = None


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
    pages: int | None = Field(default=None, ge=1)
    characters: int = Field(ge=1)
    paragraphs: int = Field(ge=1)
    sentences: int = Field(ge=1)
    binary_sha256: Sha256
    normalized_text_sha256: Sha256
    acquisition: AcquiredSource | None = None


class IngestionReceipt(StrictModel):
    status: str
    project_id: UUID
    normalized_request_uri: str = Field(pattern=r"^gs://")
    source_count: int = Field(ge=1)
    paragraph_count: int = Field(ge=1)
    sentence_count: int = Field(ge=1)
    model_invocations: int = Field(default=0, ge=0)
    sources: list[IngestedSource] = Field(min_length=1)
    failed_acquisitions: list[AcquiredSource] = Field(default_factory=list)


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


class ProvisionalEditorialSubject(StrictModel):
    title: str = Field(min_length=1)
    editorial_role: str = Field(min_length=1)
    desired_depth: str = Field(min_length=1)
    allocated_minutes: float = Field(ge=0.01)


class ProvisionalEditorialProject(StrictModel):
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    narrative_axis: NarrativeAxisProposal
    duration_minutes: int = Field(ge=1)
    subjects: list[ProvisionalEditorialSubject] = Field(min_length=1)
    disclaimer: str = Field(min_length=1)

    @model_validator(mode="after")
    def duration_is_fully_allocated(self) -> "ProvisionalEditorialProject":
        allocated = sum(item.allocated_minutes for item in self.subjects)
        if not isclose(allocated, float(self.duration_minutes), rel_tol=0, abs_tol=0.01):
            raise ValueError("provisional subjects must allocate the complete requested duration")
        return self


class EditorialEvidence(StrictModel):
    source_ordinals: list[int] = Field(min_length=1)
    contribution: str = Field(min_length=1)
    verbatim_excerpt: str | None = None


class EditorialKnowledgeUnit(StrictModel):
    title: str = Field(min_length=1)
    synthesis: str = Field(min_length=1)
    essential_points: list[str] = Field(min_length=1)
    tensions: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    evidence: list[EditorialEvidence] = Field(min_length=1)


class EditorialCorpus(StrictModel):
    governing_understanding: str = Field(min_length=1)
    units: list[EditorialKnowledgeUnit] = Field(min_length=1)
    forbidden_inferences: list[str] = Field(default_factory=list)


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
    # The key is required in newly generated projects. PostgreSQL normalizes
    # projects created before the editorial corpus existed to an explicit null.
    editorial_corpus: EditorialCorpus | None
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


class ContentReviewIssue(StrictModel):
    severity: str = Field(pattern=r"^(blocking|important|minor)$")
    location: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    required_correction: str = Field(min_length=1)


class ContentReviewProposal(StrictModel):
    approved: bool
    summary: str = Field(min_length=1)
    issues: list[ContentReviewIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def approval_matches_issues(self) -> "ContentReviewProposal":
        blocking = any(item.severity == "blocking" for item in self.issues)
        if self.approved and blocking:
            raise ValueError("an approved content review cannot contain blocking issues")
        if not self.approved and not self.issues:
            raise ValueError("a rejected content review must identify its issues")
        return self


class PreservationReviewProposal(StrictModel):
    approved: bool
    summary: str = Field(min_length=1)
    differences: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def rejection_identifies_differences(self) -> "PreservationReviewProposal":
        if not self.approved and not self.differences:
            raise ValueError("a rejected preservation review must identify differences")
        return self


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
    content_draft: FinalTextProposal | None = None
    content_review: ContentReviewProposal | None = None
    corrected_content: FinalTextProposal | None = None
    styled_text: FinalTextProposal | None = None
    preservation_review: PreservationReviewProposal | None = None
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
