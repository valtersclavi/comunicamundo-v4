from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
)
from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DominanceAssessment,
    EvidenceAnchor,
    EvidenceMatrix,
    EvidenceStrength,
    MethodProfile,
    SourceBalanceMetric,
    SourceDossier,
    SourceFinding,
)
from comunicamundo_v4.hefesto.production_evidence import ProductionEvidenceBuilder
from comunicamundo_v4.hefesto.production_v2_contracts import ArticleDraftV2, ProductionEvidenceBundle


def _dossier(*, run_id, project_id, source_id, ordinal, raw_chars, findings):
    section = f"{ordinal + 1:064x}"
    return SourceDossier(
        execution_run_id=run_id,
        project_id=project_id,
        source_id=source_id,
        source_ordinal=ordinal,
        source_title=f"Source {ordinal}",
        source_language="en",
        source_sha256=f"{ordinal + 10:064x}",
        source_char_count=raw_chars,
        estimated_source_tokens=max(1, raw_chars // 3),
        digestion_mode="direct",
        section_ids=[section],
        model="test-model",
        executive_synthesis="Social media effects vary with vulnerability and use.",
        method_profile=MethodProfile(
            source_kind="study",
            design="observational",
            population_or_scope="adolescents",
            measures_or_material="questionnaires",
            temporal_scope="one period",
        ),
        findings=[
            SourceFinding(
                finding=text,
                causal_status=CausalStatus.CORRELATIONAL,
                evidence_strength=EvidenceStrength.MODERATE,
                anchors=[
                    EvidenceAnchor(
                        section_id=section,
                        paragraph_id=f"{ordinal + 100:064x}",
                        first_sentence_id=f"{index + 1000:064x}",
                        last_sentence_id=f"{index + 1000:064x}",
                        locator=f"finding {index}",
                        excerpt=f"Evidence {ordinal}-{index}",
                    )
                ],
            )
            for index, text in enumerate(findings)
        ],
        limitations=["Observational evidence cannot establish causality."],
    )


def _fixture():
    run_id, project_id = uuid4(), uuid4()
    source_ids = [uuid4(), uuid4()]
    dossiers = [
        _dossier(
            run_id=run_id,
            project_id=project_id,
            source_id=source_ids[0],
            ordinal=0,
            raw_chars=12_000,
            findings=[
                "Vulnerability changes the association with mental health.",
                "Active and passive use have different patterns.",
                "Sleep can be displaced by late use.",
            ],
        ),
        _dossier(
            run_id=run_id,
            project_id=project_id,
            source_id=source_ids[1],
            ordinal=1,
            raw_chars=956_000,
            findings=[
                "Developmental stage changes the observed association.",
                "The evidence does not establish a uniform causal effect.",
                "Platform design can shape exposure.",
            ],
        ),
    ]
    matrix = EvidenceMatrix(
        execution_run_id=run_id,
        project_id=project_id,
        source_fingerprint="f" * 64,
        source_ids=source_ids,
        dossier_sha256s=[item.content_sha256 for item in dossiers],
        source_balance=[
            SourceBalanceMetric(
                source_id=source_ids[0],
                raw_token_share=0.02,
                dossier_character_share=0.5,
                finding_share=0.5,
                compression_ratio=0.1,
            ),
            SourceBalanceMetric(
                source_id=source_ids[1],
                raw_token_share=0.98,
                dossier_character_share=0.5,
                finding_share=0.5,
                compression_ratio=0.01,
            ),
        ],
        central_ideas=["Effects vary by vulnerability and developmental stage."],
        relations_and_tensions=["Association does not establish causality."],
        source_limits=["Most evidence is observational."],
        dominance_assessment=DominanceAssessment(
            long_source_dominance_detected=False,
            explanation="Equal dossier ceilings prevent raw-length dominance.",
        ),
        integrated_synthesis="The evidence supports a heterogeneous, contextual interpretation.",
        model="test-model",
    )
    project = EditorialProject(
        project_id=project_id,
        narrative_axis_id=uuid4(),
        narrative_axis="The impact varies with vulnerability and developmental stage.",
        brief=EditorialBrief(
            theme="Social media and adolescent mental health",
            approach="neutral",
            duration_minutes=15,
        ),
        working_title="Beyond screen time",
        editorial_promise="Explain heterogeneous effects.",
        core_subjects=[
            EditorialSubject(
                title="Vulnerability and development",
                development="Show why the same use can relate differently to mental health.",
            ),
            EditorialSubject(
                title="Causality limits",
                development="Distinguish association from causal evidence.",
            ),
        ],
    ).approve()
    return run_id, project, matrix, dossiers


def test_evidence_packets_are_bounded_balanced_and_graph_free() -> None:
    run_id, project, matrix, dossiers = _fixture()

    bundle = ProductionEvidenceBuilder(
        max_total_subject_chars=8_000,
        max_subject_chars=5_000,
        max_shared_chars=3_000,
        findings_per_source=2,
    ).build(
        execution_run_id=run_id,
        project=project,
        matrix=matrix,
        dossiers=dossiers,
    )

    assert len(bundle.subject_packets) == 2
    assert all(len(item.rendered_context) <= 4_000 for item in bundle.subject_packets)
    assert all(len(item.findings) <= 4 for item in bundle.subject_packets)
    assert all(
        {finding.source_id for finding in item.findings} == set(matrix.source_ids)
        for item in bundle.subject_packets
    )
    assert "Causality: correlational" in bundle.subject_packets[0].rendered_context
    assert "SOURCE DOMINANCE" in bundle.shared_context
    assert "graph" not in bundle.shared_context.lower()
    assert ProductionEvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle
    draft = ArticleDraftV2(body_markdown="# Título\n\nTexto.")
    assert ArticleDraftV2.model_validate_json(draft.model_dump_json()) == draft


def test_raw_source_length_does_not_increase_production_packet_weight() -> None:
    run_id, project, matrix, dossiers = _fixture()

    bundle = ProductionEvidenceBuilder(findings_per_source=1).build(
        execution_run_id=run_id,
        project=project,
        matrix=matrix,
        dossiers=dossiers,
    )

    for packet in bundle.subject_packets:
        counts = {source_id: 0 for source_id in matrix.source_ids}
        for finding in packet.findings:
            counts[finding.source_id] += 1
        assert counts[matrix.source_ids[0]] == counts[matrix.source_ids[1]] == 1
