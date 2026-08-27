"""Stable product contracts for the clean Hefesto V4 rebuild."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditorialBrief(StrictModel):
    theme: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    duration_minutes: int = Field(ge=1, le=240)
    output_language: str = Field(min_length=2, default="pt-BR")
    audience: str | None = Field(default=None, min_length=1)
    product: str = Field(default="article", min_length=1)


class NormalizedSource(StrictModel):
    source_id: UUID
    text: str = Field(min_length=1)
    language: str = Field(min_length=2)
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NarrativeAxisStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class NarrativeAxisProposal(StrictModel):
    statement: str = Field(min_length=1, max_length=800)


class NarrativeAxis(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    narrative_axis_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    parent_axis_id: UUID | None = None
    statement: str = Field(min_length=1, max_length=800)
    status: NarrativeAxisStatus = NarrativeAxisStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    def approve(self) -> "NarrativeAxis":
        if self.status is NarrativeAxisStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Narrative Axis")
        if self.status is NarrativeAxisStatus.APPROVED:
            return self
        return self.model_copy(update={"status": NarrativeAxisStatus.APPROVED, "approved_at": datetime.now(UTC)})

    def supersede(self) -> "NarrativeAxis":
        if self.status is NarrativeAxisStatus.APPROVED:
            raise ValueError("approved Narrative Axis is immutable")
        if self.status is NarrativeAxisStatus.SUPERSEDED:
            return self
        return self.model_copy(update={"status": NarrativeAxisStatus.SUPERSEDED})


class EditorialSubjectProposal(StrictModel):
    title: str = Field(min_length=1)
    development: str = Field(min_length=1)
    practical_dimension: str | None = Field(default=None, min_length=1)


class EditorialProjectProposal(StrictModel):
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    core_subjects: list[EditorialSubjectProposal] = Field(min_length=1, max_length=20)
    llm_subjects: list[str] = Field(default_factory=list, max_length=12)
    enrichment_opportunities: list[str] = Field(default_factory=list, max_length=12)


class EditorialSubject(StrictModel):
    subject_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    development: str = Field(min_length=1)
    practical_dimension: str | None = Field(default=None, min_length=1)


class EditorialProjectStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class EditorialProject(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    editorial_project_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    parent_project_id: UUID | None = None
    narrative_axis_id: UUID
    narrative_axis: str = Field(min_length=1, max_length=800)
    brief: EditorialBrief
    working_title: str = Field(min_length=1)
    editorial_promise: str = Field(min_length=1)
    core_subjects: list[EditorialSubject] = Field(min_length=1, max_length=20)
    llm_subjects: list[str] = Field(default_factory=list, max_length=12)
    enrichment_opportunities: list[str] = Field(default_factory=list, max_length=12)
    status: EditorialProjectStatus = EditorialProjectStatus.PROPOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None

    def approve(self) -> "EditorialProject":
        if self.status is EditorialProjectStatus.SUPERSEDED:
            raise ValueError("cannot approve a superseded Editorial Project")
        if self.status is EditorialProjectStatus.APPROVED:
            return self
        return self.model_copy(update={"status": EditorialProjectStatus.APPROVED, "approved_at": datetime.now(UTC)})

    def supersede(self) -> "EditorialProject":
        if self.status is EditorialProjectStatus.APPROVED:
            raise ValueError("approved Editorial Project is immutable")
        if self.status is EditorialProjectStatus.SUPERSEDED:
            return self
        return self.model_copy(update={"status": EditorialProjectStatus.SUPERSEDED})


class SubjectResearch(StrictModel):
    subject_id: UUID
    synthesis: str = Field(min_length=1)


class NarrativeBeat(StrictModel):
    subject_id: UUID
    narrative_role: str = Field(min_length=1)
    transition: str = Field(min_length=1)


class NarrativePlan(StrictModel):
    title: str = Field(min_length=1)
    opening_move: str = Field(min_length=1)
    beats: list[NarrativeBeat] = Field(min_length=1, max_length=20)
    closing_move: str = Field(min_length=1)
    voice_notes: list[str] = Field(default_factory=list, max_length=8)


class EditorialReview(StrictModel):
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
    revision_rounds: int = Field(ge=0, le=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningRequest(StrictModel):
    project_id: UUID
    brief: EditorialBrief
    sources: list[NormalizedSource] = Field(min_length=1)


class PlanningRunState(StrEnum):
    REQUEST_VALIDATED = "request_validated"
    SOURCE_CHUNKS_READY = "source_chunks_ready"
    GRAPH_EXTRACTIONS_READY = "graph_extractions_ready"
    GRAPH_READY = "graph_ready"
    UNDERSTANDING_READY = "understanding_ready"
    AXIS_PROPOSED = "axis_proposed"


class ChunkStatus(StrEnum):
    PREPARED = "prepared"
    EXTRACTED = "extracted"
    FAILED = "failed"


class PreparedChunk(StrictModel):
    chunk_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: UUID
    source_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    sequence: int = Field(ge=0)
    text: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ChunkStatus = ChunkStatus.PREPARED
    extraction_ref: str | None = None


class ChunkManifestEntry(StrictModel):
    chunk_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: UUID
    source_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    sequence: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ChunkStatus = ChunkStatus.PREPARED
    chunk_uri: str = Field(pattern=r"^gs://")
    extraction_ref: str | None = None


class ChunkManifest(StrictModel):
    schema_version: int = 1
    source_lineage_id: UUID
    project_id: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_count: int = Field(ge=1)
    chunks: list[ChunkManifestEntry] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


GRAPH_EXTRACTION_CONTRACT_VERSION = "graph-extraction.v1"
GRAPH_EXTRACTOR_VERSION = "hefesto.simple-llm-path.v1"


class SourceLineageState(StrEnum):
    CREATED = "created"
    CHUNKS_READY = "chunks_ready"
    GRAPH_EXTRACTIONS_IN_PROGRESS = "graph_extractions_in_progress"
    GRAPH_EXTRACTIONS_READY = "graph_extractions_ready"
    GRAPH_INDEXING_IN_PROGRESS = "graph_indexing_in_progress"
    GRAPH_READY = "graph_ready"


class GraphExtractionUnitStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    FAILED = "failed"


class GraphEntity(StrictModel):
    name: str = Field(min_length=1)
    label: str = Field(default="entity", min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelation(StrictModel):
    source: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphExtractionUnit(StrictModel):
    source_lineage_id: UUID
    chunk_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: UUID
    text: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    sequence: int = Field(ge=0)


class GraphExtraction(StrictModel):
    contract_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    source_lineage_id: UUID
    source_id: UUID
    chunk_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entities: list[GraphEntity] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GraphExtractionManifestEntry(StrictModel):
    chunk_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: GraphExtractionUnitStatus = GraphExtractionUnitStatus.PENDING
    chunk_uri: str = Field(pattern=r"^gs://")
    extraction_uri: str | None = Field(default=None, pattern=r"^gs://")
    contract_version: str | None = None
    extractor_version: str | None = None
    model: str | None = None
    error: str | None = None


class GraphExtractionManifest(StrictModel):
    schema_version: int = 1
    source_lineage_id: UUID
    project_id: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    chunk_count: int = Field(ge=1)
    extracted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    entries: list[GraphExtractionManifestEntry] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


GRAPH_ASSEMBLY_CONTRACT_VERSION = "graph-assembly.v1"
GRAPH_INDEXER_VERSION = "hefesto.property-graph-index.v1"


class GraphArtifactFile(StrictModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GraphArtifact(StrictModel):
    project_id: UUID
    artifact_id: UUID
    bucket: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    manifest_uri: str = Field(pattern=r"^gs://")
    files: list[GraphArtifactFile] = Field(min_length=1)
    created_at: datetime


class GraphReadyManifest(StrictModel):
    schema_version: int = 1
    contract_version: str = Field(min_length=1)
    indexer_version: str = Field(min_length=1)
    source_lineage_id: UUID
    project_id: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_manifest_uri: str = Field(pattern=r"^gs://")
    extraction_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_contract_version: str = Field(min_length=1)
    extraction_extractor_version: str = Field(min_length=1)
    extraction_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_package_version: str = Field(min_length=1)
    embedding_config: dict[str, Any] = Field(default_factory=dict)
    graph_artifact_id: UUID
    graph_artifact_version: str = Field(min_length=1)
    graph_artifact: GraphArtifact
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceLineage(StrictModel):
    source_lineage_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: SourceLineageState = SourceLineageState.CREATED
    chunk_manifest_uri: str | None = Field(default=None, pattern=r"^gs://")
    chunk_count: int | None = Field(default=None, ge=0)
    graph_extractions_manifest_uri: str | None = Field(default=None, pattern=r"^gs://")
    extracted_count: int | None = Field(default=None, ge=0)
    failed_count: int | None = Field(default=None, ge=0)
    graph_manifest_uri: str | None = Field(default=None, pattern=r"^gs://")
    graph_artifact_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningArtifactRefs(StrictModel):
    request_uri: str | None = Field(default=None, pattern=r"^gs://")
    chunk_manifest_uri: str | None = Field(default=None, pattern=r"^gs://")
    graph_extractions_uri: str | None = Field(default=None, pattern=r"^gs://")
    graph_artifact_id: UUID | None = None
    understanding_uri: str | None = Field(default=None, pattern=r"^gs://")
    narrative_axis_id: UUID | None = None


class PlanningRun(StrictModel):
    planning_run_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_lineage_id: UUID
    state: PlanningRunState
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: PlanningArtifactRefs = Field(default_factory=PlanningArtifactRefs)
    chunk_count: int | None = Field(default=None, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
