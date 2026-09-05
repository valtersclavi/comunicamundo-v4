"""Versioned prompts for evidence-preserving, graph-free source digestion."""
from __future__ import annotations

import json
from typing import Sequence

from .contracts import EditorialBrief, NormalizedSource
from .digestion_contracts import SectionDigest, SourceDossier, SourceSection
from .source_digestion import source_balance_metrics


DIGESTION_SYSTEM_INSTRUCTION = """
You are Hefesto's source analyst. Work only from the supplied source material. Do not add outside knowledge.
Keep correlation, temporal association, quasi-experimental inference, and causation distinct. Never strengthen a
source's claim. Preserve disagreements, null findings, boundary conditions, uncertainty, and methodological limits.
The causal_status field describes claim/design strength, never outcome direction. A null finding remains in the
finding text and uses no_causal_claim; never use null_finding, null_result, no_effect, or no_association as a
causal_status value.
Use longitudinal_association for claims based on temporal or longitudinal association; temporal_association is not a
transport value. The accepted causal_status values are no_causal_claim, correlational, longitudinal_association,
quasi_experimental, causal, mixed, and unclear.
Every substantive finding must cite the supplied human-sized coordinates: section_ordinal, paragraph_ordinal,
first_sentence_ordinal, and last_sentence_ordinal. Ordinals are one-based. The first and last sentence must be in the
same paragraph and identify a consecutive range in ascending source order (first_sentence_ordinal must be less than
or equal to last_sentence_ordinal). Include enough consecutive sentences to preserve
qualifications. Never return an excerpt: Hefesto resolves immutable hashes and reconstructs the exact source text
from those coordinates after validating them.
Return only the requested structured object in the requested language.
""".strip()


def _coordinate_text(section: SourceSection) -> str:
    """Render model-facing text with small, unambiguous one-based coordinates."""

    blocks: list[str] = []
    for paragraph in section.paragraphs:
        lines = [f"[SECTION {section.ordinal + 1}; PARAGRAPH {paragraph.ordinal + 1}]"]
        lines.extend(
            f"[SENTENCE {sentence.ordinal + 1}] {sentence.text.strip()}"
            for sentence in paragraph.sentences
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def section_digest_prompt(*, source: NormalizedSource, section: SourceSection, output_language: str) -> str:
    descriptor = {
        "source_id": str(source.source_id),
        "title": source.title,
        "language": source.language,
        "metadata": source.metadata,
        "section_ordinal": section.ordinal + 1,
        "heading_hint": section.heading_hint,
    }
    return f"""
Create a bounded digest of this one section. Record only findings actually supported in the section. A references or
administrative section may legitimately have no findings. For every anchor, copy section_ordinal, paragraph_ordinal,
first_sentence_ordinal, and last_sentence_ordinal from the labels below. All ordinals are one-based. A range may contain several consecutive sentences but
must stay within one paragraph. Do not return excerpts. Do not infer a whole-source method or conclusion from this
partial section. Preserve every substantively distinct finding and consolidate only genuinely redundant wording.

OUTPUT LANGUAGE: {output_language}
SOURCE AND SECTION: {json.dumps(descriptor, ensure_ascii=False, sort_keys=True)}

SECTION TEXT WITH EVIDENCE COORDINATES:
{_coordinate_text(section)}
""".strip()


def direct_source_dossier_prompt(*, source: NormalizedSource, section: SourceSection, brief: EditorialBrief) -> str:
    return f"""
Build one source dossier from the complete source below. Preserve every substantively distinct finding, qualification,
methodological fact, limitation, and uncertainty; consolidate only genuinely redundant wording. Describe the source's
actual method or institutional role. Treat recommendations as recommendations, not empirical effects. Every evidence anchor must
copy section_ordinal {section.ordinal + 1} plus a paragraph_ordinal and consecutive first/last sentence ordinals from the labels below.
Never return excerpts or locators; Hefesto reconstructs both. Use a separate anchor when another paragraph is needed.
Preserve every substantively distinct finding and consolidate only genuinely redundant wording.

EDITORIAL BRIEF:
{brief.model_dump_json(indent=2)}

SOURCE:
{json.dumps({"source_id": str(source.source_id), "title": source.title, "language": source.language, "metadata": source.metadata}, ensure_ascii=False, sort_keys=True)}

COMPLETE SOURCE TEXT WITH EVIDENCE COORDINATES:
{_coordinate_text(section)}
""".strip()


def reduced_source_dossier_prompt(
    *,
    source: NormalizedSource,
    section_digests: Sequence[SectionDigest],
    brief: EditorialBrief,
) -> str:
    serialized = [item.model_dump(mode="json", exclude={"created_at"}) for item in section_digests]
    return f"""
Reduce the section digests into one source dossier. A long source does not receive more representational weight merely
because it has more pages or sections. Synthesize rather than concatenate, but preserve every substantively distinct
finding, counterevidence item, qualification, method, limitation, and uncertainty. Reuse only
the supplied evidence coordinates. Return section_ordinal, paragraph_ordinal, first_sentence_ordinal, and
last_sentence_ordinal; never return excerpts, locators, or hashes. Do not alter a coordinate or turn an association
into causation during reduction.

EDITORIAL BRIEF:
{brief.model_dump_json(indent=2)}

SOURCE:
{json.dumps({"source_id": str(source.source_id), "title": source.title, "language": source.language, "metadata": source.metadata}, ensure_ascii=False, sort_keys=True)}

SECTION DIGESTS:
{json.dumps(serialized, ensure_ascii=False, sort_keys=True)}
""".strip()


def evidence_matrix_prompt(*, dossiers: Sequence[SourceDossier], brief: EditorialBrief) -> str:
    metrics = [item.model_dump(mode="json") for item in source_balance_metrics(dossiers)]
    proposal_fields = {
        "executive_synthesis",
        "method_profile",
        "findings",
        "limitations",
        "uncertainties_and_disputes",
        "boundary_conditions",
        "practical_implications",
        "distinctive_contributions",
        "coverage_gaps",
    }
    serialized = [
        {
            "source_id": str(item.source_id),
            "source_ordinal": item.source_ordinal,
            "source_title": item.source_title,
            "source_language": item.source_language,
            "dossier": item.model_dump(mode="json", include=proposal_fields),
        }
        for item in dossiers
    ]
    if len(dossiers) == 1:
        cardinality_rules = """
SINGLE-SOURCE RULES:
- relations_and_tensions must contain at least one substantive relation or tension within the source, such as a
  finding qualified by a limitation or a reported association constrained by the source's causal status.
- agreements and divergences must be empty because cross-source comparison is impossible.
- dominance_assessment.long_source_dominance_detected must be false and
  dominance_assessment.source_ids must be empty.
""".strip()
    else:
        cardinality_rules = """
MULTI-SOURCE RULES:
- relations_and_tensions must contain at least one substantive relation or tension across the dossiers.
- If dominance_assessment.long_source_dominance_detected is false, dominance_assessment.source_ids must be empty.
  If it is true, source_ids must identify at least one dominant source.
""".strip()
    return f"""
Build a genuinely cross-source editorial understanding from the source dossiers. Synthesize rather than append their
contents. Identify agreements, real divergences, ideas unique to one source, causal status, and methodological limits.
Weigh claims by evidence and relevance, not document length, section count, prose volume, prestige, or institutional
authority. Explicitly assess whether a long source still dominates the integrated understanding without adding useful
depth. Do not invent agreement when sources address different questions. Do not erase an idea simply because only one
source contains it. The integrated synthesis must make the sources illuminate or qualify one another.

{cardinality_rules}

EDITORIAL BRIEF:
{brief.model_dump_json(indent=2)}

DETERMINISTIC SOURCE-BALANCE METRICS:
{json.dumps(metrics, ensure_ascii=False, sort_keys=True)}

EQUAL-CEILING SOURCE DOSSIERS:
{json.dumps(serialized, ensure_ascii=False, sort_keys=True)}
""".strip()
