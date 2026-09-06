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
    reduced_source_dossier_prompt,
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
    assert "consecutive sentences" in DIGESTION_SYSTEM_INSTRUCTION.lower()
    assert "same paragraph" in DIGESTION_SYSTEM_INSTRUCTION.lower()
    assert "outcome direction" in DIGESTION_SYSTEM_INSTRUCTION.lower()
    assert "null finding" in DIGESTION_SYSTEM_INSTRUCTION.lower()
    assert "never return excerpts" in direct.lower()
    assert "[section 1; paragraph 1]" in direct.lower()
    assert "[paragraph 1; sentence 1]" in direct.lower()
    assert "evidence_key" not in direct.lower()
    assert plan.sections[0].section_id not in direct
    assert plan.sections[0].paragraphs[0].paragraph_id not in direct
    assert "preserve every substantively distinct" in direct.lower()
    assert "never return excerpts or locators" in direct.lower()
    assert "recommendation" in direct.lower()


def test_matrix_prompt_uses_equal_ceiling_dossiers_and_never_requires_a_graph() -> None:
    source_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    finding = SourceFinding(
        finding="An association was observed.",
        causal_status=CausalStatus.CORRELATIONAL,
        evidence_strength=EvidenceStrength.MODERATE,
        anchors=[
            EvidenceAnchor(
                section_ordinal=1,
                paragraph_ordinal=1,
                first_sentence_ordinal=1,
                last_sentence_ordinal=1,
                section_id=text_sha256("section"),
                paragraph_id=text_sha256("paragraph"),
                first_sentence_id=text_sha256("sentence"),
                last_sentence_id=text_sha256("sentence"),
                locator="p. 1",
                excerpt="association",
            )
        ],
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
    assert "knowledge graph" not in prompt
    assert "an association was observed" in prompt
    assert "correlational" in prompt
    assert "moderate" in prompt
    assert "association\"" not in prompt
    assert text_sha256("sentence") not in prompt
    assert "locator" not in prompt


def test_reducer_keeps_semantics_without_retransmitting_relational_evidence() -> None:
    source_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    source = NormalizedSource(
        source_id=source_id,
        text="Observed association.",
        language="en",
        title="Study",
    )
    plan = SourceDigestionPlanner(
        analyst_load_tokens=1,
        hard_section_tokens=10,
    ).plan_source(source, source_ordinal=0)
    anchor = EvidenceAnchor(
        section_ordinal=1,
        paragraph_ordinal=1,
        first_sentence_ordinal=1,
        last_sentence_ordinal=1,
        section_id=plan.sections[0].section_id,
        paragraph_id=plan.sections[0].paragraphs[0].paragraph_id,
        first_sentence_id=plan.sections[0].paragraphs[0].sentences[0].sentence_id,
        last_sentence_id=plan.sections[0].paragraphs[0].sentences[0].sentence_id,
        locator="secret-relational-locator",
        excerpt="verbatim relational evidence",
    )
    from comunicamundo_v4.hefesto.digestion_contracts import SectionDigest

    digest = SectionDigest(
        synopsis="Complete synopsis.",
        findings=[SourceFinding(
            finding="Intellectual claim survives.",
            causal_status="correlational",
            evidence_strength="moderate",
            anchors=[anchor],
            qualifications=["Important qualification survives."],
        )],
        source_id=source_id,
        section_id=plan.sections[0].section_id,
        section_sha256=plan.sections[0].content_sha256,
        model="test",
    )

    prompt = reduced_source_dossier_prompt(
        source=source,
        section_digests=[digest],
        brief=EditorialBrief(theme="Social media", approach="Neutral", duration_minutes=15),
    )

    assert "Intellectual claim survives." in prompt
    assert "Important qualification survives." in prompt
    assert "correlational" in prompt
    assert "moderate" in prompt
    assert "verbatim relational evidence" not in prompt
    assert "secret-relational-locator" not in prompt
    assert anchor.first_sentence_id not in prompt
