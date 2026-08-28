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
AXIS_FEEDBACK_DECISION_CONTRACT_VERSION = "axis-feedback-decision.v1"
AXIS_FEEDBACK_DECISION_PROMPT_VERSION = "hefesto.axis-feedback-decision.v1"
CARRIED_EDITORIAL_DIRECTIVES_CONTRACT_VERSION = "carried-editorial-directives.v1"


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
    carried_directives_uri: str | None = Field(default=None, pattern=r"^gs://")


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
    REFRAMES = "reframes"


class SupportBasis(StrEnum):
    SOURCES = "sources"
    MODEL = "model"
    ENRICHMENT_REQUIRED = "enrichment_required"


class ResidualSupportBasis(StrEnum):
    SOURCES = "sources"
    MODEL = "model"


class TreatmentScale(StrEnum):
    STRUCTURAL = "structural"
    DEVELOPED = "developed"
    BRIEF = "brief"
    MENTION_ONLY = "mention_only"


class AxisChangeIntent(StrEnum):
    PRESERVED = "preserved"
    REVISED = "revised"


class AxisFeedbackOutcome(StrEnum):
    AXIS_PRESERVED = "axis_preserved"
    AXIS_REVISED = "axis_revised"
    ENRICHMENT_REQUIRED = "enrichment_required"


class AxisFeedbackUserAction(StrEnum):
    SUBMIT_FEEDBACK = "submit_feedback"
    AWAIT_ENRICHMENT = "await_enrichment"
    REFUSE_ENRICHMENT = "refuse_enrichment"


class AxisFeedbackImpactAssessment(StrictModel):
    impact: EditorialImpactKind
    affected_premise: str = Field(min_length=1, max_length=1200)
    editorial_consequences: str = Field(min_length=1, max_length=2000)
    axis_if_supported: AxisChangeIntent


class AxisFeedbackSupportAssessment(StrictModel):
    support_basis: SupportBasis
    support_rationale: str = Field(min_length=1, max_length=2000)
    treatment_scale: TreatmentScale
    enrichment_need: str | None = Field(default=None, max_length=2000)
    carried_directive: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _enrichment_is_inspectable(self) -> "AxisFeedbackSupportAssessment":
        need = (self.enrichment_need or "").strip()
        if self.support_basis is SupportBasis.ENRICHMENT_REQUIRED and not need:
            raise ValueError("enrichment_required needs a compact enrichment need")
        return self


class AxisFeedbackRecalibration(StrictModel):
    impact: EditorialImpactKind
    affected_premise: str = Field(min_length=1, max_length=1200)
    editorial_consequences: str = Field(min_length=1, max_length=2000)
    axis_if_supported: AxisChangeIntent
    support_basis: ResidualSupportBasis
    support_rationale: str = Field(min_length=1, max_length=2000)
    treatment_scale: TreatmentScale
    carried_directive: str | None = Field(default=None, max_length=2000)


class CarriedEditorialDirective(StrictModel):
    directive_id: UUID = Field(default_factory=uuid4)
    decision_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    treatment_scale: TreatmentScale
    support_basis: SupportBasis
    impact: EditorialImpactKind
    origin_feedback: str = Field(min_length=1, max_length=4000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CarriedEditorialDirectiveSet(StrictModel):
    schema_version: int = 1
    contract_version: str = Field(min_length=1)
    planning_run_id: UUID
    project_id: UUID
    directives: list[CarriedEditorialDirective] = Field(default_factory=list, max_length=40)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AxisFeedbackDecision(StrictModel):
    schema_version: int = 1
    decision_id: UUID = Field(default_factory=uuid4)
    contract_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    planning_run_id: UUID
    project_id: UUID
    source_lineage_id: UUID
    understanding_id: UUID
    current_axis_id: UUID
    resulting_axis_id: UUID
    parent_decision_id: UUID | None = None
    user_action: AxisFeedbackUserAction
    feedback: str = Field(min_length=1, max_length=4000)
    impact: EditorialImpactKind
    affected_premise: str = Field(min_length=1, max_length=1200)
    editorial_consequences: str = Field(min_length=1, max_length=2000)
    axis_if_supported: AxisChangeIntent
    support_basis: SupportBasis
    support_rationale: str = Field(min_length=1, max_length=2000)
    treatment_scale: TreatmentScale
    outcome: AxisFeedbackOutcome
    enrichment_need: str | None = Field(default=None, max_length=2000)
    enrichment_refused: bool = False
    enrichment_awaited_at: datetime | None = None
    carried_directive: str | None = Field(default=None, max_length=2000)
    evidence_review: NarrativeAxisEvidenceReview | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _inspectable_outcome(self) -> "AxisFeedbackDecision":
        if self.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED:
            if self.support_basis is not SupportBasis.ENRICHMENT_REQUIRED:
                raise ValueError("enrichment outcome requires enrichment_required support")
            if not (self.enrichment_need or "").strip():
                raise ValueError("enrichment outcome needs a compact enrichment need")
            if self.resulting_axis_id != self.current_axis_id:
                raise ValueError("enrichment_required cannot rewrite the Axis")
            if self.enrichment_refused:
                raise ValueError("refusing enrichment cannot leave enrichment_required in place")
        else:
            if self.support_basis is SupportBasis.ENRICHMENT_REQUIRED:
                raise ValueError("supported outcomes cannot rest on enrichment_required")
            if self.outcome is AxisFeedbackOutcome.AXIS_PRESERVED and self.resulting_axis_id != self.current_axis_id:
                raise ValueError("preserved Axis must keep the current Axis id")
            if self.outcome is AxisFeedbackOutcome.AXIS_REVISED and self.resulting_axis_id == self.current_axis_id:
                raise ValueError("revised Axis must be a new Axis id")
            if self.outcome is AxisFeedbackOutcome.AXIS_PRESERVED and not (self.carried_directive or "").strip():
                raise ValueError("preserving the Axis requires a carried editorial directive")
        if self.enrichment_refused and self.support_basis is SupportBasis.ENRICHMENT_REQUIRED:
            raise ValueError("refusing enrichment cannot invent enrichment_required support")
        return self


class AxisFeedbackDecisionResult(StrictModel):
    planning_run: PlanningRun
    editorial_understanding: EditorialUnderstanding
    narrative_axis: NarrativeAxis
    decision: AxisFeedbackDecision
    carried_editorial_directives: list[CarriedEditorialDirective] = Field(default_factory=list, max_length=40)
    proposed_axis: str = Field(min_length=1, max_length=800)
    enrichment_need: str | None = Field(default=None, max_length=2000)
    human_approval_required: bool = True
    editorial_project_created: bool = False
    enrichment_search_performed: bool = False
    graph_reextracted: bool = False
    understanding_rebuilt: bool = False
    stopped_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _stop_for_human_approval(self) -> "AxisFeedbackDecisionResult":
        if self.planning_run.state is not PlanningRunState.AXIS_PROPOSED:
            raise ValueError("axis feedback must remain stopped at axis_proposed")
        if self.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            raise ValueError("axis feedback cannot auto-approve the Narrative Axis")
        if self.narrative_axis.approved_at is not None:
            raise ValueError("axis feedback cannot record Axis approval")
        if self.proposed_axis != self.narrative_axis.statement:
            raise ValueError("proposed_axis must match the current Narrative Axis")
        if not self.human_approval_required:
            raise ValueError("axis feedback requires human approval")
        if self.editorial_project_created:
            raise ValueError("axis feedback cannot create an Editorial Project")
        if self.enrichment_search_performed:
            raise ValueError("axis feedback cannot search or ingest sources")
        if self.graph_reextracted:
            raise ValueError("axis feedback cannot reextract or reindex the graph")
        if self.understanding_rebuilt:
            raise ValueError("axis feedback cannot rebuild editorial understanding")
        if self.decision.understanding_id != self.editorial_understanding.understanding_id:
            raise ValueError("decision must reuse the persisted editorial understanding")
        if self.decision.outcome is AxisFeedbackOutcome.ENRICHMENT_REQUIRED:
            if self.stopped_at != "enrichment_required":
                raise ValueError("unsupported contribution must stop at enrichment_required")
            if not (self.enrichment_need or "").strip():
                raise ValueError("enrichment_required result needs a compact enrichment need")
            if self.narrative_axis.narrative_axis_id != self.decision.current_axis_id:
                raise ValueError("enrichment_required cannot rewrite the Axis")
        else:
            if self.stopped_at != "axis_proposed":
                raise ValueError("supported feedback must stop at axis_proposed")
        if self.decision.outcome is AxisFeedbackOutcome.AXIS_PRESERVED:
            if not self.carried_editorial_directives:
                raise ValueError("preserved Axis must carry editorial directives forward")
            if self.narrative_axis.narrative_axis_id != self.decision.current_axis_id:
                raise ValueError("preserved Axis must keep the current Axis")
        if self.decision.outcome is AxisFeedbackOutcome.AXIS_REVISED:
            if self.narrative_axis.narrative_axis_id != self.decision.resulting_axis_id:
                raise ValueError("revised result must expose the new Axis")
            if self.narrative_axis.parent_axis_id != self.decision.current_axis_id:
                raise ValueError("revised Axis must supersede the previous Axis")
        return self
