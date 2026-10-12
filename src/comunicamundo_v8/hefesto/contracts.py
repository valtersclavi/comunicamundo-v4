from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditorialBrief(StrictModel):
    theme: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    duration_minutes: int = Field(ge=1, le=90)
    output_language: str = Field(min_length=2)
    audience: str = Field(min_length=1)
    product: str = Field(min_length=1)


class SourceMode(StrEnum):
    PROVIDED = "provided"


class SourceInput(StrictModel):
    uri: str = Field(pattern=r"^(gs://|https://)", min_length=8)
    title: str | None = None
    language: str = Field(default="und", min_length=2)
    content_type: str | None = None
    doi: str | None = None
    publisher: str | None = None


class EditorialInputRequest(StrictModel):
    project_id: UUID = Field(default_factory=uuid4)
    brief: EditorialBrief
    source_mode: SourceMode = SourceMode.PROVIDED
    sources: list[SourceInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_mode_matches_sources(self) -> "EditorialInputRequest":
        if self.source_mode is not SourceMode.PROVIDED:
            raise ValueError("only the provided source mode is supported")
        if not self.sources:
            raise ValueError("provided source mode requires at least one source")
        return self


class SubjectSearchQuery(StrictModel):
    subject_index: int = Field(ge=0)
    query: str = Field(min_length=1)
    provider: str = Field(pattern=r"^(you|openalex)$")


class SourceSearchPlan(StrictModel):
    queries: list[SubjectSearchQuery] = Field(min_length=1)
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


class BoundingBox(StrictModel):
    left: float
    top: float
    right: float
    bottom: float
    coord_origin: str | None = None


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
    element_type: str = "paragraph"
    page_no: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    canonical_start: int | None = Field(default=None, ge=0)
    canonical_end: int | None = Field(default=None, ge=0)
    origin_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def canonical_span_is_ordered(self) -> "Paragraph":
        if (self.canonical_start is None) != (self.canonical_end is None):
            raise ValueError("canonical offsets must be provided together")
        if (
            self.canonical_start is not None
            and self.canonical_end is not None
            and self.canonical_end <= self.canonical_start
        ):
            raise ValueError("canonical end must follow canonical start")
        return self


class SourceAsset(StrictModel):
    asset_id: Sha256
    source_id: UUID
    source_ordinal: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    kind: str = Field(pattern=r"^(table|figure|chart|unknown_picture)$")
    role: str = Field(pattern=r"^(content|decorative|unknown)$")
    page_no: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    caption: str | None = None
    text_representation: str | None = None
    blob_uri: str | None = Field(default=None, pattern=r"^gs://")
    structured_uri: str | None = Field(default=None, pattern=r"^gs://")
    binary_sha256: Sha256 | None = None
    origin_ref: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)


class ParagraphAssetLink(StrictModel):
    paragraph_id: Sha256
    asset_id: Sha256
    relation: str = Field(
        pattern=r"^(anchor_after|anchor_before|caption|context_before|context_after)$"
    )
    distance: int = Field(ge=0)
    primary: bool = False
    payload: dict[str, object] = Field(default_factory=dict)


class NormalizedSource(StrictModel):
    source_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    language: str = Field(min_length=2)
    title: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    assets: list[SourceAsset] = Field(default_factory=list)
    asset_links: list[ParagraphAssetLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def structured_content_belongs_to_source(self) -> "NormalizedSource":
        if any(item.source_id != self.source_id for item in self.paragraphs):
            raise ValueError("normalized paragraphs must belong to their source")
        if any(item.source_id != self.source_id for item in self.assets):
            raise ValueError("normalized assets must belong to their source")
        paragraph_ids = {item.paragraph_id for item in self.paragraphs}
        asset_ids = {item.asset_id for item in self.assets}
        if any(
            item.paragraph_id not in paragraph_ids or item.asset_id not in asset_ids
            for item in self.asset_links
        ):
            raise ValueError("asset links must reference normalized source content")
        return self


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
    tables: int = Field(default=0, ge=0)
    figures: int = Field(default=0, ge=0)
    normalization_status: str = Field(
        default="normalized", pattern=r"^(normalized|partial)$"
    )
    normalization_receipt_uri: str | None = Field(default=None, pattern=r"^gs://")
    binary_sha256: Sha256
    normalized_text_sha256: Sha256
    acquisition: AcquiredSource | None = None


class SourceNormalizationFailure(StrictModel):
    input_uri: str
    title: str | None = None
    failed_step: str | None = None
    error_class: str
    error_message: str


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
    failed_normalizations: list[SourceNormalizationFailure] = Field(default_factory=list)


def content_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
