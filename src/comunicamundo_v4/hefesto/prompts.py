"""Editorial prompts for the clean rebuild."""
from llama_index.core.prompts import PromptTemplate

PROJECT_UNDERSTANDING_QUERY = """
Read the project sources as a whole in light of the user's brief. Build a deep editorial understanding, not a list of excerpts. Identify central ideas, mechanisms, tensions, concrete examples, practical implications, missing context, and relationships between ideas that matter for the requested product. Distinguish what the sources actually support from plausible external knowledge. Return a rich internal synthesis in {output_language} for an editor who will decide the intellectual direction of the piece.

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
Return concrete source-grounded context, tensions, examples and practical implications. Do not write the final article.
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

AXIS_FEEDBACK_IMPACT_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial decision-maker. Before any rewrite of the Narrative Axis, assess the editorial impact of the user's natural-language feedback against the current Axis and the persisted understanding.

Do not rewrite the Axis. Do not search for sources. Do not invent evidence. Do not use keyword matching or case-specific rules. Judge semantically.

Classify exactly one impact:
- complements: the contribution adds something without changing the Axis's premises;
- qualifies: the contribution changes how relevant parts must be understood or treated, without necessarily replacing the central direction;
- reframes: the contribution alters an important premise and requires a coherent reconsideration of the Axis.

Impact structure and textual space are different dimensions. A nuance may change how the whole praxis is read and still deserve only a brief treatment later if support is limited. Decide whether, IF support later proves sufficient, the Axis statement itself should be preserved or revised. That is axis_if_supported. Do not confuse it with how many words the later product should spend.

Record compactly:
- which premise or line of reasoning is affected;
- which editorial consequences follow;
- whether the Axis should be preserved or revised if support is sufficient.

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

Return only the structured impact assessment. Keep the same language as the feedback and Axis.
""".strip()
)

AXIS_FEEDBACK_SUPPORT_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial support reviewer. The impact of the user's feedback has already been assessed. Now classify the support basis for that contribution. Do not rewrite the Axis. Do not search for sources. Do not ingest material.

Choose exactly one support_basis:
- sources: the current sources and persisted understanding already support enough development of this contribution;
- model: the contribution is not sufficiently developed in the sources, but stable internal model knowledge can treat it with editorial safety without inventing facts, numbers, strong causalities, hierarchies, controversies, or specialized precision that would require verification;
- enrichment_required: giving the contribution the weight, depth, or centrality it would have in the reasoning requires additional evidence.

`model` is not a license to hallucinate. External knowledge must not pretend it came from the sources. If treating the contribution at the impact's implied weight would require unverified specifics, choose enrichment_required rather than stretching model knowledge.

Choose a treatment_scale coherent with the support actually available, not with a word count: structural, developed, brief, or mention_only. A structurally important nuance may still be mention_only if residual support is thin.

If support_basis is enrichment_required, return a compact enrichment_need: what must be clarified or sustained, and why. Do not rewrite the Axis as if the idea were already supported.

If the Axis would be preserved, return a carried_directive that carries the contribution forward for a later Editorial Project without appending it to the Axis.

Impact already assessed:
- impact: {impact}
- affected premise: {affected_premise}
- editorial consequences: {editorial_consequences}
- axis if supported: {axis_if_supported}

Current Axis:
{current_axis}

User feedback:
{feedback}

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

Return only the structured support assessment. Keep the same language as the feedback and Axis.
""".strip()
)

AXIS_FEEDBACK_RECALIBRATION_PROMPT = PromptTemplate(
    """
You are Hefesto's editorial decision-maker. The user refused additional sources. Recalibrate the pending contribution to the maximum that current sources plus safe model knowledge can sustain. Do not invent support. Do not erase the user's intention. Do not search. Do not rewrite until the orchestrator asks.

Refusing enrichment reduces possible depth and weight. It does not fabricate evidence and does not automatically drop the intention.

Reassess impact semantically (complements / qualifies / reframes) at this reduced ambition. Decide whether, with only residual support, the Axis statement itself still must be revised or should be preserved while the contribution is carried as a limited editorial directive.

support_basis may only be:
- sources: residual source/understanding support is enough at the reduced scale;
- model: residual treatment is safe from stable model knowledge without inventing facts, numbers, strong causalities, hierarchies, controversies, or specialized precision.

Choose a reduced treatment_scale coherent with residual support: structural, developed, brief, or mention_only.

If the Axis should be preserved at this reduced scale, return a carried_directive that preserves the intention at the sustainable scale.

Pending enrichment need:
{enrichment_need}

Previous impact:
- impact: {previous_impact}
- affected premise: {affected_premise}
- editorial consequences: {editorial_consequences}

Current Axis:
{current_axis}

Original user feedback:
{feedback}

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

Return only the structured recalibration. Keep the same language as the feedback and Axis.
""".strip()
)

REVISE_AXIS_FROM_DECISION_PROMPT = PromptTemplate(
    "\n\n".join(
        [
            """
Revise the Narrative Axis coherently from an inspectable editorial decision. Do not mechanically append the feedback. Re-decide the best one- or two-sentence direction so the Axis absorbs the contribution as a governing premise, qualification, or reframing.

The decision already established impact, support basis, and treatment scale. Honor that scale: do not write as if unsupported depth were already in hand. User feedback must not turn revision into unsupported overclaiming. External knowledge may inform judgment but must not masquerade as source evidence.
""".strip(),
            NARRATIVE_AXIS_STRENGTH_CALIBRATION,
            """
Before finalizing the revised 1–2 sentence Axis, internally pressure-test every load-bearing claim against the persisted understanding, especially source_limits and nuances. Those limits and nuances constrain the Axis.

Current Axis:
{current_axis}

User feedback:
{feedback}

Inspectable decision:
- impact: {impact}
- affected premise: {affected_premise}
- editorial consequences: {editorial_consequences}
- support basis: {support_basis}
- support rationale: {support_rationale}
- treatment scale: {treatment_scale}

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

Return only the structured revised Narrative Axis. It must be 1–2 sentences and must not be empty.
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

EDITORIAL_PROJECT_PROMPT = PromptTemplate(
    """
Build the compact Editorial Project governed by the APPROVED Narrative Axis below.

Narrative Axis:
{narrative_axis}

Brief:
{brief}

Internal source understanding:
{graph_context}

Carried editorial directives from prior Axis feedback (honor these in later subject development; they are not a license to invent evidence, and model-supported items must not be presented as if they came from the sources):
{carried_editorial_directives}

The user approves subjects, not an outline. Keep the document short and editorial. Do not pre-write the article.

Produce:
1) a working title and concise editorial promise;
2) core_subjects: the subjects the piece should actually develop, prioritizing what is supported by the sources and including operational/practical development where the topic calls for "how";
3) llm_subjects: a short list of useful subjects that come mainly from model knowledge rather than the supplied sources;
4) enrichment_opportunities: a short list of places where additional sources would materially improve the piece.

Each core subject needs only a title, a compact description of what must be developed, and an optional practical_dimension. Do not turn subjects into sections, scene-by-scene instructions, or prose paragraphs of the final article.
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
Practical dimension: {practical_dimension}
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
Write the complete final article in {output_language}. Follow the approved Narrative Axis and Editorial Project, using the internal architecture and research. Preserve nuance, specificity and practical usefulness. Do not mention the internal process, the graph, prompts, or approval workflow.

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
