from __future__ import annotations

import json
from collections.abc import Sequence

from .contracts import (
    ContentReviewProposal,
    EditorialBrief,
    EditorialCondensation,
    EditorialProjectProposal,
    EditorialProjectScaffold,
    EditorialSubject,
    FinalTextProposal,
    Passage,
    ProvisionalEditorialProject,
    SourceCandidate,
)


SYSTEM = """You are Hefesto, an attentive and disciplined editor. The user's brief defines the editorial ideas, questions, intended scope and intellectual direction before any source is read. Understand every supplied source faithfully: a source may evidence, qualify, limit or contradict those briefing-led ideas, but must not silently replace them or introduce a new editorial agenda. Never turn the corpus into an additive source summary. Return only the requested JSON object."""


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
Queries are tools, not a public deliverable. Create exactly four distinct
search attempts for every provisional subject. Each query must carry that
subject's zero-based index and choose exactly one provider before search:
- "openalex" for research literature and scholarly publications;
- "you" for institutional guidance, reporting, public information and the web.

The provider choice concerns the kind of material being sought. It is not a
judgment about a candidate's prestige. Make later attempts for the same subject
broader or differently phrased so that a difficult subject receives four real
chances. Do not impose a fixed number of final sources.

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
or citations. Preserve everything the request does not change.

Duration rule:
- If the user adds a new subject or expands the scope, preserve the existing
  subjects and increase duration_minutes enough to develop the added subject.
  Never compress or cut existing subjects merely to keep the original duration.
- Only reduce, merge or delete subjects when the user explicitly asks for that.
- Always allocate the complete revised duration across the subjects.

The disclaimer must continue to say that the axis and project will be sustained
by the sources found.

BRIEF:
{brief.model_dump_json(indent=2)}

CURRENT PROVISIONAL EDITORIAL PROJECT:
{project.model_dump_json(indent=2)}

USER CHANGE REQUEST:
{instruction}
""".strip()


def source_candidate_judgment_prompt(
    brief: EditorialBrief,
    project: ProvisionalEditorialProject,
    candidate: SourceCandidate,
    *,
    eligible_subject_indexes: Sequence[int],
) -> str:
    subjects = [
        {
            "index": index,
            "title": subject.title,
            "editorial_role": subject.editorial_role,
            "desired_depth": subject.desired_depth,
        }
        for index, subject in enumerate(project.subjects)
        if index in set(eligible_subject_indexes)
    ]
    return f"""
Judge this single source candidate only by whether its visible content summary
appears to contribute to the Narrative Axis or provisional subjects.

Return only operational signals:
- decision: "acquire" if the candidate appears to contribute; otherwise "skip".
- covered_subject_indexes: every provisional subject index this candidate can
  materially sustain or enrich. It may cover more than one subject. Use [] when
  decision is "skip".

Do not explain the decision. Do not justify. Do not rank pedigree. Do not prefer
DOI, PDF, provider, publisher or academic status by themselves. Use those only
as metadata that may help identify what the candidate is about. If the visible
title/description/abstract does not show contribution to the subjects, skip.
If you choose "acquire", covered_subject_indexes must be non-empty and must only
contain indexes from PROVISIONAL SUBJECTS.

BRIEF:
{brief.model_dump_json(indent=2)}

NARRATIVE AXIS:
{project.narrative_axis.model_dump_json(indent=2)}

PROVISIONAL SUBJECTS:
{json.dumps(subjects, ensure_ascii=False, indent=2)}

CANDIDATE:
{candidate.model_dump_json(indent=2)}
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
Scan these passages once against the ideas, questions and intended scope already
defined by the complete brief. This is routing, not summarization or editorial
analysis: select material because it can evidence, qualify, limit or contradict
that briefing-led direction, never because it merely supplies another topic.
Return only compact integer flags.

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
Perform one bounded part of the editorial comprehension. The brief already
defines the ideas, questions and intended scope; read this material only to
determine how it bears on them. Identify whether it provides evidence,
qualification, limitation, context or contradiction, preserving only what
materially changes the treatment of those ideas. Synthesize that relationship;
do not enumerate passages, add a source-derived topic, make an outline, or
produce an additive summary or final draft.

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
Create one coherent Editorial Project guided first by the user's brief. Decide
the Narrative Axis, the editorial ideas and the necessary subjects from that
brief before weighing the selected source material. Then use sources only to
establish how each briefing-led idea is evidenced, qualified, limited, placed
in context or contradicted. The Narrative Axis and the rest of the project must
be conceived together in this same editorial act; sources must not silently
replace the user's direction or add a source-derived agenda.

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

Synthesize the relationship between the briefing-led ideas and the sources,
including evidence, convergence, complementarity, contradiction, context,
qualification and limitation when they matter. A contradiction remains a
contradiction of the relevant briefing-led idea; do not turn it into a different
idea merely because it appears in a source. Do not reproduce internal passage
coordinates in the result.

Create a compact editorial_corpus in the same act as the Narrative Axis and
Editorial Project. It is the integrated body of knowledge that the future
content writer will use instead of rereading the source material, but it must
be deliberately concise. Organize it around only the briefing-led ideas needed
for the approved subjects. For each unit, preserve the essential idea, then
the evidence, tensions, qualifications, contradictions and forbidden
inferences that govern its treatment. Do not include comprehensive notes, long
source summaries, exhaustive evidence lists or a source-by-source accumulation.
Source ordinals are internal grounding metadata for the content editor; they
must never be written into the final product.
For evidence that may be quoted or whose exact wording materially matters,
preserve one short exact source excerpt in verbatim_excerpt. Otherwise use
null; do not copy source prose merely for completeness. Keep every string short
enough to fit the schema; if a nuance needs more room, synthesize it instead of
expanding the output.

BRIEF:
{brief.model_dump_json(indent=2)}

INPUT KIND: {material_kind}

SELECTED EDITORIAL MATERIAL:
{material}
""".strip()


def project_scaffold_prompt(
    brief: EditorialBrief,
    material: str,
    *,
    material_kind: str,
    provisional_project: ProvisionalEditorialProject | None = None,
    supported_subject_indexes: Sequence[int] = (),
    existing_project: EditorialProjectProposal | None = None,
) -> str:
    if provisional_project is not None:
        anchor = f"""
APPROVED PROVISIONAL EDITORIAL PROJECT:
{provisional_project.model_dump_json(indent=2)}

SUPPORTED PROVISIONAL SUBJECT INDEXES:
{json.dumps(list(supported_subject_indexes))}

The approved provisional project is the editorial spine. Preserve its title,
promise, Narrative Axis, subject order, editorial roles and intended depth.
Include only the supported subject indexes above. Sources sustain and enrich
those subjects; they do not authorize a replacement project. Do not add a new
subject merely because it appears in a source.
"""
    elif existing_project is not None:
        anchor = f"""
EXISTING EDITORIAL PROJECT TO ENRICH:
{existing_project.model_dump_json(indent=2)}

Preserve the existing title, promise, Narrative Axis and every existing
subject. In source_supported_subjects return only genuinely new subjects added
by this enrichment. Do not restate or rename existing subjects. New material
must add time; never compress or remove existing subjects.
"""
    else:
        anchor = "There is no earlier Editorial Project anchor."
    return f"""
Create the Editorial Project scaffold guided first by the user's brief. Decide
its Axis and subjects from the briefing-led ideas before weighing the selected
source material. Sources may evidence, qualify, limit or contradict those
ideas; they must not replace them, create a source-led scope, or yield additive
summaries. Do not create the editorial_corpus units in this step; they will be
generated subject by subject.

The Narrative Axis is the one- or two-sentence intention of message that must
govern the complete future piece. The subjects must operationalize that Axis
with real depth and avoid superficial coverage of everything.

Start from the requested {brief.duration_minutes} minutes. If incorporating an
additional subject discovered through source or model enrichment, preserve every
existing subject and increase the total duration enough to develop the new
subject; never cut a subject merely to keep the original duration.

Return empty arrays for model_knowledge_suggestions and
source_enrichment_opportunities: this Editorial Project is closed for approval
and must not initiate another enrichment spiral.

Also return a compact corpus_governing_understanding and forbidden_inferences
that apply to the complete future corpus. Source names, citations and internal
passage coordinates must not appear in the user-facing project.

BRIEF:
{brief.model_dump_json(indent=2)}

EDITORIAL CONTINUITY CONTRACT:
{anchor}

INPUT KIND: {material_kind}

SELECTED EDITORIAL MATERIAL:
{material}
""".strip()


def subject_passage_routing_prompt(
    brief: EditorialBrief,
    scaffold: EditorialProjectScaffold,
    passages: Sequence[Passage],
    titles: dict[int, str | None],
    eligible_subjects_by_source: dict[int, list[int]],
) -> str:
    subjects = [
        {
            "index": index,
            "title": subject.title,
            "editorial_role": subject.editorial_role,
            "depth": subject.depth,
        }
        for index, subject in enumerate(scaffold.source_supported_subjects)
    ]
    eligibility = {
        str(source): indexes
        for source, indexes in eligible_subjects_by_source.items()
        if indexes
    }
    return f"""
Route each passage only to the already briefing-led Editorial Project subjects
it materially evidences, qualifies, limits, contradicts or enriches. This is
classification, not summarization: a passage never creates a subject or a
source-derived line of argument. Return no explanation.

A passage may serve several subjects. Omit passages that serve none. Use only
the exact source_ordinal, passage_ordinal and subject indexes shown below.
When SOURCE ELIGIBILITY supplies indexes for a source, that source may only be
routed to those subjects. Eligibility means the source was acquired for those
subjects; it does not mean every passage belongs to all of them.

BRIEF:
{brief.model_dump_json(indent=2)}

SUBJECTS:
{json.dumps(subjects, ensure_ascii=False, indent=2)}

SOURCE ELIGIBILITY:
{json.dumps(eligibility, ensure_ascii=False, indent=2)}

PASSAGES:
{render_passages(passages, titles)}
""".strip()


def subject_material_digest_prompt(
    brief: EditorialBrief,
    subject: EditorialSubject,
    material: str,
    *,
    material_kind: str,
) -> str:
    return f"""
Condense only how this bounded material bears on the named, briefing-led
subject: evidence, qualification, limitation, context or contradiction.
Preserve the resulting substantive points, relationships, tensions,
qualifications, limits, and source ordinals needed for grounding. Preserve at
most four short exact source excerpts only when their wording may be quoted or
materially matters. Do not outline the complete piece, discuss other subjects,
add model knowledge, create a new idea, or write an additive source summary.
Return one compact reading of at most 4,000 characters.

BRIEF:
{brief.model_dump_json(indent=2)}

SUBJECT:
{subject.model_dump_json(indent=2)}

INPUT KIND: {material_kind}

MATERIAL:
{material}
""".strip()


def subject_corpus_core_prompt(
    brief: EditorialBrief,
    subject: EditorialSubject,
    digest: str,
) -> str:
    return f"""
Create only the core of one editorial knowledge unit for the named,
briefing-led subject. Return a short title, a concise synthesis of that
subject's governing idea and at most six essential points. Do not turn the
source material into an additive summary.
Do not return tensions, qualifications or evidence in this call. Do not add
model knowledge or discuss other subjects.

BRIEF:
{brief.model_dump_json(indent=2)}

SUBJECT:
{subject.model_dump_json(indent=2)}

BOUNDED SUBJECT READING:
{digest}
""".strip()


def subject_corpus_nuance_prompt(
    brief: EditorialBrief,
    subject: EditorialSubject,
    digest: str,
) -> str:
    return f"""
Extract only the tensions, qualifications and contradictions that the future
writer must preserve when developing this briefing-led subject. Return at most four of each. Use empty arrays when
the bounded reading contains none. Do not return synthesis, essential points or
evidence. Do not add model knowledge.

BRIEF:
{brief.model_dump_json(indent=2)}

SUBJECT:
{subject.model_dump_json(indent=2)}

BOUNDED SUBJECT READING:
{digest}
""".strip()


def subject_corpus_evidence_prompt(
    brief: EditorialBrief,
    subject: EditorialSubject,
    digest: str,
) -> str:
    return f"""
Create only the concise source relationship for this briefing-led subject.
Return at most four evidence items. Every item must identify its source
ordinals and state whether it supplies evidence, qualification, limitation,
context or contradiction, then state its contribution briefly. Preserve a short verbatim excerpt only when the bounded
reading contains exact wording that may be quoted or materially matters;
otherwise use null. Do not invent or reconstruct quotations. Do not return
synthesis, essential points, tensions or qualifications.

BRIEF:
{brief.model_dump_json(indent=2)}

SUBJECT:
{subject.model_dump_json(indent=2)}

BOUNDED SUBJECT READING:
{digest}
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
