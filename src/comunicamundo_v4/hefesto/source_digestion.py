"""Deterministic planning and validation for hierarchical source digestion."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import re
from typing import Sequence
from unicodedata import normalize
from uuid import UUID, uuid4

from .contracts import NormalizedSource
from .digestion_contracts import (
    DigestionMode,
    DigestionPlan,
    EditorialUnderstandingV2,
    EvidenceMatrix,
    SourceBalanceMetric,
    SourceDigestionPlan,
    SourceDossier,
    SourceDossierProposal,
    SourceSection,
    canonical_sha256,
)
from .planning import fingerprint_sources


ESTIMATED_CHARS_PER_TOKEN = 3
DEFAULT_DIRECT_SOURCE_TOKENS = 48_000
DEFAULT_TARGET_SECTION_TOKENS = 24_000
DEFAULT_MAX_SECTION_TOKENS = 32_000
_BREAK_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")
_HEADING_LINE = re.compile(r"^[^.!?]{1,240}$")


def estimate_tokens(text: str) -> int:
    """Conservative admission estimate; the gateway will count exactly near limits."""

    if not text:
        raise ValueError("cannot estimate an empty source")
    return max(1, math.ceil(len(text) / ESTIMATED_CHARS_PER_TOKEN))


def _normalized_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        raise ValueError("source text cannot be empty")
    return normalized


def _semantic_boundary(text: str, *, start: int, target: int, hard: int) -> int:
    if hard >= len(text):
        return len(text)
    floor = start + max(1, (target - start) // 2)
    for separator in _BREAK_SEPARATORS:
        forward = text.find(separator, target, hard)
        if forward >= 0:
            return forward + len(separator)
        backward = text.rfind(separator, floor, target)
        if backward >= 0:
            return backward + len(separator)
    return hard


def _heading_hint(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        candidate = " ".join(line.split())
        if candidate and _HEADING_LINE.fullmatch(candidate):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class SourceDigestionPlanner:
    direct_source_tokens: int = DEFAULT_DIRECT_SOURCE_TOKENS
    target_section_tokens: int = DEFAULT_TARGET_SECTION_TOKENS
    max_section_tokens: int = DEFAULT_MAX_SECTION_TOKENS

    def __post_init__(self) -> None:
        if self.direct_source_tokens <= 0 or self.target_section_tokens <= 0:
            raise ValueError("digestion token thresholds must be positive")
        if self.target_section_tokens >= self.max_section_tokens:
            raise ValueError("target section size must be smaller than its hard maximum")

    def plan(self, *, project_id: UUID, sources: Sequence[NormalizedSource], execution_run_id: UUID | None = None) -> DigestionPlan:
        if not sources:
            raise ValueError("at least one normalized source is required")
        source_plans = [self.plan_source(source, source_ordinal=index) for index, source in enumerate(sources)]
        return DigestionPlan(
            execution_run_id=execution_run_id or uuid4(),
            project_id=project_id,
            source_fingerprint=fingerprint_sources(sources),
            sources=source_plans,
            planned_model_calls=sum(item.planned_model_calls for item in source_plans) + 1,
        )

    def plan_source(self, source: NormalizedSource, *, source_ordinal: int) -> SourceDigestionPlan:
        text = _normalized_text(source.text)
        source_hash = sha256(text.encode("utf-8")).hexdigest()
        estimated = estimate_tokens(text)
        mode = DigestionMode.DIRECT if estimated <= self.direct_source_tokens else DigestionMode.HIERARCHICAL
        sections = self._sections(source.source_id, text, whole_source=mode is DigestionMode.DIRECT)
        return SourceDigestionPlan(
            source_id=source.source_id,
            source_ordinal=source_ordinal,
            title=source.title,
            language=source.language,
            source_sha256=source_hash,
            source_char_count=len(text),
            estimated_source_tokens=estimated,
            mode=mode,
            sections=sections,
            planned_model_calls=1 if mode is DigestionMode.DIRECT else len(sections) + 1,
        )

    def _sections(self, source_id: UUID, text: str, *, whole_source: bool) -> list[SourceSection]:
        target_chars = len(text) if whole_source else self.target_section_tokens * ESTIMATED_CHARS_PER_TOKEN
        hard_chars = len(text) if whole_source else self.max_section_tokens * ESTIMATED_CHARS_PER_TOKEN
        sections: list[SourceSection] = []
        start = 0
        while start < len(text):
            target = min(start + target_chars, len(text))
            hard = min(start + hard_chars, len(text))
            end = len(text) if target >= len(text) else _semantic_boundary(text, start=start, target=target, hard=hard)
            if end <= start:
                raise AssertionError("section planner did not advance")
            section_text = text[start:end]
            content_hash = sha256(section_text.encode("utf-8")).hexdigest()
            ordinal = len(sections)
            section_id = sha256(f"{source_id}|{ordinal}|{start}|{end}|{content_hash}".encode("utf-8")).hexdigest()
            sections.append(
                SourceSection(
                    section_id=section_id,
                    source_id=source_id,
                    ordinal=ordinal,
                    char_start=start,
                    char_end=end,
                    text=section_text,
                    content_sha256=content_hash,
                    estimated_tokens=estimate_tokens(section_text),
                    heading_hint=_heading_hint(section_text),
                )
            )
            start = end
        return sections


def validate_dossier_evidence(dossier: SourceDossier, sections: Sequence[SourceSection]) -> None:
    """Fail closed if an LLM quote is absent from the immutable source section."""

    section_map = {section.section_id: section for section in sections}
    if set(dossier.section_ids) != set(section_map):
        raise ValueError("dossier and source plan disagree about their sections")
    for finding in dossier.findings:
        for anchor in finding.anchors:
            section = section_map[anchor.section_id]
            excerpt = " ".join(normalize("NFKC", anchor.excerpt).split())
            section_text = " ".join(normalize("NFKC", section.text).split())
            if excerpt not in section_text:
                raise ValueError("dossier contains an evidence excerpt absent from its source section")


def validate_reduced_dossier_evidence(
    dossier: SourceDossier,
    section_digests: Sequence,
    sections: Sequence[SourceSection],
) -> None:
    """A reducer may select section evidence, but may not manufacture new anchors."""

    allowed = {
        (anchor.section_id, anchor.locator, anchor.excerpt)
        for digest in section_digests
        for finding in digest.findings
        for anchor in finding.anchors
    }
    for finding in dossier.findings:
        for anchor in finding.anchors:
            if (anchor.section_id, anchor.locator, anchor.excerpt) not in allowed:
                raise ValueError("reduced dossier contains an anchor absent from its section digests")
    validate_dossier_evidence(dossier, sections)


def source_balance_metrics(dossiers: Sequence[SourceDossier]) -> list[SourceBalanceMetric]:
    if not dossiers:
        raise ValueError("source balance requires at least one dossier")
    if len({item.source_id for item in dossiers}) != len(dossiers):
        raise ValueError("source balance requires one dossier per source")
    token_total = sum(item.estimated_source_tokens for item in dossiers)
    # Compare editorial representation, not lineage metadata. A long source has
    # more section IDs, but those IDs never enter the cross-source argument.
    proposal_fields = set(SourceDossierProposal.model_fields)
    dossier_lengths = [
        len(SourceDossierProposal.model_validate(item.model_dump(include=proposal_fields)).model_dump_json())
        for item in dossiers
    ]
    dossier_total = sum(dossier_lengths)
    finding_total = sum(len(item.findings) for item in dossiers)
    if token_total <= 0 or dossier_total <= 0:
        raise ValueError("source balance denominators must be positive")
    metrics = [
        SourceBalanceMetric(
            source_id=dossier.source_id,
            raw_token_share=dossier.estimated_source_tokens / token_total,
            dossier_character_share=dossier_length / dossier_total,
            finding_share=(len(dossier.findings) / finding_total) if finding_total else (1 / len(dossiers)),
            compression_ratio=min(1.0, dossier_length / dossier.source_char_count),
        )
        for dossier, dossier_length in zip(dossiers, dossier_lengths, strict=True)
    ]
    # Floating point division can leave totals a few ulps away from one. Normalize
    # the final entry so the persisted invariant is exact and independently checkable.
    for field in ("raw_token_share", "dossier_character_share", "finding_share"):
        preceding = sum(getattr(item, field) for item in metrics[:-1])
        metrics[-1] = metrics[-1].model_copy(update={field: 1.0 - preceding})
    return metrics


def editorial_understanding_from_matrix(matrix: EvidenceMatrix) -> EditorialUnderstandingV2:
    """The cross-source synthesis is the understanding; no extra paid call is needed."""

    return EditorialUnderstandingV2(
        execution_run_id=matrix.execution_run_id,
        project_id=matrix.project_id,
        source_fingerprint=matrix.source_fingerprint,
        evidence_matrix_id=matrix.evidence_matrix_id,
        evidence_matrix_sha256=matrix.content_sha256,
        central_ideas=matrix.central_ideas,
        relations_and_tensions=matrix.relations_and_tensions,
        nuances=matrix.nuances,
        source_limits=matrix.source_limits,
        editorial_opportunities=matrix.editorial_opportunities,
        synthesis=matrix.integrated_synthesis,
    )


def dossier_inputs_sha256(dossiers: Sequence[SourceDossier]) -> str:
    return canonical_sha256([item.content_sha256 for item in dossiers])
