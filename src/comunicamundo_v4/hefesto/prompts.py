from __future__ import annotations

import json
from collections.abc import Sequence

from .contracts import EditorialBrief, EditorialCondensation, Passage


SYSTEM = """You are Hefesto, an attentive and disciplined editor. Understand the supplied material faithfully. The brief guides editorial attention but never changes what a source says. Return only the requested JSON object."""


def selection_prompt(
    brief: EditorialBrief,
    passages: Sequence[Passage],
    *,
    source_title: str | None,
    previous_context: Passage | None = None,
    next_context: Passage | None = None,
) -> str:
    context_before = _passage_line(previous_context) if previous_context else "none"
    context_after = _passage_line(next_context) if next_context else "none"
    body = "\n\n".join(_passage_line(item) for item in passages)
    allowed = [item.ordinal for item in passages]
    return f"""
Scan these passages once in light of the complete brief. This is routing, not
summarization or editorial analysis. Return only compact integer flags.

- relevant: passages that can materially serve the requested piece.
- central: especially important passages. Do not repeat them in relevant.
- uncertain: passages whose possible value, qualification, contradiction, or
  unexpected contribution should be preserved for deeper reading.
- include_previous/include_next: core passage numbers whose meaning requires
  the adjacent passage. Do not use these arrays as importance labels.

Omitted core passages will not receive deep reading. Preserve meaningful
contradictions, limitations, qualifications, and surprising but useful
directions. Use only these core passage numbers: {allowed}.
The context passages help interpret boundaries and cannot themselves be flagged.

BRIEF:
{brief.model_dump_json(indent=2)}

SOURCE {passages[0].source_ordinal}: {source_title or 'Untitled source'}

PREVIOUS CONTEXT (not selectable):
{context_before}

CORE PASSAGES:
{body}

NEXT CONTEXT (not selectable):
{context_after}
""".strip()


def condensation_prompt(
    brief: EditorialBrief,
    material: str,
    *,
    level: int,
) -> str:
    return f"""
Perform one bounded part of the editorial comprehension. Condense what this
material genuinely contributes to the user's intention. Preserve substantive
ideas, relationships, tensions, qualifications, limits, and unexpected useful
directions. Synthesize; do not enumerate every passage and do not make an
outline or draft the final product.

Keep the source labels only where they are intellectually necessary to
distinguish agreement, disagreement, evidence, context, or limitation. This
reading will be combined with other bounded readings before the Editorial
Project is created.

BRIEF:
{brief.model_dump_json(indent=2)}

COMPREHENSION LEVEL: {level}

MATERIAL:
{material}
""".strip()


def project_prompt(
    brief: EditorialBrief,
    material: str,
    *,
    material_kind: str,
) -> str:
    return f"""
Create one coherent Editorial Project directly from the selected source
material and the user's brief. The Narrative Axis and the rest of the project
must be conceived together in this same editorial act.

The Narrative Axis is the one- or two-sentence intention of message that must
govern the complete future piece. It is not merely the topic, an outline, a
generic source summary, or an invented opinion. Its rationale must explain why
this direction carries the user's intention through the material.

The source-supported subjects must operationalize that Axis, fill exactly the
requested {brief.duration_minutes} minutes with real depth, and avoid superficial
coverage of everything. Do not pre-write the final piece. Keep three classes
separate: source-supported subjects, optional suggestions coming mainly from
model knowledge, and opportunities that require additional sources. Never
present model knowledge as source-supported content.

Synthesize relationships among all sources, including convergence,
complementarity, contradiction, context, qualification, and limitation when
they matter. Do not reproduce internal passage coordinates in the result.

BRIEF:
{brief.model_dump_json(indent=2)}

INPUT KIND: {material_kind}

SELECTED EDITORIAL MATERIAL:
{material}
""".strip()


def render_passages(passages: Sequence[Passage], titles: dict[int, str | None]) -> str:
    sections: list[str] = []
    current_source: int | None = None
    previous_ordinal: int | None = None
    block_ordinal = 0
    for item in passages:
        if item.source_ordinal != current_source:
            current_source = item.source_ordinal
            previous_ordinal = None
            sections.append(
                f"\nSOURCE {current_source}: {titles.get(current_source) or 'Untitled source'}"
            )
        if previous_ordinal is None or item.ordinal != previous_ordinal + 1:
            block_ordinal += 1
            sections.append(f"BLOCK {block_ordinal}")
        sections.append(_passage_line(item))
        previous_ordinal = item.ordinal
    return "\n\n".join(sections).strip()


def render_condensations(items: Sequence[EditorialCondensation]) -> str:
    payload = [
        {"sources": item.source_ordinals, "reading": item.reading}
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False)


def _passage_line(passage: Passage) -> str:
    return (
        f"[passage {passage.ordinal}; paragraph {passage.paragraph_ordinal}] "
        f"{passage.text}"
    )
