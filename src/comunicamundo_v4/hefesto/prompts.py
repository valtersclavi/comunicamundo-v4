"""Editorial prompts for the clean rebuild."""
from llama_index.core.prompts import PromptTemplate

PROJECT_UNDERSTANDING_QUERY = """
Read the project sources as a whole in light of the user's brief. Build a deep editorial understanding, not a list of excerpts. Identify central ideas, mechanisms, tensions, concrete examples, implications, missing context, and relationships between ideas that matter for the requested product. Distinguish what the sources actually support from plausible external knowledge. Return a rich internal synthesis in {output_language} for an editor who will decide the intellectual direction of the piece.

Theme: {theme}
Approach: {approach}
Audience: {audience}
Product: {product}
Target duration: {duration_minutes} minutes
""".strip()

SUBJECT_CONTEXT_QUERY = """
Recover the strongest source material relevant to the following subject while respecting the approved Narrative Axis.
Narrative Axis: {narrative_axis}
Subject: {subject}
Development intent: {development}
Return concrete source-grounded context, tensions, examples and implications. Do not write the final article.
""".strip()

EDITORIAL_UNDERSTANDING_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial reader. Build a durable editorial understanding of the supplied sources for this brief. Do not propose the Narrative Axis yet. Do not write the article.

Use BOTH inputs below:
1) the persisted PropertyGraph (entities and relations already extracted from the sources);
2) the persisted source chunks/text, which preserve nuance that graph triples alone cannot.

Synthesize what is actually material for this briefing: central facts/ideas, relevant relations and tensions, nuances, limits of what the sources support, and editorial opportunities that are genuinely grounded in the sources. Distinguish source-supported claims from external knowledge. Do not invent evidence.

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Persisted PropertyGraph entities:
{graph_entities}

Persisted PropertyGraph relations:
{graph_relations}

Persisted source chunks:
{source_chunks}

Return only the structured editorial understanding in {output_language}.
""".strip()
)

NARRATIVE_AXIS_STRENGTH_CALIBRATION = """
Take a position. A Narrative Axis must be a strong editorial statement, not a generic summary and not hedge soup. Strength comes from selection, tension, precision, consequence and editorial judgment — not from silently upgrading the evidence. Choose the strongest formulation that is warranted, not the strongest wording available.

Inference and synthesis are permitted. You may synthesize, infer, choose a tension, and make an editorial proposition that is not a literal sentence from the sources. Internally distinguish:
- what the understanding directly supports;
- a reasonable synthesis or inference from supported material;
- a stronger comparative, causal, exclusive or universal claim that would require additional evidence.

Superlatives, rankings, exclusivity, universal quantifiers and strong causal certainty (examples: "the most", "the main", "the only", "always", "never", "causes", "determines") are not forbidden and must not be rejected by banned-word filtering. They remain allowed when the understanding actually warrants them. If the evidence does not establish that degree, preserve the editorial force while removing the unsupported upgrade. Do not mechanically insert hedges such as "may", "could", or "perhaps". A precise declarative statement can be both strong and evidence-faithful.

External knowledge may inform editorial judgment but must not masquerade as source evidence and must not silently increase the confidence, rank, or causality attributed to the supplied sources.
""".strip()

NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
You are Hefesto's editor-in-chief. Propose the Narrative Axis from the user's brief and the persisted editorial understanding. No Editorial Project exists yet and you must not create one.

A Narrative Axis is one or two sentences that define the intellectual/editorial direction of the piece. It is not an outline, subtitle, generic topic statement, invented opinion, or summary. It may be a proposition, question, tension, lens, or hypothesis. In essence it must say: it is through this angle that this story is worth telling.

It must arise from the user's intention, audience/product, brief, and the real understanding of the sources.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
Before finalizing the 1–2 sentence Axis, internally pressure-test every load-bearing claim against the persisted understanding, especially source_limits and nuances. Those limits and nuances constrain the Axis.

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Persisted editorial understanding:
Synthesis:
{synthesis}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

Return only the structured Narrative Axis proposal in {output_language}. It must be 1–2 sentences and must not be empty.
""".strip(),
        ]
    )
)

NARRATIVE_AXIS_EVIDENCE_STRENGTH_REVIEW_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
You are an independent evidence-strength reviewer of a candidate Narrative Axis. You are not a second writer and not a generic style critic. Do not improve style, voice, or flair. Do not propose a new editorial direction. Do not weaken warranted strong statements.

Your only task is to verify, claim by claim, whether the candidate silently upgrades the evidence in the persisted understanding via unsupported ranking/comparison, exclusivity, universality, causal certainty, scope, magnitude, or confidence.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
Candidate Narrative Axis:
{candidate}

Persisted editorial understanding:
Synthesis:
{synthesis}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

If every load-bearing claim is warranted, set revision_required to false and return the candidate unchanged as final_statement. Strong forms remain allowed.

If a claim is not warranted, set revision_required to true, name the unsupported upgrades compactly, and return a revised 1–2 sentence Axis that preserves the editorial choice, tension, and force while removing only the unsupported upgrade. Do not hedge mechanically.

Return only the structured evidence-strength review. Keep the same language as the candidate.
""".strip(),
        ]
    )
)

NARRATIVE_AXIS_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
You are Hefesto's editor-in-chief. Propose the Narrative Axis before any Editorial Project exists.

A Narrative Axis is one or two sentences that define the intellectual/editorial direction of the piece. It is not an outline, subtitle, generic topic statement, invented opinion, or summary. It may be a proposition, question, tension, lens, or hypothesis. It must arise from the user's intention, audience/product, brief, and the real understanding of the sources below.

The Axis must be specific enough to govern what the later Editorial Project prioritizes, what the writer emphasizes, and what the critic rejects as drift.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
Before finalizing the 1–2 sentence Axis, internally pressure-test every load-bearing claim against the internal source understanding, especially its limits and nuances. Those limits and nuances constrain the Axis.

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Internal source understanding:
{graph_context}

Return only the structured Narrative Axis proposal in {output_language}.
""".strip(),
        ]
    )
)

REVISE_AXIS_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
Revise the Narrative Axis using the user's natural-language feedback. Preserve the source understanding and editorial intention; do not mechanically append the feedback. Re-decide the best one- or two-sentence direction.

User feedback must not turn revision into unsupported overclaiming. If the feedback asks for a stronger claim than the understanding warrants, keep the editorial force while refusing the unsupported upgrade.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
Before finalizing the revised 1–2 sentence Axis, internally pressure-test every load-bearing claim against the internal source understanding, especially its limits and nuances. Those limits and nuances constrain the Axis.

Current Axis:
{current_axis}

User feedback:
{feedback}

Brief:
{brief}

Internal source understanding:
{graph_context}

Return only the structured revised Narrative Axis.
""".strip(),
        ]
    )
)

AXIS_FEEDBACK_DECISION_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial intelligence. A proposed Narrative Axis already exists. A human has contributed natural-language feedback. Your job is to decide what that contribution does to the existing editorial logic, whether it can be sustained, and what should happen next. Do not write the article. Do not create an Editorial Project. Do not search for sources. Do not approve the Axis.

A user contribution is not automatically another subject. Reason about meaning and consequences, not keywords, importance-sounding words, or string matching.

Editorial impact — classify from what the contribution does to load-bearing premises:
- complements: adds useful material without changing a load-bearing premise of the Axis.
- qualifies: changes how an existing idea or practice must be interpreted or applied, but does not necessarily replace the central direction.
- reformulates: changes a load-bearing premise or tension such that the Axis itself must be reconsidered.

Identify the affected premise(s) and the downstream editorial consequences. Do not classify merely by apparent importance.

Structural impact and textual space are independent. A nuance can materially change interpretation or praxis while requiring only a brief textual treatment. Do not infer section length from impact.

Support — after impact, classify how the contribution can be sustained. Judge support from the persisted SOURCE EVIDENCE below, not from the editorial understanding alone. The understanding is a synthesis: it may already mention an idea that the sources themselves only touch, or that entered during synthesis from model knowledge. Classify support as sources only when the source material itself provides enough substance for the proposed editorial role. A passing mention, echo, or synthesized restatement in the understanding is not sufficient for substantial or structural development. If the sources are thin or silent on the contribution, choose model (when safe general knowledge can sustain a bounded mention) or enrichment (when responsible development needs additional reliable sources).
- sources: the persisted source material itself can sustain the proposed editorial role.
- model: sources are insufficient, but stable internal model knowledge is sufficient for responsible editorial use. It must not invent facts, numbers, strong causalities, rankings, contested or specialized precision, or evidence claims. External or model knowledge must never masquerade as source evidence.
- enrichment: responsible development at the needed level requires additional reliable source material.

Do not treat "the model knows something" as automatically sufficient. Do not treat "the understanding already mentions it" as source evidence. If you cannot sustain the contribution responsibly from current source material or safe model knowledge, choose enrichment.

Decision:
- keep: leave the current Axis unchanged and carry the contribution forward for the later Editorial Project.
- revise: the Axis should be rewritten now because the impact warrants it and support is sources or model.
- hold_for_enrichment: required when support is enrichment. Do not pretend the new idea is already sustained. Describe what evidence is missing and why it matters.

If support is sources or model, you may revise when the impact warrants it. Complements often keep the Axis; qualifying or reformulating impact often warrants revision — but only when support is sufficient. If support is enrichment, axis_action must be hold_for_enrichment.

treatment_scale (brief, integrated, substantial) is the later textual treatment, independent of structural impact. Importance does not imply length.

If axis_action is keep, set carry_forward_intent to a durable statement of the user's intention for the later Editorial Project. If revising or holding, carry_forward_intent may be empty.

Current Narrative Axis:
{current_axis}

User feedback:
{feedback}

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Persisted editorial understanding:
Synthesis:
{synthesis}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

Persisted source evidence (normalized chunks with provenance; this is the source material, not the synthesis):
{source_chunks}

Return only the structured feedback decision in {output_language}.
""".strip()
)

AXIS_FEEDBACK_REVISION_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
You are Hefesto's editor-in-chief. Revise the proposed Narrative Axis so it absorbs the human contribution at the strength and scope the decision already justified. Preserve editorial intention; do not mechanically append the feedback. Re-decide the best one- or two-sentence direction.

No Editorial Project exists. Do not create one. Do not search. Do not approve the Axis.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
The contribution's editorial impact is {impact}. Support is {support}. Later textual treatment should be {treatment_scale}; that scale does not dictate Axis length — the Axis remains 1–2 sentences.

Affected premises:
{affected_premises}

Editorial consequences:
{editorial_consequences}

Decision rationale:
{decision_rationale}

Support rationale:
{support_rationale}

{bounded_constraint}

Current Axis:
{current_axis}

User feedback:
{feedback}

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Persisted editorial understanding:
Synthesis:
{synthesis}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

Return only the structured revised Narrative Axis in {output_language}. It must be 1–2 sentences and must not be empty.
""".strip(),
        ]
    )
)

AXIS_FEEDBACK_BOUNDED_RECALIBRATION_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial intelligence. The user refused additional sources: stay with what we already have. Recalibrate ambition. Do not erase the user's intention. Do not fabricate support. Do not search. Do not create an Editorial Project. Do not approve the Axis.

Use the persisted SOURCE EVIDENCE below plus safe model knowledge. Do not treat the editorial understanding as a substitute for source substance. sources means the source material itself can sustain the bounded role; a passing mention or synthesized restatement in the understanding is not enough. Safe model knowledge must not invent facts, numbers, strong causalities, rankings, contested or specialized precision, or evidence claims, and must never masquerade as source evidence.

support may only be sources or model. axis_action may only be keep or revise. You cannot choose enrichment or hold_for_enrichment.

Revise the Axis only if the contribution is still justified at this bounded level. Otherwise keep the current Axis and carry the contribution forward for the later Editorial Project at the treatment scale current support actually warrants.

Structural importance does not imply section length. A load-bearing nuance may become one sentence or a disclaimer if that is all current support can responsibly sustain. treatment_scale (brief, integrated, substantial) is independent of the original impact classification, which remains {impact}.

Original enrichment need that was refused:
{enrichment_need}

Original support rationale:
{original_support_rationale}

Current Narrative Axis:
{current_axis}

User feedback (preserve this intention):
{feedback}

Brief:
- theme: {theme}
- approach: {approach}
- audience: {audience}
- product: {product}
- duration: {duration_minutes} minutes
- language: {output_language}

Persisted editorial understanding:
Synthesis:
{synthesis}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

Persisted source evidence (normalized chunks with provenance; this is the source material, not the synthesis):
{source_chunks}

If keeping the Axis, set carry_forward_intent to the bounded commitment for the later Editorial Project. Return only the structured bounded recalibration in {output_language}.
""".strip()
)

EDITORIAL_PROJECT_PROMPT = PromptTemplate(
    """
Build the compact Editorial Project governed by the APPROVED Narrative Axis below.

Narrative Axis:
{narrative_axis}

Brief:
{brief}

Reuse the persisted Editorial Understanding. Do not rebuild the graph or search. Do not invent a new understanding.

Internal source understanding:
{graph_context}

Central ideas:
{central_ideas}

Relations and tensions:
{relations_and_tensions}

Nuances:
{nuances}

Source limits:
{source_limits}

Editorial opportunities:
{editorial_opportunities}

Synthesis:
{synthesis}

Durable Axis feedback history, oldest first. Keep provenance and ordering. A REVISE decision already absorbed into the active Axis remains inspectable. A KEEP/carry-forward commitment must survive at its recorded treatment_scale (brief, integrated, or substantial):
{axis_feedback_history}

Carry-forward commitments in history order. Honor each at its treatment_scale; do not inflate a brief commitment into a section or collapse a substantial one into a mention:
{carry_forwards}

The user approves subjects, not an outline. Keep the document short and editorial. Do not pre-write the article. Do not search. Do not rebuild understanding.

Treatment scale guides development, not section count. Subject selection and development must follow the brief, approved Narrative Axis, source understanding, and editorial judgment rather than a fixed content checklist.

Produce:
1) a working title and concise editorial promise;
2) core_subjects: the subjects the piece should actually develop to fulfill the brief under the approved Narrative Axis, prioritizing what is supported by the sources;
3) llm_subjects: a short list of useful subjects that come mainly from model knowledge rather than the supplied sources;
4) enrichment_opportunities: a short list of places where additional sources would materially improve the piece.

Each core subject needs only a title and a compact description of what must be developed. Do not turn subjects into sections, scene-by-scene instructions, or prose paragraphs of the final article.
Return only the structured proposal in {output_language}.
""".strip()
)

REVISE_PROJECT_PROMPT = PromptTemplate(
    """
Revise the proposed Editorial Project from the user's natural-language feedback while remaining governed by the approved Narrative Axis. Re-decide the project coherently; do not simply append a new bullet.

Approved Narrative Axis:
{narrative_axis}

Current project:
{current_project}

User feedback:
{feedback}

Internal source understanding:
{graph_context}

Return only the structured revised Editorial Project proposal.
""".strip()
)

SUBJECT_RESEARCH_PROMPT = PromptTemplate(
    """
Prepare an internal research briefing for one approved subject. Use the recovered source context first. You may add generally known context when useful, but clearly keep it subordinate to the editorial task and do not fabricate evidence.

Approved Narrative Axis: {narrative_axis}
Subject: {subject}
Development: {development}
Recovered source context:
{source_context}

Write 350-650 words in {output_language}. Synthesize; do not dump citations or answer as a checklist.
""".strip()
)

ARCHITECTURE_PROMPT = PromptTemplate(
    """
Design the internal narrative architecture for the final piece. The approved Narrative Axis is the governing principle; the approved subjects are commitments, not a mandatory section order. Build a coherent movement with transitions, an opening and closing that earn the Axis.

Axis: {narrative_axis}
Project: {project}
Research briefings:
{research}

Return only the structured narrative plan.
""".strip()
)

DRAFT_PROMPT = PromptTemplate(
    """
Write the complete final article in {output_language}. Follow the approved Narrative Axis and Editorial Project, using the internal architecture and research. Preserve nuance, specificity and usefulness for the intended audience. Do not mention the internal process, the graph, prompts, or approval workflow.

Narrative Axis: {narrative_axis}
Editorial Project: {project}
Architecture: {architecture}
Research:
{research}

Target length is appropriate to {duration_minutes} minutes of reading/listening. Produce polished Markdown only.
""".strip()
)

CRITIC_PROMPT = PromptTemplate(
    """
Critique the complete draft against the approved Narrative Axis and Editorial Project. Penalize superficial coverage, generic advice, drift from the Axis, weak practical synthesis, repetition, and claims that outrun the available support. Judge the whole piece, not sentence trivia.

Axis: {narrative_axis}
Project: {project}
Draft:
{draft}

Return only the structured editorial review.
""".strip()
)

EDIT_PROMPT = PromptTemplate(
    """
Rewrite the complete article to resolve the critic's problems while preserving its strengths. The approved Narrative Axis remains non-negotiable.

Axis: {narrative_axis}
Project: {project}
Draft:
{draft}
Review:
{review}

Return polished Markdown only in {output_language}.
""".strip()
)
