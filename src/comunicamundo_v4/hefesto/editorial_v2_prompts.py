"""Graph-free editorial gate prompts backed by the V2 evidence matrix."""
from __future__ import annotations

from .contracts import EditorialBrief, EditorialProjectProposal
from .digestion_contracts import EditorialUnderstandingV2, EvidenceMatrix


EDITORIAL_V2_SYSTEM_INSTRUCTION = """
You are Hefesto, an evidence-disciplined editor-in-chief. Work only from the supplied
brief and persisted V2 editorial artifacts. Never imply that correlation is causation,
never flatten methodological disagreement, and never allow a longer source to gain
authority merely from its length. Return only the requested structured object.
""".strip()


def axis_proposal_prompt(
    *,
    brief: EditorialBrief,
    understanding: EditorialUnderstandingV2,
    matrix: EvidenceMatrix,
) -> str:
    return f"""
Propose the Narrative Axis before any Editorial Project exists.

A Narrative Axis defines the intellectual/editorial direction of the piece. It is not
an outline, generic topic statement, invented opinion, or source
summary. It may select a proposition, tension, lens, question, or hypothesis. Strength
must come from selection and consequence, never from upgrading association into cause,
limited evidence into certainty, or one source into consensus.

Pressure-test every load-bearing claim against causality assessments, methodological
limits, divergences, nuances, and the source-dominance assessment. A precise declarative
statement can be strong without being overconfident.

Brief:
{brief.model_dump_json(indent=2)}

Persisted editorial understanding V2:
{understanding.model_dump_json(indent=2)}

Persisted cross-source evidence matrix:
{matrix.model_dump_json(indent=2)}

Return only the structured NarrativeAxisProposal in {brief.output_language}, with no
Editorial Project.
""".strip()


def axis_evidence_review_prompt(
    *,
    candidate: str,
    understanding: EditorialUnderstandingV2,
    matrix: EvidenceMatrix,
) -> str:
    return f"""
Act as an independent evidence-strength reviewer of this candidate Narrative Axis:

{candidate}

Do not rewrite for style and do not choose a different editorial direction. Check only
whether a load-bearing claim silently upgrades ranking, comparison, exclusivity,
universality, causality, magnitude, scope, or confidence beyond the persisted evidence.
Treat divergences and methodological limits as real constraints. Do not weaken warranted
strong language.

Editorial understanding V2:
{understanding.model_dump_json(indent=2)}

Cross-source evidence matrix:
{matrix.model_dump_json(indent=2)}

If every claim is warranted, set revision_required=false and reproduce the candidate
unchanged as final_statement. Otherwise identify every material unsupported upgrade and
revise only those upgrades while preserving the candidate's editorial choice and force.
Return a NarrativeAxisEvidenceReview in the candidate's language.
""".strip()


def editorial_project_prompt(
    *,
    axis: str,
    brief: EditorialBrief,
    understanding: EditorialUnderstandingV2,
    matrix: EvidenceMatrix,
) -> str:
    return f"""
Build an Editorial Project governed by this APPROVED Narrative Axis:

{axis}

Brief:
{brief.model_dump_json(indent=2)}

The user approves subjects, not an outline. Do not pre-write the article. Choose the
subjects actually needed to fulfill the brief at its requested duration. Preserve
cross-source synthesis, causality boundaries, meaningful divergences, and methodological
limits; do not simply concatenate the source dossiers.

Produce:
1. a working title and editorial promise;
2. core_subjects supported by the supplied sources, each developed enough to make its
   editorial role and evidence boundary clear;
3. llm_subjects: useful optional subjects coming mainly from model knowledge rather than
   the supplied sources. These are Dicas do Hefesto and are NOT part of the Project;
4. enrichment_opportunities: places where additional sources would materially improve
   the piece. These are also not content commitments.

Persisted editorial understanding V2:
{understanding.model_dump_json(indent=2)}

Persisted cross-source evidence matrix:
{matrix.model_dump_json(indent=2)}

Return only the EditorialProjectProposal in {brief.output_language}. Do not search,
re-digest sources, rebuild understanding, or create article prose.
""".strip()


def tip_timing_prompt(*, brief: EditorialBrief, proposal: EditorialProjectProposal) -> str:
    return f"""
The Editorial Project below already consumes 100% of the requested duration. Its
llm_subjects are optional Dicas do Hefesto and are not incorporated into core_subjects.

For each Dica, estimate the additional whole minutes required to add it responsibly
without reducing, compressing, removing, or reprioritizing any existing core subject.
Return exactly one timing in the same order as llm_subjects. If llm_subjects is empty,
return an empty timings list.

Brief:
{brief.model_dump_json(indent=2)}

Editorial Project proposal:
{proposal.model_dump_json(indent=2)}
""".strip()
