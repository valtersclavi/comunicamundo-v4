"""Stable product contracts for the clean Hefesto V4 rebuild."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class NarrativeAxisEvidenceReview(StrictModel):
    candidate_statement: str = Field(min_length=1, max_length=800)
    revision_required: bool
    unsupported_upgrades: list[str] = Field(default_factory=list, max_length=8)
    final_statement: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def _inspectable_decision(self) -> "NarrativeAxisEvidenceReview":
        if self.revision_required and not [item for item in self.unsupported_upgrades if item.strip()]:
            raise ValueError("revision requires compact unsupported-upgrade reasons")
        return self


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


EDITORIAL_UNDERSTANDING_CONTRACT_VERSION = "editorial-understanding.v1"
EDITORIAL_UNDERSTANDING_PROMPT_VERSION = "hefesto.editorial-understanding.v1"
NARRATIVE_AXIS_CONTRACT_VERSION = "narrative-axis.v1"
NARRATIVE_AXIS_PROMPT_VERSION = "hefesto.narrative-axis.v3"
AXIS_FEEDBACK_CONTRACT_VERSION = "axis-feedback.v1"
AXIS_FEEDBACK_PROMPT_VERSION = "hefesto.axis-feedback.v1"


class EditorialUnderstandingProposal(StrictModel):
    central_ideas: list[str] = Field(min_length=1, max_length=20)
    relations_and_tensions: list[str] = Field(min_length=1, max_length=20)
    nuances: list[str] = Field(default_factory=list, max_length=16)
    source_limits: list[str] = Field(default_factory=list, max_length=16)
    editorial_opportunities: list[str] = Field(default_factory=list, max_length=16)
    synthesis: str = Field(min_length=1, max_length=12000)


class EditorialUnderstanding(StrictModel):
    schema_version: int = 1
    understanding_id: UUID = Field(default_factory=uuid4)
    planning_run_id: UUID
    project_id: UUID
    source_lineage_id: UUID
    contract_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_artifact_id: UUID
    graph_manifest_uri: str = Field(pattern=r"^gs://")
    graph_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_manifest_uri: str = Field(pattern=r"^gs://")
    chunk_ids: list[str] = Field(min_length=1)
    graph_entity_count: int = Field(ge=0)
    graph_relation_count: int = Field(ge=0)
    central_ideas: list[str] = Field(min_length=1, max_length=20)
    relations_and_tensions: list[str] = Field(min_length=1, max_length=20)
    nuances: list[str] = Field(default_factory=list, max_length=16)
    source_limits: list[str] = Field(default_factory=list, max_length=16)
    editorial_opportunities: list[str] = Field(default_factory=list, max_length=16)
    synthesis: str = Field(min_length=1, max_length=12000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersistedNarrativeAxis(StrictModel):
    schema_version: int = 1
    contract_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    planning_run_id: UUID
    project_id: UUID
    source_lineage_id: UUID
    understanding_id: UUID
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_artifact_id: UUID
    graph_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    narrative_axis: NarrativeAxis
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningArtifactRefs(StrictModel):
    request_uri: str | None = Field(default=None, pattern=r"^gs://")
    chunk_manifest_uri: str | None = Field(default=None, pattern=r"^gs://")
    graph_extractions_uri: str | None = Field(default=None, pattern=r"^gs://")
    graph_artifact_id: UUID | None = None
    understanding_uri: str | None = Field(default=None, pattern=r"^gs://")
    narrative_axis_id: UUID | None = None
    narrative_axis_uri: str | None = Field(default=None, pattern=r"^gs://")
    axis_feedback_decision_id: UUID | None = None
    axis_feedback_decision_uri: str | None = Field(default=None, pattern=r"^gs://")


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


class AxisProposalResult(StrictModel):
    planning_run: PlanningRun
    editorial_understanding: EditorialUnderstanding
    narrative_axis: NarrativeAxis
    proposed_axis: str = Field(min_length=1, max_length=800)
    human_approval_required: bool = True
    editorial_project_created: bool = False
    stopped_at: str = "axis_proposed"

    @model_validator(mode="after")
    def _stop_for_human_approval(self) -> "AxisProposalResult":
        if self.planning_run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("PlanningRun must stop at axis_proposed")
        if self.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            raise ValueError("axis_proposed cannot auto-approve the Narrative Axis")
        if self.narrative_axis.approved_at is not None:
            raise ValueError("axis_proposed cannot record Axis approval")
        if self.proposed_axis != self.narrative_axis.statement:
            raise ValueError("proposed_axis must match the persisted Narrative Axis")
        if not self.human_approval_required:
            raise ValueError("axis_proposed requires human approval")
        if self.editorial_project_created:
            raise ValueError("axis_proposed cannot create an Editorial Project")
        if self.stopped_at != "axis_proposed":
            raise ValueError("entrega 3 must stop at axis_proposed")
        return self


class EditorialImpactKind(StrEnum):
    COMPLEMENTS = "complements"
    QUALIFIES = "qualifies"
    REFORMULATES = "reformulates"


class SupportKind(StrEnum):
    SOURCES = "sources"
    MODEL = "model"
    ENRICHMENT = "enrichment"


class AxisAction(StrEnum):
    KEEP = "keep"
    REVISE = "revise"
    HOLD_FOR_ENRICHMENT = "hold_for_enrichment"


class TreatmentScale(StrEnum):
    BRIEF = "brief"
    INTEGRATED = "integrated"
    SUBSTANTIAL = "substantial"


class AxisFeedbackDecisionState(StrEnum):
    DECIDED = "decided"
    ENRICHMENT_REQUIRED = "enrichment_required"
    ENRICHMENT_ACCEPTED = "enrichment_accepted"
    ENRICHMENT_DECLINED = "enrichment_declined"


class EnrichmentResolutionChoice(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"


class CarryForwardCommitment(StrictModel):
    intent: str = Field(min_length=1, max_length=4000)
    treatment_scale: TreatmentScale
    rationale: str = Field(min_length=1, max_length=4000)


class AxisFeedbackDecisionProposal(StrictModel):
    impact: EditorialImpactKind
    affected_premises: list[str] = Field(min_length=1, max_length=12)
    editorial_consequences: list[str] = Field(min_length=1, max_length=12)
    impact_rationale: str = Field(min_length=1, max_length=4000)
    support: SupportKind
    support_rationale: str = Field(min_length=1, max_length=4000)
    axis_action: AxisAction
    treatment_scale: TreatmentScale
    treatment_rationale: str = Field(min_length=1, max_length=4000)
    enrichment_need: str | None = Field(default=None, min_length=1, max_length=4000)
    decision_rationale: str = Field(min_length=1, max_length=4000)
    carry_forward_intent: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _coherent_decision(self) -> "AxisFeedbackDecisionProposal":
        if self.support is SupportKind.ENRICHMENT:
            if self.axis_action is not AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("enrichment support must hold the Axis pending enrichment")
            if not (self.enrichment_need and self.enrichment_need.strip()):
                raise ValueError("enrichment support requires an enrichment_need")
        else:
            if self.axis_action is AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("sources/model support cannot hold the Axis for enrichment")
            if self.enrichment_need and self.enrichment_need.strip():
                raise ValueError("enrichment_need is only valid when support is enrichment")
        if self.axis_action is AxisAction.KEEP and not (self.carry_forward_intent and self.carry_forward_intent.strip()):
            raise ValueError("keeping the Axis requires a carry-forward commitment")
        return self


class BoundedRecalibrationProposal(StrictModel):
    support: SupportKind
    support_rationale: str = Field(min_length=1, max_length=4000)
    axis_action: AxisAction
    treatment_scale: TreatmentScale
    treatment_rationale: str = Field(min_length=1, max_length=4000)
    decision_rationale: str = Field(min_length=1, max_length=4000)
    carry_forward_intent: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _bounded_support(self) -> "BoundedRecalibrationProposal":
        if self.support is SupportKind.ENRICHMENT:
            raise ValueError("declining enrichment cannot request enrichment again")
        if self.axis_action is AxisAction.HOLD_FOR_ENRICHMENT:
            raise ValueError("declining enrichment cannot hold the Axis for enrichment")
        if self.axis_action is AxisAction.KEEP and not (self.carry_forward_intent and self.carry_forward_intent.strip()):
            raise ValueError("keeping the Axis after decline requires a carry-forward commitment")
        return self


class AxisFeedbackDecision(StrictModel):
    schema_version: int = 1
    feedback_decision_id: UUID = Field(default_factory=uuid4)
    contract_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    planning_run_id: UUID
    project_id: UUID
    source_lineage_id: UUID
    understanding_id: UUID
    current_narrative_axis_id: UUID
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    feedback: str = Field(min_length=1, max_length=8000)
    feedback_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: AxisFeedbackDecisionState
    impact: EditorialImpactKind
    affected_premises: list[str] = Field(min_length=1, max_length=12)
    editorial_consequences: list[str] = Field(min_length=1, max_length=12)
    impact_rationale: str = Field(min_length=1, max_length=4000)
    support: SupportKind
    support_rationale: str = Field(min_length=1, max_length=4000)
    axis_action: AxisAction
    treatment_scale: TreatmentScale
    treatment_rationale: str = Field(min_length=1, max_length=4000)
    enrichment_need: str | None = Field(default=None, min_length=1, max_length=4000)
    decision_rationale: str = Field(min_length=1, max_length=4000)
    carry_forward: CarryForwardCommitment | None = None
    user_resolution: EnrichmentResolutionChoice | None = None
    resulting_narrative_axis_id: UUID | None = None
    previous_feedback_decision_id: UUID | None = None
    previous_feedback_decision_uri: str | None = Field(default=None, pattern=r"^gs://")
    human_approval_required: bool = True
    editorial_project_created: bool = False
    search_performed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _durable_invariants(self) -> "AxisFeedbackDecision":
        if not self.human_approval_required:
            raise ValueError("axis feedback cannot auto-approve")
        if self.editorial_project_created:
            raise ValueError("axis feedback cannot create an Editorial Project")
        if self.search_performed:
            raise ValueError("axis feedback cannot search or retrieve sources")
        if (self.previous_feedback_decision_id is None) != (self.previous_feedback_decision_uri is None):
            raise ValueError("previous feedback decision id and uri must be set together")
        if self.previous_feedback_decision_id == self.feedback_decision_id:
            raise ValueError("feedback decision cannot be its own predecessor")
        if self.state is AxisFeedbackDecisionState.ENRICHMENT_REQUIRED:
            if self.support is not SupportKind.ENRICHMENT:
                raise ValueError("enrichment_required requires enrichment support")
            if self.axis_action is not AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("enrichment_required must hold the Axis")
            if self.user_resolution is not None:
                raise ValueError("enrichment_required is unresolved")
            if self.resulting_narrative_axis_id is not None:
                raise ValueError("enrichment_required cannot revise the Axis")
            if not (self.enrichment_need and self.enrichment_need.strip()):
                raise ValueError("enrichment_required needs a missing-evidence description")
        elif self.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED:
            if self.user_resolution is not EnrichmentResolutionChoice.ACCEPT:
                raise ValueError("enrichment_accepted requires user acceptance")
            if self.support is not SupportKind.ENRICHMENT:
                raise ValueError("accepted enrichment remains an enrichment need")
            if self.axis_action is not AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("accepted enrichment must keep the Axis held")
            if self.resulting_narrative_axis_id is not None:
                raise ValueError("accepted enrichment cannot revise the Axis")
        elif self.state is AxisFeedbackDecisionState.ENRICHMENT_DECLINED:
            if self.user_resolution is not EnrichmentResolutionChoice.DECLINE:
                raise ValueError("enrichment_declined requires user refusal")
            if self.support is SupportKind.ENRICHMENT:
                raise ValueError("declining enrichment must bound support to sources or model")
            if self.axis_action is AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("declining enrichment cannot keep the Axis held")
            if not (self.enrichment_need and self.enrichment_need.strip()):
                raise ValueError("declined enrichment must retain the refused need")
        elif self.state is AxisFeedbackDecisionState.DECIDED:
            if self.user_resolution is not None:
                raise ValueError("decided feedback has no enrichment resolution")
            if self.support is SupportKind.ENRICHMENT or self.axis_action is AxisAction.HOLD_FOR_ENRICHMENT:
                raise ValueError("decided feedback cannot remain pending enrichment")
        else:
            raise ValueError("unknown axis feedback state")
        if self.axis_action is AxisAction.REVISE and self.resulting_narrative_axis_id is None:
            raise ValueError("revising the Axis requires a resulting Narrative Axis id")
        if self.axis_action is not AxisAction.REVISE and self.resulting_narrative_axis_id is not None:
            raise ValueError("a resulting Axis is only valid when the Axis is revised")
        if self.axis_action is AxisAction.KEEP and self.carry_forward is None:
            raise ValueError("keeping the Axis requires a carry-forward commitment")
        return self


class AxisFeedbackReviewResult(StrictModel):
    planning_run: PlanningRun
    editorial_understanding: EditorialUnderstanding
    narrative_axis: NarrativeAxis
    decision: AxisFeedbackDecision
    proposed_axis: str = Field(min_length=1, max_length=800)
    human_approval_required: bool = True
    editorial_project_created: bool = False
    search_performed: bool = False
    research_needed: bool = False
    stopped_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _stop_for_human(self) -> "AxisFeedbackReviewResult":
        if self.planning_run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("axis feedback must keep PlanningRun at axis_proposed")
        if self.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            raise ValueError("axis feedback cannot auto-approve the Narrative Axis")
        if self.narrative_axis.approved_at is not None:
            raise ValueError("axis feedback cannot record Axis approval")
        if self.proposed_axis != self.narrative_axis.statement:
            raise ValueError("proposed_axis must match the active Narrative Axis")
        if not self.human_approval_required:
            raise ValueError("axis feedback requires human approval")
        if self.editorial_project_created or self.decision.editorial_project_created:
            raise ValueError("axis feedback cannot create an Editorial Project")
        if self.search_performed or self.decision.search_performed:
            raise ValueError("axis feedback cannot search or retrieve sources")
        pending_enrichment = self.decision.state in {
            AxisFeedbackDecisionState.ENRICHMENT_REQUIRED,
            AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED,
        }
        if pending_enrichment:
            if self.stopped_at != "enrichment_required":
                raise ValueError("unresolved or accepted enrichment must stop at enrichment_required")
            if self.narrative_axis.narrative_axis_id != self.decision.current_narrative_axis_id:
                raise ValueError("enrichment must leave the current Axis intact")
            if self.decision.resulting_narrative_axis_id is not None:
                raise ValueError("enrichment cannot supersede the current Axis")
        else:
            if self.stopped_at != "axis_proposed":
                raise ValueError("bounded or decided feedback must remain at the Axis human-gate")
        if self.research_needed != (self.decision.state is AxisFeedbackDecisionState.ENRICHMENT_ACCEPTED):
            raise ValueError("research_needed is only true after accepted enrichment")
        if self.decision.axis_action is AxisAction.REVISE:
            if self.narrative_axis.narrative_axis_id != self.decision.resulting_narrative_axis_id:
                raise ValueError("revised Axis must be the active Narrative Axis")
            if self.narrative_axis.narrative_axis_id == self.decision.current_narrative_axis_id:
                raise ValueError("a revision must persist a new Narrative Axis")
        elif self.narrative_axis.narrative_axis_id != self.decision.current_narrative_axis_id:
            raise ValueError("keep/hold decisions must retain the current Axis")
        return self

