"""Bounded, auditable production contracts for Hefesto V2."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import EditorialReview, FinalArticle, NarrativePlan, StrictModel, SubjectResearch
from .digestion_contracts import ArtifactPointer, EvidenceAnchor, Sha256, canonical_sha256


PRODUCTION_V2_ENGINE_VERSION = "hefesto-production-v2.0"
RESEARCH_V2_PROMPT_VERSION = "production-research-v2.0"
ARCHITECTURE_V2_PROMPT_VERSION = "production-architecture-v2.0"
DRAFT_V2_PROMPT_VERSION = "production-draft-v2.0"
CRITIC_V2_PROMPT_VERSION = "production-critic-v2.0"
EDIT_V2_PROMPT_VERSION = "production-edit-v2.0"


class EvidencePacketFinding(StrictModel):
    source_id: UUID
    source_title: str | None = None
    finding: str = Field(min_length=1, max_length=1200)
    causal_status: str = Field(min_length=1, max_length=100)
    evidence_strength: str = Field(min_length=1, max_length=100)
    qualifications: list[str] = Field(default_factory=list, max_length=4)
    anchors: list[EvidenceAnchor] = Field(min_length=1, max_length=3)
    relevance_score: int = Field(ge=0)


class SubjectEvidencePacket(StrictModel):
    subject_id: UUID
    title: str = Field(min_length=1)
    development: str = Field(min_length=1)
    findings: list[EvidencePacketFinding] = Field(default_factory=list, max_length=100)
    source_limits: list[str] = Field(default_factory=list, max_length=20)
    rendered_context: str = Field(min_length=1, max_length=30000)


class ProductionEvidenceBundle(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    evidence_matrix_sha256: Sha256
    dossier_sha256s: list[Sha256] = Field(min_length=1)
    shared_context: str = Field(min_length=1, max_length=30000)
    subject_packets: list[SubjectEvidencePacket] = Field(min_length=1, max_length=20)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"created_at"}))


class SubjectResearchBundleProposal(StrictModel):
    subjects: list[SubjectResearch] = Field(min_length=1, max_length=20)


class ArticleDraftV2(StrictModel):
    body_markdown: str = Field(min_length=1, max_length=200000)

    @property
    def content_sha256(self) -> str:
        return canonical_sha256({"body_markdown": self.body_markdown})


class ProductionArtifactRefsV2(StrictModel):
    approved_project: ArtifactPointer
    evidence_bundle: ArtifactPointer | None = None
    research_bundle: ArtifactPointer | None = None
    narrative_plan: ArtifactPointer | None = None
    drafts: list[ArtifactPointer] = Field(default_factory=list, max_length=3)
    reviews: list[ArtifactPointer] = Field(default_factory=list, max_length=3)
    final_article: ArtifactPointer | None = None
    production_record: ArtifactPointer | None = None


class ProductionV2State(StrEnum):
    PROJECT_APPROVED = "project_approved"
    EVIDENCE_READY = "evidence_ready"
    RESEARCH_READY = "research_ready"
    ARCHITECTURE_READY = "architecture_ready"
    DRAFT_READY = "draft_ready"
    REVIEWING = "reviewing"
    ARTICLE_READY = "article_ready"
    FAILED = "failed"


class ProductionExecutionRunV2(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    state: ProductionV2State
    completed_model_calls: int = Field(default=0, ge=0)
    revision_rounds: int = Field(default=0, ge=0, le=2)
    artifacts: ProductionArtifactRefsV2
    resume_state: ProductionV2State | None = None
    failed_work_unit_ids: list[Sha256] = Field(default_factory=list)
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _state_is_checkpointed(self) -> "ProductionExecutionRunV2":
        if self.state is ProductionV2State.FAILED:
            if self.resume_state is None or not self.failed_work_unit_ids or not self.error_class:
                raise ValueError("failed production requires resume state, failed work, and error")
            if self.resume_state is ProductionV2State.FAILED:
                raise ValueError("failed cannot be its own resume state")
            return self
        if self.resume_state is not None or self.failed_work_unit_ids or self.error_class or self.error_message:
            raise ValueError("active production cannot retain failure state")
        order = _production_order()
        index = order.index(self.state)
        requirements = [
            (ProductionV2State.EVIDENCE_READY, self.artifacts.evidence_bundle),
            (ProductionV2State.RESEARCH_READY, self.artifacts.research_bundle),
            (ProductionV2State.ARCHITECTURE_READY, self.artifacts.narrative_plan),
        ]
        for required_state, pointer in requirements:
            if index >= order.index(required_state) and pointer is None:
                raise ValueError(f"{required_state.value} requires its artifact")
        if index >= order.index(ProductionV2State.DRAFT_READY) and not self.artifacts.drafts:
            raise ValueError("draft_ready requires a persisted draft")
        if self.state is ProductionV2State.ARTICLE_READY:
            if self.artifacts.final_article is None or self.artifacts.production_record is None:
                raise ValueError("article_ready requires final article and production record")
        if len(self.artifacts.drafts) != self.revision_rounds + 1 and self.artifacts.drafts:
            raise ValueError("draft checkpoints must equal the initial draft plus revisions")
        if len(self.artifacts.reviews) > len(self.artifacts.drafts):
            raise ValueError("a draft can have at most one persisted review")
        return self


class ProductionRecordV2(StrictModel):
    schema_version: int = 2
    execution_run_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    evidence_bundle_sha256: Sha256
    research_bundle: SubjectResearchBundleProposal
    narrative_plan: NarrativePlan
    reviews: list[EditorialReview] = Field(min_length=1, max_length=3)
    final_article: FinalArticle
    model: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _article_matches_record(self) -> "ProductionRecordV2":
        if (
            self.final_article.project_id != self.project_id
            or self.final_article.editorial_project_id != self.editorial_project_id
        ):
            raise ValueError("final article belongs to another production record")
        if self.final_article.revision_rounds != len(self.reviews) - 1:
            raise ValueError("final article revision count must match persisted reviews")
        if self.final_article.quality_score != self.reviews[-1].quality_score:
            raise ValueError("final quality score must come from the last review")
        return self


class ProductionV2Result(StrictModel):
    execution_run: ProductionExecutionRunV2
    production_record: ProductionRecordV2

    @model_validator(mode="after")
    def _ready(self) -> "ProductionV2Result":
        if self.execution_run.state is not ProductionV2State.ARTICLE_READY:
            raise ValueError("production result must be article_ready")
        if self.execution_run.editorial_project_id != self.production_record.editorial_project_id:
            raise ValueError("result project identity mismatch")
        return self


def _production_order() -> list[ProductionV2State]:
    return [
        ProductionV2State.PROJECT_APPROVED,
        ProductionV2State.EVIDENCE_READY,
        ProductionV2State.RESEARCH_READY,
        ProductionV2State.ARCHITECTURE_READY,
        ProductionV2State.DRAFT_READY,
        ProductionV2State.REVIEWING,
        ProductionV2State.ARTICLE_READY,
    ]
