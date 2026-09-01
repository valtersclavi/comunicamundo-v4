from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import EditorialBrief, NormalizedSource
from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DigestionMode,
    DominanceAssessment,
    EvidenceAnchor,
    EvidenceStrength,
    MethodProfile,
    SourceDossier,
    SourceFinding,
)
from comunicamundo_v4.hefesto.digestion_prompts import (
    DIGESTION_SYSTEM_INSTRUCTION,
    direct_source_dossier_prompt,
    evidence_matrix_prompt,
)
from comunicamundo_v4.hefesto.execution_contracts import text_sha256
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner


def test_prompts_make_epistemic_and_balance_rules_explicit() -> None:
    source = NormalizedSource(
        source_id=uuid4(),
        text="Observed association, not proof of causation.",
        language="en",
        title="Study",
    )
    brief = EditorialBrief(theme="Social media", approach="Neutral", duration_minutes=15)
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)
    direct = direct_source_dossier_prompt(source=source, section=plan.sections[0], brief=brief)

    assert "correlation" in DIGESTION_SYSTEM_INSTRUCTION.lower()
    assert "causation" in direct.lower()
    assert "fixed output ceiling" in direct.lower()


def test_matrix_prompt_uses_equal_ceiling_dossiers_and_never_requires_a_graph() -> None:
    source_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    finding = SourceFinding(
        finding="An association was observed.",
        causal_status=CausalStatus.CORRELATIONAL,
        evidence_strength=EvidenceStrength.MODERATE,
        anchors=[EvidenceAnchor(section_id=text_sha256("section"), locator="p. 1", excerpt="association")],
    )
    dossier = SourceDossier(
        execution_run_id=run_id,
        project_id=project_id,
        source_id=source_id,
        source_ordinal=0,
        source_title="Study",
        source_language="en",
        source_sha256=text_sha256("source"),
        source_char_count=1000,
        estimated_source_tokens=300,
        digestion_mode=DigestionMode.DIRECT,
        section_ids=[text_sha256("section")],
        model="test",
        executive_synthesis="A bounded account.",
        method_profile=MethodProfile(
            source_kind="study",
            design="observational",
            population_or_scope="adolescents",
            measures_or_material="survey",
            temporal_scope="one wave",
        ),
        findings=[finding],
    )
    brief = EditorialBrief(theme="Social media", approach="Neutral", duration_minutes=15)

    prompt = evidence_matrix_prompt(dossiers=[dossier], brief=brief).lower()

    assert "synthesize rather than append" in prompt
    assert "document length" in prompt
    assert "institutional" in prompt
    assert "single-source rules" in prompt
    assert "relations_and_tensions must contain at least one" in prompt
    assert "dominance_assessment.source_ids must be empty" in prompt
    assert "graph" not in prompt

