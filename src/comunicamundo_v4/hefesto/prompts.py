from __future__ import annotations

import json
from typing import Sequence

from .contracts import (
    EditorialBrief,
    EditorialUnderstanding,
    NarrativeAxisRecord,
    Paragraph,
    ParagraphNote,
)


SYSTEM = """You are Hefesto, an attentive and disciplined editor. Understand the supplied material faithfully. The brief guides editorial attention but never changes what a source says. Return only the requested JSON object."""


def paragraph_batch_prompt(brief: EditorialBrief, paragraphs: Sequence[Paragraph]) -> str:
    payload = [
        {
            "paragraph_id": item.paragraph_id,
            "source_ordinal": item.source_ordinal,
            "paragraph_ordinal": item.ordinal,
            "sentences": [
                {"ordinal": sentence.ordinal, "text": sentence.text}
                for sentence in item.sentences
            ],
        }
        for item in paragraphs
    ]
    return f"""
Read every paragraph exactly once and return exactly one structured note for each paragraph_id, in input order.

For each paragraph, return every substantively distinct observation needed to
understand what it adds in light of the brief. Give each observation whatever
free-form tags help describe its meaning and the sentence ordinals that support
it. Explain the paragraph's possible relevance to the brief. Be concise, but do
not collapse distinct observations into one. Do not force scientific, causal,
methodological, or any other fixed taxonomy. Do not omit a paragraph because it
seems peripheral. Low relevance is information, not permission to discard it.

The requested duration ({brief.duration_minutes} minutes) guides eventual editorial selection and depth. It does not truncate reading.

BRIEF:
{brief.model_dump_json(indent=2)}

PARAGRAPHS:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def understanding_prompt(brief: EditorialBrief, notes: Sequence[ParagraphNote]) -> str:
    payload = [
        {
            "paragraph_id": item.paragraph_id,
            "source_ordinal": item.source_ordinal,
            "paragraph_ordinal": item.paragraph_ordinal,
            "observations": [
                {
                    "observation": observation.observation,
                    "tags": observation.tags,
                }
                for observation in item.observations
            ],
            "brief_relevance": item.brief_relevance,
        }
        for item in notes
    ]
    return f"""
Create the single editorial understanding for this corpus. Synthesize rather than concatenate. Decide what matters for the user's intention, audience, product, and requested duration while preserving meaningful tensions, nuances, limits, and unexpected but useful material.

Use only paragraph_ids supplied below. The understanding should be compact enough to drive the Narrative Axis and Editorial Project without reproducing all paragraph notes.

BRIEF:
{brief.model_dump_json(indent=2)}

ORDERED PARAGRAPH NOTES:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def axis_prompt(brief: EditorialBrief, understanding: EditorialUnderstanding) -> str:
    return f"""
Propose one Narrative Axis before any Editorial Project exists. It must be a one- or two-sentence intellectual/editorial direction appropriate to the user's intention, audience, product, and {brief.duration_minutes}-minute duration. It is not an outline, generic topic, source summary, or invented opinion.

BRIEF:
{brief.model_dump_json(indent=2)}

EDITORIAL UNDERSTANDING:
{understanding.model_dump_json(indent=2)}
""".strip()


def project_prompt(
    brief: EditorialBrief,
    understanding: EditorialUnderstanding,
    axis: NarrativeAxisRecord,
) -> str:
    return f"""
Build the Editorial Project governed by the APPROVED Narrative Axis. The source-supported subjects together must fill the requested {brief.duration_minutes} minutes with real depth; do not cover everything superficially and do not pre-write the article. Assign allocated_minutes to every source-supported subject, and make their sum exactly {brief.duration_minutes}.

Keep three classes separate: source-supported subjects, optional suggestions that come mainly from model knowledge, and opportunities that require additional sources. Optional suggestions and enrichment opportunities are not content commitments.

BRIEF:
{brief.model_dump_json(indent=2)}

APPROVED NARRATIVE AXIS:
{axis.proposal.statement}

EDITORIAL UNDERSTANDING:
{understanding.model_dump_json(indent=2)}
""".strip()
