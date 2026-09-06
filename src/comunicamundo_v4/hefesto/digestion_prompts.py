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
The evidence_strength field is a separate assessment of support. Its only values are strong, moderate, limited, and
indeterminate. Never put a causal_status value in evidence_strength; use indeterminate when the source does not support
a defensible strength assessment.
Return one flat items array. Each item has a kind and text. An evidence-bearing item selects paragraph_ordinal,
first_sentence_ordinal, and last_sentence_ordinal from the displayed local coordinates, plus causal_status and
evidence_strength when the item makes an empirical finding. For evidenced method, context, recommendation, or other
non-causal material, use empty epistemic fields; Hefesto stores their absence exactly as absence. Use the same sentence ordinal twice for one sentence. Both endpoints must remain in the same paragraph
selected by paragraph_ordinal. When an item needs evidence from different paragraphs, repeat the complete item once per paragraph. Every
item uses the same eight fields. Non-evidence items use zero for all three coordinates, empty strings for epistemic
fields, and an empty qualifications array.
Choose kind freely so it accurately names the source material; do not force an item into a predefined taxonomy.
Hefesto preserves every category and performs grouping later in code. Never
create parallel category arrays or nested anchor arrays.
Include enough consecutive sentences to preserve qualifications. Never return an ID, hash, key, locator, or excerpt:
Hefesto resolves local positions to immutable PostgreSQL sentence rows and reconstructs the exact source text.
Return only the requested structured object in the requested language.
""".strip()


def _coordinate_text(section: SourceSection) -> str:
    """Render one-based local positions; immutable identities remain code-owned."""

    blocks: list[str] = []
    for paragraph in section.paragraphs:
        lines = [f"[SECTION {section.ordinal + 1}; PARAGRAPH {paragraph.ordinal + 1}]"]
        lines.extend(
            f"[PARAGRAPH {paragraph.ordinal + 1}; "
            f"SENTENCE {sentence.ordinal + 1}] "
            f"{sentence.text.strip()}"
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
administrative section may legitimately have no findings. Put every output atom in the single items array. Use kinds
clear, precise kinds that match the material, such as method, methodological_fact, distinctive_contribution, or
recommendation. You may create another accurate kind when needed. Recommendations remain recommendations and must
never be recast as empirical findings. For each evidence-bearing item, select paragraph_ordinal,
first_sentence_ordinal, and last_sentence_ordinal from the labels below. Use the same sentence ordinal twice for one
sentence. A range may contain several consecutive sentences but must stay within one paragraph. Use zero coordinates
for non-evidence items. Do not return excerpts, IDs, hashes, keys, locators, or nested anchors. Do not infer a
whole-source method or conclusion from this
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
actual method or institutional role in the same single items array, using one item for each of source_kind, design,
population_or_scope, measures_or_material, and temporal_scope. Treat recommendations as recommendations, not empirical
effects. Use precise kinds such as source_kind, design, population_or_scope, measures_or_material, temporal_scope,
methodological_fact, recommendation, boundary_condition, practical_implication, distinctive_contribution, or
coverage_gap. You may create another accurate kind when the material requires it. Every evidence-bearing item must
select paragraph_ordinal, first_sentence_ordinal, and last_sentence_ordinal from the labels below. Use the same
sentence ordinal twice when one sentence suffices. Repeat an item when another paragraph is needed. Use zero
coordinates for non-evidence items. Never return excerpts or locators, IDs, hashes, keys, parallel category arrays, or
nested anchors; Hefesto resolves the local positions to relational evidence.
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
    serialized = []
    for item in section_digests:
        payload = {
            "synopsis": item.synopsis,
            "methods": item.methods,
            "limitations": item.limitations,
            "uncertainties": item.uncertainties,
            "distinctive_contributions": item.distinctive_contributions,
            "recommendations": item.recommendations,
            "methodological_facts": item.methodological_facts,
            "other_items": [entry.model_dump(mode="json") for entry in item.other_items],
        }
        payload["validated_findings"] = [
            {
                "finding": finding.finding,
                "causal_status": finding.causal_status,
                "evidence_strength": finding.evidence_strength,
                "qualifications": finding.qualifications,
                "evidence": [anchor.excerpt for anchor in finding.anchors],
            }
            for finding in item.findings
        ]
        serialized.append(payload)
    return f"""
Reduce the section digests into one source dossier. A long source does not receive more representational weight merely
because it has more pages or sections. Synthesize rather than concatenate, and preserve methodological facts,
methodological facts, limitations, uncertainty, recommendations, boundary conditions, implications, distinctive
contributions, coverage gaps, and legitimate other categorized material. Fill the
five method-profile items and put those other atoms in the same single items array. Do not return evidence-bearing
items: Hefesto carries every validated finding and its relational evidence into the dossier without model
transcription. Use zero for every coordinate. Never return excerpts, locators, IDs, hashes, keys, parallel category
arrays, or nested data.

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
        "recommendations",
        "methodological_facts",
        "other_items",
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
