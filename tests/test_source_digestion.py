from __future__ import annotations

from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import NormalizedSource
from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DigestionMode,
    EvidenceAnchor,
    EvidenceStrength,
    MethodProfile,
    SourceDossier,
    SourceFinding,
)
from comunicamundo_v4.hefesto.source_digestion import (
    SourceDigestionPlanner,
    estimate_tokens,
    source_balance_metrics,
    validate_dossier_evidence,
)


def _source(size: int, *, title: str = "Source") -> NormalizedSource:
    paragraph = "A substantive sentence about adolescent wellbeing and digital life.\n\n"
    repetitions = max(1, size // len(paragraph))
    text = (paragraph * repetitions)[:size]
    return NormalizedSource(source_id=uuid4(), text=text, language="en", title=title)


def _dossier(plan, *, project_id, run_id, findings: int = 4) -> SourceDossier:
    paragraph = plan.sections[0].paragraphs[0]
    sentence = paragraph.sentences[0]
    anchor = EvidenceAnchor(
        section_ordinal=plan.sections[0].ordinal + 1,
        paragraph_ordinal=paragraph.ordinal + 1,
        first_sentence_ordinal=sentence.ordinal + 1,
        last_sentence_ordinal=sentence.ordinal + 1,
        section_id=plan.sections[0].section_id,
        paragraph_id=paragraph.paragraph_id,
        first_sentence_id=sentence.sentence_id,
        last_sentence_id=sentence.sentence_id,
        locator="opening section",
        excerpt=sentence.text.strip(),
    )
    return SourceDossier(
        execution_run_id=run_id,
        project_id=project_id,
        source_id=plan.source_id,
        source_ordinal=plan.source_ordinal,
        source_title=plan.title,
        source_language=plan.language,
        source_sha256=plan.source_sha256,
        source_char_count=plan.source_char_count,
        estimated_source_tokens=plan.estimated_source_tokens,
        digestion_mode=plan.mode,
        section_ids=[section.section_id for section in plan.sections],
        model="test-model",
        executive_synthesis="Bounded synthesis.",
        method_profile=MethodProfile(
            source_kind="study",
            design="observational",
            population_or_scope="adolescents",
            measures_or_material="reported use and wellbeing",
            temporal_scope="single period",
        ),
        findings=[
            SourceFinding(
                finding=f"Finding {number}",
                causal_status=CausalStatus.CORRELATIONAL,
                evidence_strength=EvidenceStrength.MODERATE,
                anchors=[anchor],
            )
            for number in range(findings)
        ],
    )


def test_source_size_allocates_analysts_without_cutting_paragraphs() -> None:
    source = _source(121_342)
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)

    assert plan.mode is DigestionMode.HIERARCHICAL
    assert plan.planned_model_calls == 3
    assert len(plan.sections) == 2
    assert "".join(section.text for section in plan.sections) == source.text
    assert all(
        paragraph.char_start >= section.char_start and paragraph.char_end <= section.char_end
        for section in plan.sections
        for paragraph in section.paragraphs
    )


def test_size_alone_cannot_force_a_cut_inside_one_semantic_paragraph() -> None:
    source = NormalizedSource(
        source_id=uuid4(),
        text="A long but indivisible argument " + ("with context " * 10_000) + ".",
        language="en",
        title="One paragraph",
    )

    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)

    assert estimate_tokens(source.text) > 24_000
    assert plan.mode is DigestionMode.DIRECT
    assert len(plan.sections) == 1
    assert plan.sections[0].text == source.text


def test_long_source_is_split_without_overlap_gaps_or_content_loss() -> None:
    source = _source(956_001, title="Full institutional report")
    planner = SourceDigestionPlanner()
    first = planner.plan_source(source, source_ordinal=3)
    second = planner.plan_source(source, source_ordinal=3)

    assert first.mode is DigestionMode.HIERARCHICAL
    assert len(first.sections) == planner.max_analysts_per_source
    assert first.planned_model_calls == len(first.sections) + 1
    assert "".join(section.text for section in first.sections) == source.text
    assert max(section.estimated_tokens for section in first.sections) <= planner.hard_section_tokens
    assert [section.section_id for section in first.sections] == [section.section_id for section in second.sections]


def test_realistic_a_and_b_call_shape_replaces_chunk_explosion() -> None:
    planner = SourceDigestionPlanner()
    project_id = uuid4()
    common = [_source(121_342), _source(35_310), _source(67_980)]
    test_a = planner.plan(project_id=project_id, sources=[*common, _source(12_140)])
    test_b = planner.plan(project_id=project_id, sources=[*common, _source(956_001)])

    assert test_a.planned_model_calls == 7
    assert test_b.planned_model_calls == 15
    assert test_a.planned_model_calls < 77
    assert test_b.planned_model_calls < 298


def test_dossier_evidence_must_be_a_verbatim_source_excerpt() -> None:
    source = _source(10_000)
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)
    project_id, run_id = uuid4(), uuid4()
    dossier = _dossier(plan, project_id=project_id, run_id=run_id)

    validate_dossier_evidence(dossier, plan.sections)
    invented = dossier.model_copy(
        update={
            "findings": [
                dossier.findings[0].model_copy(
                    update={"anchors": [dossier.findings[0].anchors[0].model_copy(update={"excerpt": "invented quote"})]}
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="coordinates"):
        validate_dossier_evidence(invented, plan.sections)


def test_dossier_evidence_accepts_unicode_compatibility_normalization() -> None:
    source = NormalizedSource(
        source_id=uuid4(),
        text="Speciﬁcally, the observed association remained bounded.",
        language="en",
        title="PDF ligature",
    )
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)
    dossier = _dossier(plan, project_id=uuid4(), run_id=uuid4(), findings=1)
    normalized_anchor = dossier.findings[0].anchors[0].model_copy(
        update={"excerpt": "Specifically, the observed association remained bounded."}
    )
    dossier = dossier.model_copy(
        update={
            "findings": [
                dossier.findings[0].model_copy(update={"anchors": [normalized_anchor]})
            ]
        }
    )

    validate_dossier_evidence(dossier, plan.sections)


def test_dossier_evidence_accepts_a_complete_sentence_longer_than_400_characters() -> None:
    sentence = "A complete evidentiary sentence " + ("with necessary context " * 24) + "and its qualification."
    assert len(sentence) > 400
    source = NormalizedSource(source_id=uuid4(), text=sentence, language="en", title="Long sentence")
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)
    dossier = _dossier(plan, project_id=uuid4(), run_id=uuid4(), findings=1)

    validate_dossier_evidence(dossier, plan.sections)


def test_dossier_evidence_cannot_join_sentences_across_paragraphs() -> None:
    source = NormalizedSource(
        source_id=uuid4(),
        text="First complete sentence.\n\nSecond complete sentence.",
        language="en",
        title="Two paragraphs",
    )
    plan = SourceDigestionPlanner().plan_source(source, source_ordinal=0)
    dossier = _dossier(plan, project_id=uuid4(), run_id=uuid4(), findings=1)
    second_sentence = plan.sections[0].paragraphs[1].sentences[0]
    anchor = dossier.findings[0].anchors[0].model_copy(
        update={
            "paragraph_ordinal": plan.sections[0].paragraphs[1].ordinal + 1,
            "last_sentence_id": second_sentence.sentence_id,
        }
    )
    dossier = dossier.model_copy(
        update={"findings": [dossier.findings[0].model_copy(update={"anchors": [anchor]})]}
    )

    with pytest.raises(ValueError, match="hashes do not match"):
        validate_dossier_evidence(dossier, plan.sections)


def test_equal_output_ceiling_prevents_raw_length_from_becoming_representation_weight() -> None:
    planner = SourceDigestionPlanner()
    project_id, run_id = uuid4(), uuid4()
    short_plan = planner.plan_source(_source(12_000), source_ordinal=0)
    long_plan = planner.plan_source(_source(956_001), source_ordinal=1)
    dossiers = [
        _dossier(short_plan, project_id=project_id, run_id=run_id),
        _dossier(long_plan, project_id=project_id, run_id=run_id),
    ]

    metrics = source_balance_metrics(dossiers)

    assert metrics[1].raw_token_share > 0.98
    assert 0.45 <= metrics[1].dossier_character_share <= 0.55
    assert metrics[1].finding_share == 0.5


def test_sources_with_no_findings_are_not_forced_to_invent_one() -> None:
    planner = SourceDigestionPlanner()
    project_id, run_id = uuid4(), uuid4()
    plans = [
        planner.plan_source(_source(10_000), source_ordinal=0),
        planner.plan_source(_source(20_000), source_ordinal=1),
    ]
    dossiers = [_dossier(plan, project_id=project_id, run_id=run_id, findings=0) for plan in plans]

    metrics = source_balance_metrics(dossiers)

    assert [item.finding_share for item in metrics] == [0.5, 0.5]
