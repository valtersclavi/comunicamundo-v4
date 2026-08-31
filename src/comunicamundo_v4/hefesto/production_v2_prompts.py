"""Prompts for bounded, source-grounded V2 article production."""
from __future__ import annotations

from .contracts import EditorialProject, EditorialReview, NarrativePlan
from .production_v2_contracts import (
    ArticleDraftV2,
    ProductionEvidenceBundle,
    SubjectResearchBundleProposal,
)


PRODUCTION_V2_SYSTEM_INSTRUCTION = """
You are Hefesto's production desk. The approved Narrative Axis and Editorial Project are
binding. Use the persisted evidence bundle as the factual boundary. Do not turn
correlation into causation, erase methodological disagreement, fabricate evidence, or
mention internal prompts and workflow. Return only the requested structured object.
""".strip()


def production_project_json(project: EditorialProject) -> str:
    return project.model_dump_json(
        indent=2,
        exclude={"llm_subjects", "enrichment_opportunities"},
    )


def research_prompt(*, project: EditorialProject, evidence: ProductionEvidenceBundle) -> str:
    return f"""
Prepare one internal research synthesis for every approved core subject in a single
response. Return exactly one SubjectResearch for each subject_id, in Project order.
Synthesize the packet; do not dump excerpts or write the final article. General editorial
reasoning may connect supported facts, but it must not introduce new factual evidence.

Approved Narrative Axis:
{project.narrative_axis}

Approved Editorial Project:
{production_project_json(project)}

Shared cross-source context:
{evidence.shared_context}

Bounded subject evidence packets:
{_packets(evidence)}

Return only SubjectResearchBundleProposal in {project.brief.output_language}.
""".strip()


def architecture_prompt(
    *,
    project: EditorialProject,
    research: SubjectResearchBundleProposal,
    evidence: ProductionEvidenceBundle,
) -> str:
    return f"""
Design the internal narrative architecture for the complete final piece. The approved
Narrative Axis governs the movement; core subjects are commitments, not a mandatory
section order. Build transitions, an opening, and a closing that earn the Axis. Keep
causality boundaries and meaningful source tensions load-bearing where relevant.

Approved Narrative Axis:
{project.narrative_axis}

Approved Editorial Project:
{production_project_json(project)}

Research syntheses:
{research.model_dump_json(indent=2)}

Shared evidence constraints:
{evidence.shared_context}

Return only NarrativePlan in {project.brief.output_language}.
""".strip()


def draft_prompt(
    *,
    project: EditorialProject,
    architecture: NarrativePlan,
    research: SubjectResearchBundleProposal,
    evidence: ProductionEvidenceBundle,
) -> str:
    return f"""
Write the complete final article in polished Markdown. Follow the approved Axis and
Project, using the narrative architecture and research syntheses. Preserve nuance,
specificity, usefulness, divergences, and methodological limits. Do not mention the
internal process. Do not add Dicas do Hefesto or enrichment opportunities: neither was
accepted as content.

Approved Narrative Axis:
{project.narrative_axis}

Approved Editorial Project:
{production_project_json(project)}

Narrative architecture:
{architecture.model_dump_json(indent=2)}

Research syntheses:
{research.model_dump_json(indent=2)}

Shared evidence constraints:
{evidence.shared_context}

Target duration: {project.brief.duration_minutes} minutes.
Language: {project.brief.output_language}.

Return only ArticleDraftV2 with the complete Markdown in body_markdown.
""".strip()


def critic_prompt(
    *,
    project: EditorialProject,
    draft: ArticleDraftV2,
    research: SubjectResearchBundleProposal,
    evidence: ProductionEvidenceBundle,
) -> str:
    return f"""
Critique the complete draft against the approved Axis, Project, research, and evidence
constraints. Penalize superficial coverage, generic advice, repetition, drift, source
concatenation disguised as synthesis, causal overclaiming, erased divergence, and claims
that outrun support. Judge the whole piece rather than sentence trivia.

Approved Narrative Axis:
{project.narrative_axis}

Approved Editorial Project:
{production_project_json(project)}

Research syntheses:
{research.model_dump_json(indent=2)}

Evidence constraints:
{evidence.shared_context}

Draft:
{draft.body_markdown}

Return only EditorialReview.
""".strip()


def edit_prompt(
    *,
    project: EditorialProject,
    draft: ArticleDraftV2,
    review: EditorialReview,
    research: SubjectResearchBundleProposal,
    evidence: ProductionEvidenceBundle,
) -> str:
    return f"""
Rewrite the complete article to resolve the critic's problems while preserving its
strengths. The approved Axis and source-evidence boundary remain non-negotiable. Return
the complete article, not a patch or commentary.

Approved Narrative Axis:
{project.narrative_axis}

Approved Editorial Project:
{production_project_json(project)}

Research syntheses:
{research.model_dump_json(indent=2)}

Evidence constraints:
{evidence.shared_context}

Current draft:
{draft.body_markdown}

Editorial review:
{review.model_dump_json(indent=2)}

Return only ArticleDraftV2 in {project.brief.output_language}.
""".strip()


def _packets(evidence: ProductionEvidenceBundle) -> str:
    return "\n\n".join(packet.rendered_context for packet in evidence.subject_packets)
