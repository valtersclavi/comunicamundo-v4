from __future__ import annotations

import json
from collections.abc import Sequence

from .contracts import (
    ContentReviewProposal,
    EditorialBrief,
    EditorialCondensation,
    EditorialProjectProposal,
    FinalTextProposal,
    Passage,
)


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

Create the editorial_corpus in the same act as the Narrative Axis and Editorial
Project. It is the complete, integrated body of knowledge that the future
content writer will use instead of rereading the source material. Organize it
into meaningful editorial units and preserve essential points, tensions,
qualifications, forbidden inferences, and concise source support. It must carry
the intellectual work that led to the project, not merely repeat its outline.
Source ordinals are internal grounding metadata for the content editor; they
must never be written into the final product.
For evidence that may be quoted or whose exact wording materially matters,
preserve a short exact source excerpt in verbatim_excerpt. Otherwise use null;
do not copy source prose merely for completeness.

BRIEF:
{brief.model_dump_json(indent=2)}

INPUT KIND: {material_kind}

SELECTED EDITORIAL MATERIAL:
{material}
""".strip()


def content_draft_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
    *,
    target_words: int,
    section_targets: list[dict[str, object]],
) -> str:
    return f"""
Act as the content writer. Write a complete content draft in
{brief.output_language} for {brief.audience}. This draft resolves what the piece
says; product-specific style will be applied later by another writer.

The approved editorial state is binding and complete. Continue its thinking;
do not reinterpret sources, redesign the project, or choose a new direction.
Use the editorial_corpus as the knowledge body, the Narrative Axis as the
governing intention, and the source-supported subjects as the required
development. Do not use model_knowledge_suggestions or
source_enrichment_opportunities as factual content.

Produce approximately {target_words} words and respect the section targets
below. Preserve all relevant qualifications, tensions, uncertainty, and limits.
Do not invent facts, quotations, statistics, source names, links, or citations.
Never expose source ordinals or internal processing labels.

Return a title and a plain-text body. The body must not repeat the title. Use
clear sections matching the approved content structure. Do not optimize yet
for blog, voice-over, podcast, newsletter, or another final style.

BRIEF:
{brief.model_dump_json(indent=2)}

TARGET WORDS BY SECTION:
{json.dumps(section_targets, ensure_ascii=False, indent=2)}

APPROVED EDITORIAL STATE:
{project.model_dump_json(indent=2)}
""".strip()


def content_review_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
    draft: FinalTextProposal,
    *,
    target_words: int,
    actual_words: int,
) -> str:
    return f"""
Act as the content editor. Audit the content draft against the complete approved
editorial state. Do not rewrite it.

Check whether the Narrative Axis genuinely governs the entire draft; the
editorial promise is fulfilled; every essential corpus contribution, tension,
qualification, and forbidden inference is handled correctly; the approved
subjects have the intended depth and proportion; factual statements are
supported by the corpus evidence; no model suggestion or enrichment opportunity
became unsupported content; and no internal source coordinate leaked.

The requested target is {target_words} words. The draft contains
{actual_words} words. Treat a deviation greater than 8 percent as blocking.
Return an operational review. Every issue must name its location, problem, and
required correction. Approval means that no blocking content defect remains.

BRIEF:
{brief.model_dump_json(indent=2)}

APPROVED EDITORIAL STATE:
{project.model_dump_json(indent=2)}

CONTENT DRAFT:
{draft.model_dump_json(indent=2)}
""".strip()


def content_revision_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
    draft: FinalTextProposal,
    review: ContentReviewProposal,
    *,
    target_words: int,
    section_targets: list[dict[str, object]],
) -> str:
    return f"""
Act as the same content writer who produced the draft. Apply the editor's
review completely and return the corrected, editorially closed text. Preserve
everything that is already correct. Resolve problems coherently across the
whole piece rather than applying disconnected patches.

The result must continue the approved editorial state, contain approximately
{target_words} words within an 8 percent tolerance, and respect the section
targets. Do not add unsupported knowledge or expose internal source coordinates.
This is still content writing, not product-specific styling.

BRIEF:
{brief.model_dump_json(indent=2)}

TARGET WORDS BY SECTION:
{json.dumps(section_targets, ensure_ascii=False, indent=2)}

APPROVED EDITORIAL STATE:
{project.model_dump_json(indent=2)}

ORIGINAL CONTENT DRAFT:
{draft.model_dump_json(indent=2)}

EDITOR REVIEW:
{review.model_dump_json(indent=2)}
""".strip()


def product_style_prompt(
    text: FinalTextProposal,
    *,
    product: str,
    language: str,
    audience: str,
    target_words: int,
    correction_request: str | None = None,
) -> str:
    if correction_request:
        task = f"""
This is a correction pass over an already styled text. Make only the local
corrections explicitly requested below. Preserve all unmentioned wording,
structure, tone, rhythm, and stylistic decisions. Do not restart the styling
and do not rewrite the text as a whole.

CORRECTIONS REQUIRED:
{correction_request}

CURRENT STYLED TEXT:
"""
    else:
        task = """
This is the first styling pass.

EDITORIALLY CLOSED TEXT:
"""
    return f"""
Act only as the style writer for a {product}. You receive an editorially closed
text and no sources, Editorial Project, Narrative Axis, or research context.

Give the text the strongest natural form, tone, rhythm, structure, transitions,
opening, ending, and degree of verbosity for a {product} in {language} for
{audience}. You have complete freedom of form.

You have no freedom of content. Preserve every fact, number, relationship,
qualification, limitation, and conclusion. Add no examples, explanations,
claims, or knowledge. Remove no substantive information. Target approximately
{target_words} words within an 8 percent tolerance.

{task}
{text.model_dump_json(indent=2)}
""".strip()


def preservation_review_prompt(
    closed_text: FinalTextProposal,
    styled_text: FinalTextProposal,
    *,
    product: str,
    target_words: int,
    mechanical_issues: list[str],
) -> str:
    return f"""
Act as the final preservation verifier. Compare the editorially closed text
with the styled {product}. Do not rewrite either text.

Approve only if the styled version preserves every substantive fact, number,
relationship, qualification, limitation, and conclusion without adding new
content or losing necessary content. Formal changes are allowed without limit.
The target is {target_words} words within an 8 percent tolerance.

Mechanically detected issues are binding and make approval impossible until
corrected:
{json.dumps(mechanical_issues, ensure_ascii=False, indent=2)}

If approval is denied, every differences item must be a precise, local,
actionable correction instruction. Identify the affected wording or location
and state exactly what must be restored, removed, or changed. Do not ask for a
general rewrite.

EDITORIALLY CLOSED TEXT:
{closed_text.model_dump_json(indent=2)}

STYLED TEXT:
{styled_text.model_dump_json(indent=2)}
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
