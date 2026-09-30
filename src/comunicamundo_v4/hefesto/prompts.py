from __future__ import annotations

import json
from collections.abc import Sequence

from .contracts import (
    ContentReviewProposal,
    EditorialBrief,
    EditorialProjectProposal,
    EditorialProjectScaffold,
    EditorialSubject,
    FinalTextProposal,
    NarrativeArchitecture,
    Passage,
    ProvisionalEditorialProject,
    SourceCandidate,
)


SYSTEM = """You are Hefesto, an attentive and disciplined editor. The user's brief defines the editorial ideas, questions, intended scope and intellectual direction before any source is read. Understand every supplied source faithfully: a source may evidence, qualify, limit or contradict those briefing-led ideas, but must not silently replace them or introduce a new editorial agenda. Never turn the corpus into an additive source summary. Explicit user revisions authorize changes to the original brief; follow the current approved Editorial Project for those changes. A selected enrichment opportunity may add pertinent new perspectives without replacing that project. Return only the requested JSON object."""


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
defined by the complete brief. This is routing, not
summarization or editorial analysis: select material because it can evidence, qualify, limit or contradict
that briefing-led direction, never because it merely supplies another topic.
Before selecting, derive the extraction criteria from the brief: what questions
must this piece answer, and what evidence could support, qualify or contradict
those answers? Apply those criteria to every passage. Related vocabulary alone
is not relevance. Do not select a new agenda because a source develops it at length.
When the brief includes a selected enrichment opportunity, that choice authorizes
new pertinent perspectives and dimensions not previously named in the brief.
Judge their relevance to the purpose, not whether they were already anticipated.
Return only compact integer flags.

- relevant: passages that can materially serve the requested piece.
- central: especially important passages. Do not repeat them in relevant.
- uncertain: passages whose possible value, qualification, contradiction, or
  unexpected contribution should be preserved for deeper reading.
- include_previous/include_next: core passage numbers whose meaning requires
  the adjacent passage. Do not use these arrays as importance labels.

Omitted core passages will not receive deep reading. Preserve meaningful
contradictions, limitations, qualifications, and surprising but useful
contributions within the brief. Use only these core passage numbers: {allowed}.
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
its Axis and subjects from the briefing-led ideas. At this step no source
material is supplied: propose questions and intended development, not factual
conclusions or claims of source support. Support will be checked against
original passages after this scaffold. Sources may evidence, qualify, limit or contradict those
ideas; they must not replace them, create a source-led scope, or yield additive
summaries. Do not create the editorial_corpus units in this step; they will be
generated subject by subject.

The Narrative Axis is the one- or two-sentence intention of message that must
govern the complete future piece. The subjects must operationalize that Axis
with real depth and avoid superficial coverage of everything. Every subject must
answer a need derived from the brief. Source length and the number of passages
never authorize a new subject. Extract what serves those needs; omit unrelated
source themes even when they are well developed.

Start from the requested {brief.duration_minutes} minutes and honor the approved
provisional project's duration when present. This scaffold is not an enrichment
operation and must not add a source-led subject outside the authorized scope.

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


def referenced_subject_prompt(
    brief: EditorialBrief,
    subject: EditorialSubject,
    passages: Sequence[Passage],
    titles: dict[int, str | None],
) -> str:
    material = [
        {"passage_index": index, "source_title": titles.get(item.source_ordinal),
         "text": item.text}
        for index, item in enumerate(passages)
    ]
    return f"""Read the ORIGINAL passages for this briefing-led subject.
Produce the editorial understanding directly from them, not a source summary
or a condensation for another extraction. Preserve substantive relationships,
tensions, qualifications and contradictions pertinent to the authorized scope.
Every factual point must be grounded in evidence identifying passage_indexes
from this packet. Use these local indexes exactly; source identities are
resolved by the program. Do not invent identifiers, facts or quotations.
Quote exact original wording when necessary; otherwise use null. Preserve the
complete quotation without truncation or a character-count limit.
The originals remain the documentary authority and will be recovered through
these references. Do not impose a fixed number of ideas or evidence items.
A selected enrichment may add pertinent new perspectives without replacing
the frozen project. An explicit user revision may change its scope.

BRIEF:
{brief.model_dump_json(indent=2)}
SUBJECT:
{subject.model_dump_json(indent=2)}
ORIGINAL PASSAGES (local indexes, not source numbers):
{json.dumps(material, ensure_ascii=False)}
""".strip()


def final_narrative_prompt(text: FinalTextProposal, *, brief: EditorialBrief) -> str:
    return f"""
You are the final narrative editor. Read the entire finished piece as a reader
or listener would experience it. Its substantive content has already been
reviewed. Return the complete edited title and body, not an analysis or plan.

Choose and sustain a clear intellectual movement: each passage should deepen,
complicate, qualify or advance the reader's understanding. You have authority
to merge passages, move them, condense repeated arguments and rewrite the
opening, transitions and ending. Preserve useful returns to an idea when they
add a consequence, tension or perspective; remove restatements that add none.
Preserve all distinct substantive ideas, facts, numbers, qualifications,
uncertainties, contradictions and causal limits. Preserve meaning rather than
every sentence or occurrence of an argument. Add no unsupported claims,
invented scenes, anecdotes, evidence or quotations.

Find the movement appropriate to this particular piece and its intended
product and audience. Do not impose a universal story template, a compulsory
turning point, a topic-by-topic checklist or the wording and ordering of an
earlier outline. Weave related ideas where that makes the argument clearer.
Let the development enact its turns instead of announcing narrative machinery.
Use signposting only where it helps the audience follow the reasoning.
Keep concrete and emotionally precise passages. Avoid generic inspirational
language and forced closure: resolve only what the content supports, leaving
real tensions open. Do not manufacture hope, redemption or a moral.
Duration is guidance, not a quota: do not pad with repetition or discard
distinct ideas to meet a word count. For spoken products, make the movement
intelligible on first hearing without repeated formal recitations.

This is the final editing pass. Your edited text will be delivered directly;
no later style or completeness pass will restore its earlier organization.

PRODUCT AND READER INTENTION:
{brief.model_dump_json(indent=2)}

VALIDATED COMPLETE TEXT:
{text.model_dump_json(indent=2)}
""".strip()


def narrative_architecture_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
) -> str:
    return f"""\
Act as the narrative editor. Turn the approved Editorial Project into the
intellectual movement of one coherent piece, not a sequence of its subjects.
Decide the opening tension, the question carrying the reader, a progression in
which ideas depend on and illuminate one another, deliberate interweavings,
one turning point, and a closing that resolves or productively reframes the
opening. Preserve every approved subject, qualification, tension and forbidden
inference, but never turn them into a visible checklist.

This architecture is binding for the content writer, style writer and final
review. It is not an outline with headings and it must not contain sources,
citations or new factual claims.

BRIEF:
{brief.model_dump_json(indent=2)}

APPROVED EDITORIAL PROJECT:
{project.model_dump_json(indent=2)}
""".strip()


def content_draft_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
    architecture: NarrativeArchitecture,
    *,
    target_words: int,
    section_targets: list[dict[str, object]],
    original_material: str = "",
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

BINDING NARRATIVE ARCHITECTURE:
{architecture.model_dump_json(indent=2)}

APPROVED EDITORIAL STATE:
{project.model_dump_json(indent=2)}

REFERENCED ORIGINAL DOCUMENTARY MATERIAL:
Use these originals to verify and develop the approved ideas, qualifications
and quotations. They do not authorize changing the approved scope. Editorial
notes are interpretation; the original passages are the documentary authority.
{original_material}
""".strip()


def content_review_prompt(
    brief: EditorialBrief,
    project: EditorialProjectProposal,
    draft: FinalTextProposal,
    *,
    architecture: NarrativeArchitecture | None = None,
    original_material: str = "",
) -> str:
    return f"""
Act as the content editor. Audit the content draft against the complete approved
editorial state. Do not rewrite it.

Preserve the binding narrative progression, interweavings, turning point and
resolution when requesting corrections.

BINDING NARRATIVE ARCHITECTURE:
{architecture.model_dump_json(indent=2) if architecture else "none"}

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

REFERENCED ORIGINAL DOCUMENTARY MATERIAL:
Use these originals to verify and develop the approved ideas, qualifications
and quotations. They do not authorize changing the approved scope. Editorial
notes are interpretation; the original passages are the documentary authority.
{original_material}

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
    architecture: NarrativeArchitecture | None = None,
    original_material: str = "",
) -> str:
    return f"""
Act as the same content writer who produced the draft. Apply the editor's
review completely and return the corrected, editorially closed text. Preserve
everything that is already correct. Resolve problems coherently across the
whole piece rather than applying disconnected patches.

Preserve the binding narrative progression, interweavings, turning point and
resolution while applying the content corrections.

BINDING NARRATIVE ARCHITECTURE:
{architecture.model_dump_json(indent=2) if architecture else "none"}

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

REFERENCED ORIGINAL DOCUMENTARY MATERIAL:
Use these originals to verify and develop the approved ideas, qualifications
and quotations. They do not authorize changing the approved scope. Editorial
notes are interpretation; the original passages are the documentary authority.
{original_material}

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
    architecture: NarrativeArchitecture | None = None,
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
qualification, limitation, conclusion, and the binding narrative movement.
Do not turn its interwoven development back into consecutive topic blocks.
Add no examples, explanations, claims, or knowledge. Remove no substantive information. Use approximately
{target_words} words as guidance for the requested duration, not as a hard
acceptance boundary.

{task}
BINDING NARRATIVE ARCHITECTURE:
{architecture.model_dump_json(indent=2) if architecture else "none"}

EDITORIALLY CLOSED TEXT:
{text.model_dump_json(indent=2)}
""".strip()


def preservation_review_prompt(
    closed_text: FinalTextProposal,
    styled_text: FinalTextProposal,
    *,
    product: str,
    architecture: NarrativeArchitecture | None = None,
) -> str:
    return f"""
Read the editorially closed text and the styled {product}. Decide only whether
the styled text is substantively compatible with the closed text.

Approve when both texts carry the same facts, argument, relationships,
qualifications, limitations, conclusions and binding narrative movement.
Wording, rhythm and local transitions are normal styling decisions. Reject
a change of order or structure when it breaks the architecture's progression,
interweavings, turning point, opening or closing.

Reject only when the styled text materially contradicts the closed text, adds
an unsupported substantive claim, or omits something necessary in a way that
changes the reader's understanding. Do not enforce a word-count range and do
not perform mechanical pattern checks.

If approval is denied, every differences item must identify one material,
local difference and say exactly what must be restored, removed, or corrected.
Do not request a general rewrite.

BINDING NARRATIVE ARCHITECTURE:
{architecture.model_dump_json(indent=2) if architecture else "none"}

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


def _passage_line(passage: Passage) -> str:
    return (
        f"[passage {passage.ordinal}; paragraph {passage.paragraph_ordinal}] "
        f"{passage.text}"
    )
