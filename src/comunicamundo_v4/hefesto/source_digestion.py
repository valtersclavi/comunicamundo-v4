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
    EvidenceAnchor,
    EvidenceReference,
    EvidenceMatrix,
    SourceFinding,
    SourceFindingProposal,
    SourceParagraph,
    SourceBalanceMetric,
    SourceDigestionPlan,
    SourceDossier,
    SourceDossierProposal,
    SourceSection,
    SourceSentence,
    canonical_sha256,
)
from .planning import fingerprint_sources


ESTIMATED_CHARS_PER_TOKEN = 3
DEFAULT_ANALYST_LOAD_TOKENS = 24_000
DEFAULT_MAX_ANALYSTS_PER_SOURCE = 8
DEFAULT_HARD_SECTION_TOKENS = 200_000
_PARAGRAPH_SEPARATOR = re.compile(r"\n[ \t]*\n+")
_SENTENCE_END = re.compile(r"[.!?…。！？]+(?:[\"'”’»）)\]]+)?(?=\s+|$)")
_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+", re.IGNORECASE)
_ABBREVIATIONS = frozenset(
    {
        "dr.", "dra.", "mr.", "mrs.", "ms.", "prof.", "sr.", "sra.",
        "e.g.", "i.e.", "etc.", "fig.", "no.", "vs.",
    }
)


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


def _is_heading(paragraph: SourceParagraph) -> bool:
    candidate = " ".join(paragraph.text.split())
    words = candidate.split()
    if not candidate or len(candidate) > 180 or len(words) > 24:
        return False
    if _NUMBERED_HEADING.match(candidate):
        return True
    letters = [char for char in candidate if char.isalpha()]
    if letters and all(char.isupper() for char in letters) and len(words) <= 18:
        return True
    if candidate.endswith(":") and len(words) <= 18:
        return True
    return not re.search(r"[.!?…。！？]$", candidate) and len(words) <= 12


def _sentence_spans(text: str, *, absolute_start: int) -> list[tuple[int, int]]:
    """Return complete, gap-free sentence spans; whitespace follows the prior sentence."""

    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        token = text[start:match.end()].rstrip().rsplit(maxsplit=1)[-1].lower()
        if token in _ABBREVIATIONS or re.fullmatch(r"[A-Za-z]\.", token):
            continue
        end = match.end()
        while end < len(text) and text[end].isspace():
            end += 1
        spans.append((absolute_start + start, absolute_start + end))
        start = end
    if start < len(text):
        spans.append((absolute_start + start, absolute_start + len(text)))
    return spans or [(absolute_start, absolute_start + len(text))]


def _paragraphs(source_id: UUID, text: str) -> list[SourceParagraph]:
    spans: list[tuple[int, int]] = []
    start = 0
    for separator in _PARAGRAPH_SEPARATOR.finditer(text):
        end = separator.start()
        if text[start:end].strip():
            spans.append((start, end))
        start = separator.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    if not spans:
        raise ValueError("source must contain at least one non-empty paragraph")

    paragraphs: list[SourceParagraph] = []
    for ordinal, (start, end) in enumerate(spans):
        paragraph_text = text[start:end]
        paragraph_hash = sha256(paragraph_text.encode("utf-8")).hexdigest()
        paragraph_id = sha256(
            f"{source_id}|paragraph|{ordinal}|{start}|{end}|{paragraph_hash}".encode("utf-8")
        ).hexdigest()
        sentences: list[SourceSentence] = []
        for sentence_ordinal, (sentence_start, sentence_end) in enumerate(
            _sentence_spans(paragraph_text, absolute_start=start)
        ):
            sentence_text = text[sentence_start:sentence_end]
            sentence_hash = sha256(sentence_text.encode("utf-8")).hexdigest()
            sentence_id = sha256(
                f"{paragraph_id}|sentence|{sentence_ordinal}|{sentence_start}|{sentence_end}|{sentence_hash}".encode("utf-8")
            ).hexdigest()
            sentences.append(
                SourceSentence(
                    sentence_id=sentence_id,
                    ordinal=sentence_ordinal,
                    char_start=sentence_start,
                    char_end=sentence_end,
                    text=sentence_text,
                    content_sha256=sentence_hash,
                )
            )
        paragraphs.append(
            SourceParagraph(
                paragraph_id=paragraph_id,
                ordinal=ordinal,
                char_start=start,
                char_end=end,
                text=paragraph_text,
                content_sha256=paragraph_hash,
                sentences=sentences,
            )
        )
    return paragraphs


@dataclass(frozen=True, slots=True)
class SourceDigestionPlanner:
    analyst_load_tokens: int = DEFAULT_ANALYST_LOAD_TOKENS
    max_analysts_per_source: int = DEFAULT_MAX_ANALYSTS_PER_SOURCE
    hard_section_tokens: int = DEFAULT_HARD_SECTION_TOKENS

    def __post_init__(self) -> None:
        if min(self.analyst_load_tokens, self.max_analysts_per_source, self.hard_section_tokens) <= 0:
            raise ValueError("planner capacity settings must be positive")
        if self.analyst_load_tokens > self.hard_section_tokens:
            raise ValueError("analyst load target cannot exceed the hard model-admission ceiling")

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
        sections = self._sections(source.source_id, text)
        mode = DigestionMode.DIRECT if len(sections) == 1 else DigestionMode.HIERARCHICAL
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

    def _sections(self, source_id: UUID, text: str) -> list[SourceSection]:
        paragraphs = _paragraphs(source_id, text)
        heading_indexes = [index for index, paragraph in enumerate(paragraphs) if _is_heading(paragraph)]
        if heading_indexes:
            starts = ([0] if heading_indexes[0] else []) + heading_indexes
        else:
            # In unheaded prose, each paragraph is a source-native message
            # block. Blocks may share an analyst, but are never cut apart.
            starts = list(range(len(paragraphs)))
        starts = sorted(set(starts))

        semantic_ranges = [
            (start, starts[index + 1] if index + 1 < len(starts) else len(paragraphs))
            for index, start in enumerate(starts)
        ]
        desired_analysts = min(
            self.max_analysts_per_source,
            max(1, math.ceil(estimate_tokens(text) / self.analyst_load_tokens)),
        )
        balanced_ranges = _balance_semantic_ranges(
            semantic_ranges,
            paragraphs=paragraphs,
            source_length=len(text),
            desired_groups=desired_analysts,
        )
        admitted_ranges = _enforce_hard_admission(
            balanced_ranges,
            paragraphs=paragraphs,
            source_length=len(text),
            hard_tokens=self.hard_section_tokens,
        )

        sections: list[SourceSection] = []
        for ordinal, (paragraph_start, paragraph_end) in enumerate(admitted_ranges):
            unit_paragraphs = paragraphs[paragraph_start:paragraph_end]
            start = 0 if ordinal == 0 else unit_paragraphs[0].char_start
            end = paragraphs[admitted_ranges[ordinal + 1][0]].char_start if ordinal + 1 < len(admitted_ranges) else len(text)
            section_text = text[start:end]
            content_hash = sha256(section_text.encode("utf-8")).hexdigest()
            section_id = sha256(f"{source_id}|{ordinal}|{start}|{end}|{content_hash}".encode("utf-8")).hexdigest()
            heading = next((item for item in unit_paragraphs if _is_heading(item)), None)
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
                    heading_hint=(" ".join(heading.text.split()) if heading else None),
                    paragraphs=unit_paragraphs,
                )
            )
        return sections


def _range_char_span(
    paragraph_range: tuple[int, int],
    *,
    paragraphs: Sequence[SourceParagraph],
    source_length: int,
) -> int:
    start_index, end_index = paragraph_range
    start = 0 if start_index == 0 else paragraphs[start_index].char_start
    end = paragraphs[end_index].char_start if end_index < len(paragraphs) else source_length
    return end - start


def _balance_semantic_ranges(
    ranges: list[tuple[int, int]],
    *,
    paragraphs: Sequence[SourceParagraph],
    source_length: int,
    desired_groups: int,
) -> list[tuple[int, int]]:
    """Allocate analysts without inventing a boundary inside a paragraph."""

    target = min(desired_groups, len(paragraphs))
    expanded = list(ranges)
    while len(expanded) < target:
        candidates = [item for item in expanded if item[1] - item[0] > 1]
        if not candidates:
            break
        selected = max(
            candidates,
            key=lambda item: _range_char_span(item, paragraphs=paragraphs, source_length=source_length),
        )
        start, end = selected
        split = min(
            range(start + 1, end),
            key=lambda boundary: abs(
                _range_char_span((start, boundary), paragraphs=paragraphs, source_length=source_length)
                - _range_char_span((boundary, end), paragraphs=paragraphs, source_length=source_length)
            ),
        )
        expanded.remove(selected)
        expanded.extend(((start, split), (split, end)))
        expanded.sort()

    if len(expanded) <= target:
        return expanded

    weights = [
        _range_char_span(item, paragraphs=paragraphs, source_length=source_length)
        for item in expanded
    ]
    cumulative = [0]
    for weight in weights:
        cumulative.append(cumulative[-1] + weight)
    boundaries = [0]
    for group in range(1, target):
        previous = boundaries[-1]
        remaining_groups = target - group
        candidates = range(previous + 1, len(expanded) - remaining_groups + 1)
        ideal = cumulative[-1] * group / target
        boundaries.append(min(candidates, key=lambda index: abs(cumulative[index] - ideal)))
    boundaries.append(len(expanded))
    return [
        (expanded[left][0], expanded[right - 1][1])
        for left, right in zip(boundaries, boundaries[1:])
    ]


def _enforce_hard_admission(
    ranges: Sequence[tuple[int, int]],
    *,
    paragraphs: Sequence[SourceParagraph],
    source_length: int,
    hard_tokens: int,
) -> list[tuple[int, int]]:
    """Apply the provider ceiling only at source-native paragraph boundaries."""

    admitted: list[tuple[int, int]] = []
    for start, end in ranges:
        cursor = start
        while cursor < end:
            last_fitting = cursor
            for candidate_end in range(cursor + 1, end + 1):
                span = _range_char_span(
                    (cursor, candidate_end),
                    paragraphs=paragraphs,
                    source_length=source_length,
                )
                if math.ceil(span / ESTIMATED_CHARS_PER_TOKEN) > hard_tokens:
                    break
                last_fitting = candidate_end
            if last_fitting == cursor:
                raise ValueError(
                    "one semantic paragraph exceeds the model-admission ceiling; no arbitrary split is allowed"
                )
            admitted.append((cursor, last_fitting))
            cursor = last_fitting
    return admitted


def materialize_findings(
    findings: Sequence[SourceFindingProposal],
    sections: Sequence[SourceSection],
) -> list[SourceFinding]:
    """Reconstruct exact excerpts from consecutive sentence IDs inside one paragraph."""

    section_map = {section.section_id: section for section in sections}
    materialized: list[SourceFinding] = []
    for finding in findings:
        anchors = [_materialize_anchor(reference, section_map) for reference in finding.anchors]
        materialized.append(
            SourceFinding(
                finding=finding.finding,
                causal_status=finding.causal_status,
                evidence_strength=finding.evidence_strength,
                anchors=anchors,
                qualifications=finding.qualifications,
            )
        )
    return materialized


def _materialize_anchor(
    reference: EvidenceReference,
    section_map: dict[str, SourceSection],
) -> EvidenceAnchor:
    section = section_map.get(reference.section_id)
    if section is None:
        raise ValueError("evidence reference names a section outside the source")
    paragraph = next(
        (item for item in section.paragraphs if item.paragraph_id == reference.paragraph_id),
        None,
    )
    if paragraph is None:
        raise ValueError("evidence reference names a paragraph outside its section")
    sentence_indexes = {item.sentence_id: index for index, item in enumerate(paragraph.sentences)}
    first = sentence_indexes.get(reference.first_sentence_id)
    last = sentence_indexes.get(reference.last_sentence_id)
    if first is None or last is None or first > last:
        raise ValueError("evidence reference must select consecutive sentences in source order")
    selected = paragraph.sentences[first:last + 1]
    excerpt = paragraph.text[
        selected[0].char_start - paragraph.char_start:
        selected[-1].char_end - paragraph.char_start
    ].strip()
    if not excerpt:
        raise ValueError("evidence reference reconstructed an empty excerpt")
    sentence_label = str(first + 1) if first == last else f"{first + 1}-{last + 1}"
    locator = (
        f"section {section.ordinal + 1}; paragraph {paragraph.ordinal + 1}; "
        f"sentences {sentence_label}"
    )
    return EvidenceAnchor(**reference.model_dump(), locator=locator, excerpt=excerpt)


def validate_dossier_evidence(dossier: SourceDossier, sections: Sequence[SourceSection]) -> None:
    """Fail closed unless every persisted quote equals its sentence coordinates."""

    section_map = {section.section_id: section for section in sections}
    if set(dossier.section_ids) != set(section_map):
        raise ValueError("dossier and source plan disagree about their sections")
    for finding in dossier.findings:
        for anchor in finding.anchors:
            rebuilt = _materialize_anchor(
                EvidenceReference.model_validate(anchor.model_dump(exclude={"excerpt", "locator"})),
                section_map,
            )
            if normalize("NFKC", rebuilt.excerpt) != normalize("NFKC", anchor.excerpt):
                raise ValueError("dossier evidence does not match its immutable sentence coordinates")


def validate_reduced_dossier_evidence(
    dossier: SourceDossier,
    section_digests: Sequence,
    sections: Sequence[SourceSection],
) -> None:
    """A reducer may select section evidence, but may not manufacture new anchors."""

    allowed = {
        (
            anchor.section_id,
            anchor.paragraph_id,
            anchor.first_sentence_id,
            anchor.last_sentence_id,
        )
        for digest in section_digests
        for finding in digest.findings
        for anchor in finding.anchors
    }
    for finding in dossier.findings:
        for anchor in finding.anchors:
            identity = (
                anchor.section_id,
                anchor.paragraph_id,
                anchor.first_sentence_id,
                anchor.last_sentence_id,
            )
            if identity not in allowed:
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
        len(item.model_dump_json(include=proposal_fields))
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
