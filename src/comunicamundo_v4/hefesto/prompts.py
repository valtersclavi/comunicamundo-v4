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
    ProvisionalEditorialProject,
    SourceCandidate,
)


SYSTEM = """You are Hefesto, an attentive and disciplined editor. Understand the supplied material faithfully. The brief guides editorial attention but never changes what a source says. Return only the requested JSON object."""


def provisional_project_prompt(brief: EditorialBrief) -> str:
    return f"""
Create the provisional Editorial Project that will guide an internal source
search. At this point there are zero sources. Decide the Narrative Axis and the
subjects from the user's editorial intention, balancing depth, generality and
available time. Do not invent facts, evidence, source names or citations.

The user-facing meaning is explicitly provisional:
- "Eixo editorial pensado: [axis], a ser sustentado pelas fontes encontradas"
- "Projeto editorial [subjects], a ser sustentado pelas fontes encontradas"

Allocate the complete requested duration across the subjects. The disclaimer
must say that both the axis and project will be sustained by the sources found.

BRIEF:
{brief.model_dump_json(indent=2)}
""".strip()


def source_search_plan_prompt(
    brief: EditorialBrief,
    project: ProvisionalEditorialProject,
) -> str:
    return f"""
Plan the internal search needed to sustain this provisional Editorial Project.
Queries are tools, not a public deliverable. Cover the Narrative Axis and the
subjects with enough depth and useful diversity for the editor to judge the
eventual source set. Do not impose a fixed number of final sources and do not
name preferred providers.

BRIEF:
{brief.model_dump_json(indent=2)}

PROVISIONAL EDITORIAL PROJECT:
{project.model_dump_json(indent=2)}
    """.strip()


def provisional_revision_prompt(
    brief: EditorialBrief,
    project: ProvisionalEditorialProject,
    instruction: str,
) -> str:
    return f"""
Revise the provisional Editorial Project according to the user's chat request.
The result still has zero sources: do not invent facts, evidence, source names
or citations. Preserve everything the request does not change. Rebalance the
subjects and duration coherently when needed, while keeping the whole duration
allocated. The disclaimer must continue to say that the axis and project will
be sustained by the sources found.

BRIEF:
{brief.model_dump_json(indent=2)}

CURRENT PROVISIONAL EDITORIAL PROJECT:
{project.model_dump_json(indent=2)}

USER CHANGE REQUEST:
{instruction}
""".strip()


def source_selection_prompt(
    brief: EditorialBrief,
    project: ProvisionalEditorialProject,
    candidates: Sequence[SourceCandidate],
    *,
    acquired_candidate_ids: Sequence[str] = (),
    failed_candidate_ids: Sequence[str] = (),
) -> str:
    rendered = json.dumps(
        [item.model_dump(mode="json") for item in candidates],
        ensure_ascii=False,
        indent=2,
    )
    return f"""
Act as the source editor. Judge candidates against the Narrative Axis and the
provisional subjects. Select the exact sources worth acquiring; provider rank
is evidence for discovery, never the editorial decision. Balance depth,
generality, authority, complementarity, contradiction and the user's available
time. There is no fixed provider quota and no target number of sources.

Return coverage_complete only when the selected and already acquired set is
editorially sufficient. If it is not sufficient, explain the gap and return
specific follow_up_queries. Never select a failed candidate again. Use only
candidate IDs present below. This judgment remains internal and source names
must not later appear in the user-facing Editorial Project.

BRIEF:
{brief.model_dump_json(indent=2)}

PROVISIONAL EDITORIAL PROJECT:
{project.model_dump_json(indent=2)}

ALREADY ACQUIRED CANDIDATE IDS:
{json.dumps(list(acquired_candidate_ids), ensure_ascii=False)}

FAILED CANDIDATE IDS:
{json.dumps(list(failed_candidate_ids), ensure_ascii=False)}

CANDIDATES:
{rendered}
""".strip()


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

The subjects must operationalize that Axis with real depth and avoid superficial
coverage of everything. Start from the requested {brief.duration_minutes}
minutes. If incorporating an additional subject discovered through source or
model enrichment, preserve every existing subject and increase the total
duration enough to develop the new subject; never cut a subject merely to keep
the original duration. Do not pre-write the final piece. Return empty arrays for
model_knowledge_suggestions and source_enrichment_opportunities: this Editorial
Project is closed for approval and must not initiate another enrichment spiral.

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
) -> str:
    return f"""
Act as the content editor. Audit the content draft against the complete approved
editorial state. Do not rewrite it.

Check whether the Narrative Axis genuinely governs the entire draft; the
editorial promise is fulfilled; every essential corpus contribution, tension,
qualification, and forbidden inference is handled correctly; the approved
subjects have the intended depth and proportion; factual statements are
supported by the corpus evidence; and no model suggestion or enrichment
opportunity became unsupported content.

Judge the content editorially. Do not enforce a word-count range or perform
mechanical pattern checks. Return an operational review. Every issue must name
its location, problem, and required correction. Approval means that no blocking
content defect remains.

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

The result must continue the approved editorial state, use approximately
{target_words} words as editorial guidance, and respect the section targets
without treating a word count as an acceptance boundary. Do not add unsupported
knowledge. This is still content writing, not product-specific styling.

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
    reference_text: FinalTextProposal | None = None,
) -> str:
    if correction_request:
        if reference_text is None:
            raise ValueError("A style correction requires the closed reference text")
        task = f"""
This is a correction pass over an already styled text. Read the editorially
closed reference and the current styled text, then make only the local
corrections explicitly requested below. Preserve all unmentioned wording,
structure, tone, rhythm, and stylistic decisions. Do not restart the styling
and do not rewrite the text as a whole.

CORRECTIONS REQUIRED:
{correction_request}

EDITORIALLY CLOSED REFERENCE:
{reference_text.model_dump_json(indent=2)}

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
claims, or knowledge. Remove no substantive information. Use approximately
{target_words} words as guidance for the requested duration, not as a hard
acceptance boundary.

{task}
{text.model_dump_json(indent=2)}
""".strip()


def preservation_review_prompt(
    closed_text: FinalTextProposal,
    styled_text: FinalTextProposal,
    *,
    product: str,
) -> str:
    return f"""
Read the editorially closed text and the styled {product}. Decide only whether
the styled text is substantively compatible with the closed text.

Approve when both texts carry the same facts, argument, relationships,
qualifications, limitations, and conclusions. Changes of wording, order,
headings, rhythm, transitions, compression, expansion, or length are normal
styling decisions and are not reasons to reject.

Reject only when the styled text materially contradicts the closed text, adds
an unsupported substantive claim, or omits something necessary in a way that
changes the reader's understanding. Do not enforce a word-count range and do
not perform mechanical pattern checks.

If approval is denied, every differences item must identify one material,
local difference and say exactly what must be restored, removed, or corrected.
Do not request a general rewrite.

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
