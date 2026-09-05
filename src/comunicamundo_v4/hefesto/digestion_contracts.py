"""Evidence-preserving contracts for the graph-free Hefesto V2 digestion path."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from math import isclose
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, model_validator

from .contracts import StrictModel


SHA256_PATTERN = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
EvidenceKey = Annotated[str, Field(pattern=r"^E_[0-9a-f]{16}$")]
CompactText = Annotated[str, Field(min_length=1)]
ShortText = Annotated[str, Field(min_length=1)]

DIGESTION_ENGINE_VERSION = "hefesto.source-digestion.v15"
SECTION_DIGEST_PROMPT_VERSION = "hefesto.section-digest.v13"
SOURCE_DOSSIER_PROMPT_VERSION = "hefesto.source-dossier.v13"
EVIDENCE_MATRIX_PROMPT_VERSION = "hefesto.evidence-matrix.v11"
EDITORIAL_UNDERSTANDING_V2_CONTRACT_VERSION = "editorial-understanding.v2"


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()


class DigestionMode(StrEnum):
    DIRECT = "direct"
    HIERARCHICAL = "hierarchical"


class CausalStatus(StrEnum):
    NO_CAUSAL_CLAIM = "no_causal_claim"
    CORRELATIONAL = "correlational"
    LONGITUDINAL_ASSOCIATION = "longitudinal_association"
    QUASI_EXPERIMENTAL = "quasi_experimental"
    CAUSAL = "causal"
    MIXED = "mixed"
    UNCLEAR = "unclear"

    @classmethod
    def _missing_(cls, value: object) -> "CausalStatus | None":
        # Outcome direction and causal design are separate dimensions. Model
        # transports sometimes put a null-outcome label in this field even
        # though the finding text already preserves the null result. Such a
        # label makes no causal claim, so normalize it without discarding the
        # finding or weakening the persisted epistemic vocabulary.
        if isinstance(value, str) and value.strip().lower() in {
            "null_finding",
            "null_result",
            "no_effect",
            "no_association",
        }:
            return cls.NO_CAUSAL_CLAIM
        if isinstance(value, str) and value.strip().lower() in {
            "temporal_association",
            "temporal",
        }:
            return cls.LONGITUDINAL_ASSOCIATION
        return None


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    LIMITED = "limited"
    INDETERMINATE = "indeterminate"

    @classmethod
    def _missing_(cls, value: object) -> "EvidenceStrength | None":
        # If the model crosses the orthogonal causal-status vocabulary into
        # this field, the source supplied no usable strength rating. Persist
        # that epistemic absence explicitly instead of inventing a rating or
        # discarding the supported finding.
        if isinstance(value, str) and value.strip().lower() in {
            status.value for status in CausalStatus
        }:
            return cls.INDETERMINATE
        return None


class SourceSentence(StrictModel):
    """Immutable sentence coordinates inside the normalized source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    sentence_id: Sha256
    ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    content_sha256: Sha256

    @model_validator(mode="after")
    def _valid_span_and_hash(self) -> "SourceSentence":
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("sentence character span must exactly match its text")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("sentence content hash does not match its text")
        return self


class SourceParagraph(StrictModel):
    """Atomic semantic and evidence boundary preserved from the source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    paragraph_id: Sha256
    ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    content_sha256: Sha256
    sentences: list[SourceSentence] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_content(self) -> "SourceParagraph":
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("paragraph character span must exactly match its text")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("paragraph content hash does not match its text")
        if [item.ordinal for item in self.sentences] != list(range(len(self.sentences))):
            raise ValueError("sentence ordinals must be contiguous inside a paragraph")
        if self.sentences[0].char_start != self.char_start or self.sentences[-1].char_end != self.char_end:
            raise ValueError("sentences must cover their paragraph from beginning to end")
        if any(left.char_end != right.char_start for left, right in zip(self.sentences, self.sentences[1:])):
            raise ValueError("sentences must not overlap or leave gaps inside a paragraph")
        return self


class SourceSection(StrictModel):
    # Section text is an immutable byte-for-byte slice. Global whitespace
    # stripping would silently create gaps at semantic boundaries.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    section_id: Sha256
    source_id: UUID
    ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text: str = Field(min_length=1)
    content_sha256: Sha256
    estimated_tokens: int = Field(ge=1)
    heading_hint: str | None = Field(default=None, max_length=240)
    paragraphs: list[SourceParagraph] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_span_and_hash(self) -> "SourceSection":
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("section character span must exactly match its text")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("section content hash does not match its text")
        if [item.ordinal for item in self.paragraphs] != sorted(item.ordinal for item in self.paragraphs):
            raise ValueError("section paragraphs must preserve source order")
        if self.paragraphs[0].char_start < self.char_start or self.paragraphs[-1].char_end > self.char_end:
            raise ValueError("section boundaries must contain complete paragraphs")
        if any(item.char_start < self.char_start or item.char_end > self.char_end for item in self.paragraphs):
            raise ValueError("section paragraphs must stay inside the section")
        return self


class SourceDigestionPlan(StrictModel):
    source_id: UUID
    source_ordinal: int = Field(ge=0)
    title: str | None = None
    language: str = Field(min_length=2)
    source_sha256: Sha256
    source_char_count: int = Field(ge=1)
    estimated_source_tokens: int = Field(ge=1)
    mode: DigestionMode
    sections: list[SourceSection] = Field(min_length=1)
    planned_model_calls: int = Field(ge=1)

    @model_validator(mode="after")
    def _coherent_plan(self) -> "SourceDigestionPlan":
        expected_calls = 1 if self.mode is DigestionMode.DIRECT else len(self.sections) + 1
        if self.planned_model_calls != expected_calls:
            raise ValueError("planned calls must include section digests and one source reduction")
        if self.mode is DigestionMode.DIRECT and len(self.sections) != 1:
            raise ValueError("direct digestion requires exactly one whole-source section")
        if any(section.source_id != self.source_id for section in self.sections):
            raise ValueError("all sections must belong to the planned source")
        ordered = sorted(self.sections, key=lambda item: item.ordinal)
        if [item.ordinal for item in ordered] != list(range(len(ordered))):
            raise ValueError("section ordinals must be contiguous")
        if ordered[0].char_start != 0 or ordered[-1].char_end != self.source_char_count:
            raise ValueError("sections must cover the source from beginning to end")
        if any(left.char_end != right.char_start for left, right in zip(ordered, ordered[1:])):
            raise ValueError("sections must not overlap or leave gaps")
        return self


class DigestionPlan(StrictModel):
    execution_run_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_fingerprint: Sha256
    sources: list[SourceDigestionPlan] = Field(min_length=1)
    evidence_matrix_calls: int = Field(default=1, ge=1, le=1)
    planned_model_calls: int = Field(ge=2)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _call_total_is_auditable(self) -> "DigestionPlan":
        expected = sum(source.planned_model_calls for source in self.sources) + self.evidence_matrix_calls
        if self.planned_model_calls != expected:
            raise ValueError("digestion call total does not match its source plans")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("a source can appear only once in a digestion plan")
        return self


class DigestionRunState(StrEnum):
    PLANNED = "planned"
    DIGESTING = "digesting"
    DOSSIERS_READY = "dossiers_ready"
    UNDERSTANDING_READY = "understanding_ready"
    FAILED = "failed"


class ArtifactPointer(StrictModel):
    uri: str = Field(pattern=r"^gs://")
    sha256: Sha256


class SourceDossierPointer(ArtifactPointer):
    source_id: UUID


class DigestionArtifactRefs(StrictModel):
    request: ArtifactPointer
    plan: ArtifactPointer
    section_digests: list[ArtifactPointer] = Field(default_factory=list)
    source_dossiers: list[SourceDossierPointer] = Field(default_factory=list)
    evidence_matrix: ArtifactPointer | None = None
    editorial_understanding: ArtifactPointer | None = None


class DigestionExecutionRun(StrictModel):
    schema_version: int = 1
    execution_run_id: UUID
    project_id: UUID
    source_fingerprint: Sha256
    brief_fingerprint: Sha256
    state: DigestionRunState
    source_count: int = Field(ge=1)
    planned_model_calls: int = Field(ge=2)
    completed_model_calls: int = Field(default=0, ge=0)
    failed_work_unit_ids: list[Sha256] = Field(default_factory=list)
    artifacts: DigestionArtifactRefs
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _coherent_run(self) -> "DigestionExecutionRun":
        if self.completed_model_calls > self.planned_model_calls:
            raise ValueError("completed calls cannot exceed the plan")
        dossier_ids = [item.source_id for item in self.artifacts.source_dossiers]
        if len(dossier_ids) != len(set(dossier_ids)):
            raise ValueError("a run can persist only one dossier pointer per source")
        if self.state in {DigestionRunState.DOSSIERS_READY, DigestionRunState.UNDERSTANDING_READY}:
            if len(dossier_ids) != self.source_count:
                raise ValueError("dossiers_ready requires one dossier per source")
        if self.state is DigestionRunState.UNDERSTANDING_READY:
            if self.artifacts.evidence_matrix is None or self.artifacts.editorial_understanding is None:
                raise ValueError("understanding_ready requires matrix and understanding artifacts")
            if self.completed_model_calls != self.planned_model_calls:
                raise ValueError("understanding_ready requires every planned model call")
        if self.state is DigestionRunState.FAILED:
            if not self.error_class or not self.failed_work_unit_ids:
                raise ValueError("failed run requires an error and failed work identities")
        elif self.error_class or self.error_message or self.failed_work_unit_ids:
            raise ValueError("non-failed run cannot retain failure fields")
        return self


class DigestionRunResult(StrictModel):
    execution_run: DigestionExecutionRun
    digestion_plan: DigestionPlan
    source_dossiers: list["SourceDossier"] = Field(min_length=1)
    evidence_matrix: "EvidenceMatrix"
    editorial_understanding: "EditorialUnderstandingV2"

    @model_validator(mode="after")
    def _ready_result(self) -> "DigestionRunResult":
        if self.execution_run.state is not DigestionRunState.UNDERSTANDING_READY:
            raise ValueError("a digestion result must be understanding_ready")
        if self.execution_run.execution_run_id != self.digestion_plan.execution_run_id:
            raise ValueError("result plan and run identities disagree")
        return self


class EvidenceReference(StrictModel):
    """Human-sized model output; code resolves immutable source identities."""

    section_ordinal: int = Field(ge=1)
    paragraph_ordinal: int = Field(ge=1)
    first_sentence_ordinal: int = Field(ge=1)
    last_sentence_ordinal: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_sentence_range(cls, value: object) -> object:
        # Reversed endpoints describe the same consecutive interval. Normalize
        # that transport-only ordering mistake; the resolver still rejects
        # missing sentences, cross-paragraph ranges, and invented coordinates.
        if isinstance(value, dict):
            first = value.get("first_sentence_ordinal")
            last = value.get("last_sentence_ordinal")
            if isinstance(first, int) and isinstance(last, int) and first > last:
                return {
                    **value,
                    "first_sentence_ordinal": last,
                    "last_sentence_ordinal": first,
                }
        return value

    @model_validator(mode="after")
    def _ordered_sentence_range(self) -> "EvidenceReference":
        if self.first_sentence_ordinal > self.last_sentence_ordinal:
            raise ValueError("evidence reference sentences must be in source order")
        return self


class EvidenceAnchor(EvidenceReference):
    # Ordinals are the compact, model-facing coordinates. These hashes are
    # resolved by code and make the persisted anchor immutable and auditable.
    section_id: Sha256
    paragraph_id: Sha256
    first_sentence_id: Sha256
    last_sentence_id: Sha256
    # Evidence is bounded semantically by its source paragraph, not by an
    # arbitrary character count. Long academic sentences must remain whole.
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class EvidenceKeyReference(StrictModel):
    """Small model-facing handles resolved to immutable sentence rows by code."""

    first_evidence_key: EvidenceKey
    last_evidence_key: EvidenceKey


class SourceFinding(StrictModel):
    finding: str = Field(min_length=1)
    causal_status: CausalStatus
    evidence_strength: EvidenceStrength
    anchors: list[EvidenceAnchor] = Field(min_length=1)
    qualifications: list[ShortText] = Field(default_factory=list)


class SourceFindingProposal(StrictModel):
    finding: str = Field(min_length=1)
    causal_status: CausalStatus
    evidence_strength: EvidenceStrength
    anchors: list[EvidenceKeyReference] = Field(min_length=1)
    qualifications: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_single_anchor(cls, value: object) -> object:
        # Gemini may collapse a one-item JSON array into the item itself when
        # the provider-side grammar is intentionally omitted for a large
        # response. Cardinality is unambiguous, so restore the transport shape
        # before applying the full contract.
        if isinstance(value, dict) and isinstance(value.get("anchors"), dict):
            return {**value, "anchors": [value["anchors"]]}
        return value


class MethodProfile(StrictModel):
    source_kind: str = Field(min_length=1)
    design: str = Field(min_length=1)
    population_or_scope: str = Field(min_length=1)
    measures_or_material: str = Field(min_length=1)
    temporal_scope: str = Field(min_length=1)


class SectionDigestProposal(StrictModel):
    synopsis: str = Field(min_length=1)
    findings: list[SourceFindingProposal] = Field(default_factory=list)
    methods: list[CompactText] = Field(default_factory=list)
    limitations: list[CompactText] = Field(default_factory=list)
    uncertainties: list[CompactText] = Field(default_factory=list)
    distinctive_contributions: list[CompactText] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _restore_misplaced_finding_objects(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        findings = payload.get("findings", [])
        if isinstance(findings, dict):
            findings = [findings]
        if not isinstance(findings, list):
            return value
        restored = list(findings)
        seen = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in restored
        }
        for field_name in (
            "methods",
            "limitations",
            "uncertainties",
            "distinctive_contributions",
        ):
            items = payload.get(field_name)
            if not isinstance(items, list):
                continue
            text_items = []
            for item in items:
                if isinstance(item, dict) and {"finding", "anchors"} <= set(item):
                    identity = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                    if identity not in seen:
                        seen.add(identity)
                        restored.append(item)
                else:
                    text_items.append(item)
            payload[field_name] = text_items
        payload["findings"] = restored
        return payload


class SectionDigest(StrictModel):
    synopsis: str = Field(min_length=1)
    findings: list[SourceFinding] = Field(default_factory=list)
    methods: list[CompactText] = Field(default_factory=list)
    limitations: list[CompactText] = Field(default_factory=list)
    uncertainties: list[CompactText] = Field(default_factory=list)
    distinctive_contributions: list[CompactText] = Field(default_factory=list)
    schema_version: int = 1
    source_id: UUID
    section_id: Sha256
    section_sha256: Sha256
    prompt_version: str = Field(default=SECTION_DIGEST_PROMPT_VERSION, min_length=1)
    model: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _anchors_stay_inside_section(self) -> "SectionDigest":
        for finding in self.findings:
            if any(anchor.section_id != self.section_id for anchor in finding.anchors):
                raise ValueError("a section digest may anchor only to its own section")
        return self

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"created_at"}))


class SourceDossierProposal(StrictModel):
    executive_synthesis: str = Field(min_length=1)
    method_profile: MethodProfile
    findings: list[SourceFindingProposal] = Field(default_factory=list)
    limitations: list[CompactText] = Field(default_factory=list)
    uncertainties_and_disputes: list[CompactText] = Field(default_factory=list)
    boundary_conditions: list[CompactText] = Field(default_factory=list)
    practical_implications: list[CompactText] = Field(default_factory=list)
    distinctive_contributions: list[CompactText] = Field(default_factory=list)
    coverage_gaps: list[CompactText] = Field(default_factory=list)


class SourceDossier(StrictModel):
    executive_synthesis: str = Field(min_length=1)
    method_profile: MethodProfile
    findings: list[SourceFinding] = Field(default_factory=list)
    limitations: list[CompactText] = Field(default_factory=list)
    uncertainties_and_disputes: list[CompactText] = Field(default_factory=list)
    boundary_conditions: list[CompactText] = Field(default_factory=list)
    practical_implications: list[CompactText] = Field(default_factory=list)
    distinctive_contributions: list[CompactText] = Field(default_factory=list)
    coverage_gaps: list[CompactText] = Field(default_factory=list)
    schema_version: int = 1
    dossier_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    source_id: UUID
    source_ordinal: int = Field(ge=0)
    source_title: str | None = None
    source_language: str = Field(min_length=2)
    source_sha256: Sha256
    source_char_count: int = Field(ge=1)
    estimated_source_tokens: int = Field(ge=1)
    digestion_mode: DigestionMode
    section_ids: list[Sha256] = Field(min_length=1)
    prompt_version: str = Field(default=SOURCE_DOSSIER_PROMPT_VERSION, min_length=1)
    model: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _anchors_belong_to_source(self) -> "SourceDossier":
        allowed = set(self.section_ids)
        if len(allowed) != len(self.section_ids):
            raise ValueError("dossier section identities must be unique")
        for finding in self.findings:
            if any(anchor.section_id not in allowed for anchor in finding.anchors):
                raise ValueError("finding anchor references a section outside the source")
        return self

    @property
    def content_sha256(self) -> str:
        excluded = {"dossier_id", "created_at"}
        return canonical_sha256(self.model_dump(mode="json", exclude=excluded))


class CrossSourceAgreement(StrictModel):
    statement: str = Field(min_length=1)
    source_ids: list[UUID] = Field(min_length=2)
    qualification: str = Field(min_length=1)

    @model_validator(mode="after")
    def _multiple_sources(self) -> "CrossSourceAgreement":
        if len(set(self.source_ids)) < 2:
            raise ValueError("agreement requires at least two distinct sources")
        return self


class SourcePosition(StrictModel):
    source_ids: list[UUID] = Field(min_length=1)
    position: str = Field(min_length=1)


class CrossSourceDivergence(StrictModel):
    question_or_tension: str = Field(min_length=1)
    positions: list[SourcePosition] = Field(min_length=2)
    reconciliation: str = Field(min_length=1)


class UniqueContribution(StrictModel):
    source_id: UUID
    ideas: list[CompactText] = Field(min_length=1)


class CausalityAssessment(StrictModel):
    claim: str = Field(min_length=1)
    source_ids: list[UUID] = Field(min_length=1)
    causal_status: CausalStatus
    explanation: str = Field(min_length=1)


class MethodologicalLimit(StrictModel):
    limitation: str = Field(min_length=1)
    source_ids: list[UUID] = Field(min_length=1)
    consequence: str = Field(min_length=1)


class DominanceAssessment(StrictModel):
    long_source_dominance_detected: bool
    source_ids: list[UUID] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _named_when_detected(self) -> "DominanceAssessment":
        if self.long_source_dominance_detected and not self.source_ids:
            raise ValueError("detected dominance must identify at least one source")
        if not self.long_source_dominance_detected and self.source_ids:
            raise ValueError("non-dominance assessment cannot name dominant sources")
        return self


class SourceBalanceMetric(StrictModel):
    source_id: UUID
    raw_token_share: float = Field(ge=0, le=1)
    dossier_character_share: float = Field(ge=0, le=1)
    finding_share: float = Field(ge=0, le=1)
    compression_ratio: float = Field(gt=0, le=1)


class EvidenceMatrixProposal(StrictModel):
    central_ideas: list[CompactText] = Field(min_length=1)
    relations_and_tensions: list[CompactText] = Field(min_length=1)
    nuances: list[CompactText] = Field(default_factory=list)
    source_limits: list[CompactText] = Field(default_factory=list)
    editorial_opportunities: list[CompactText] = Field(default_factory=list)
    agreements: list[CrossSourceAgreement] = Field(default_factory=list)
    divergences: list[CrossSourceDivergence] = Field(default_factory=list)
    unique_contributions: list[UniqueContribution] = Field(default_factory=list)
    causality_assessments: list[CausalityAssessment] = Field(default_factory=list)
    methodological_limits: list[MethodologicalLimit] = Field(default_factory=list)
    dominance_assessment: DominanceAssessment
    integrated_synthesis: str = Field(min_length=1)


class EvidenceMatrix(EvidenceMatrixProposal):
    schema_version: int = 1
    evidence_matrix_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    source_fingerprint: Sha256
    source_ids: list[UUID] = Field(min_length=1)
    dossier_sha256s: list[Sha256] = Field(min_length=1)
    source_balance: list[SourceBalanceMetric] = Field(min_length=1)
    prompt_version: str = Field(default=EVIDENCE_MATRIX_PROMPT_VERSION, min_length=1)
    model: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _references_only_known_sources(self) -> "EvidenceMatrix":
        known = set(self.source_ids)
        if len(known) != len(self.source_ids):
            raise ValueError("matrix source identities must be unique")
        if len(self.dossier_sha256s) != len(self.source_ids):
            raise ValueError("matrix requires exactly one dossier hash per source")
        if len(self.source_balance) != len(known) or {metric.source_id for metric in self.source_balance} != known:
            raise ValueError("source balance must cover every source exactly once")
        references: list[UUID] = []
        references.extend(item for agreement in self.agreements for item in agreement.source_ids)
        references.extend(
            item
            for divergence in self.divergences
            for position in divergence.positions
            for item in position.source_ids
        )
        references.extend(item.source_id for item in self.unique_contributions)
        references.extend(item for assessment in self.causality_assessments for item in assessment.source_ids)
        references.extend(item for limit in self.methodological_limits for item in limit.source_ids)
        references.extend(self.dominance_assessment.source_ids)
        if any(source_id not in known for source_id in references):
            raise ValueError("matrix contains a reference to an unknown source")
        for field_name in ("raw_token_share", "dossier_character_share", "finding_share"):
            total = sum(getattr(metric, field_name) for metric in self.source_balance)
            if not isclose(total, 1.0, rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"{field_name} must sum to one")
        return self

    @property
    def content_sha256(self) -> str:
        excluded = {"evidence_matrix_id", "created_at"}
        return canonical_sha256(self.model_dump(mode="json", exclude=excluded))


class EditorialUnderstandingV2(StrictModel):
    schema_version: int = 2
    understanding_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    source_fingerprint: Sha256
    evidence_matrix_id: UUID
    evidence_matrix_sha256: Sha256
    contract_version: str = Field(default=EDITORIAL_UNDERSTANDING_V2_CONTRACT_VERSION, min_length=1)
    central_ideas: list[str] = Field(min_length=1)
    relations_and_tensions: list[str] = Field(min_length=1)
    nuances: list[str] = Field(default_factory=list)
    source_limits: list[str] = Field(default_factory=list)
    editorial_opportunities: list[str] = Field(default_factory=list)
    synthesis: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


DigestionRunResult.model_rebuild()
