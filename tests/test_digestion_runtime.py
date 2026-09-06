from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import EditorialBrief, NormalizedSource
from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DigestionItemKind,
    DigestionItemProposal,
    DigestionMode,
    DominanceAssessment,
    EvidenceKeyReference,
    EvidenceReference,
    EvidenceMatrixProposal,
    EvidenceStrength,
    ReducedSourceDossierProposal,
    SectionDigestProposal,
    SourceDossierProposal,
    SourceFindingProposal,
    SourceSection,
)
from comunicamundo_v4.hefesto.digestion_prompts import DIGESTION_SYSTEM_INSTRUCTION
from comunicamundo_v4.hefesto.digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
from comunicamundo_v4.hefesto.execution_contracts import text_sha256
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner, evidence_key


class FakeGateway:
    def __init__(self, proposal) -> None:
        self.proposal = proposal
        self.calls = []

    async def generate(self, intent, *, prompt, system_instruction=None, response_schema=None):
        self.calls.append((intent, prompt, system_instruction, response_schema))
        return SimpleNamespace(parsed=self.proposal)


def _brief() -> EditorialBrief:
    return EditorialBrief(
        theme="O impacto das redes sociais na saúde mental dos adolescentes",
        approach="Neutra",
        duration_minutes=15,
        output_language="pt-BR",
    )


def _source(text: str) -> NormalizedSource:
    return NormalizedSource(source_id=uuid4(), text=text, language="pt-BR", title="Estudo")


def _finding(section: SourceSection) -> DigestionItemProposal:
    paragraph = section.paragraphs[0]
    sentence = paragraph.sentences[0]
    return DigestionItemProposal(
        kind=DigestionItemKind.FINDING,
        text="Foi observada uma associação.",
        causal_status=CausalStatus.CORRELATIONAL,
        evidence_strength=EvidenceStrength.MODERATE,
        first_evidence_key=evidence_key(sentence.sentence_id),
        last_evidence_key=evidence_key(sentence.sentence_id),
        qualifications=[],
    )


def _dossier_proposal(section: SourceSection) -> SourceDossierProposal:
    return SourceDossierProposal(
        executive_synthesis="Síntese delimitada da fonte.",
        items=[
            _finding(section),
            *_profile_items(),
            _text_item(DigestionItemKind.LIMITATION, "O desenho não identifica causalidade."),
            _text_item(DigestionItemKind.RECOMMENDATION, "Proteja o sono dos adolescentes."),
            _text_item(DigestionItemKind.METHODOLOGICAL_FACT, "A amostra foi ponderada."),
            DigestionItemProposal(
                kind="content",
                category_detail="institutional_context",
                text="A fonte também exerce papel normativo.",
                causal_status="",
                evidence_strength="",
                first_evidence_key="",
                last_evidence_key="",
                qualifications=[],
            ),
        ],
    )


def _text_item(kind: DigestionItemKind, text: str) -> DigestionItemProposal:
    return DigestionItemProposal(
        kind=kind,
        text=text,
        causal_status="",
        evidence_strength="",
        first_evidence_key="",
        last_evidence_key="",
        qualifications=[],
    )


def _profile_items() -> list[DigestionItemProposal]:
    return [
        _text_item(DigestionItemKind.SOURCE_KIND, "estudo"),
        _text_item(DigestionItemKind.DESIGN, "observacional"),
        _text_item(DigestionItemKind.POPULATION_OR_SCOPE, "adolescentes"),
        _text_item(DigestionItemKind.MEASURES_OR_MATERIAL, "questionários"),
        _text_item(DigestionItemKind.TEMPORAL_SCOPE, "um período"),
    ]


def _reduced_dossier_proposal() -> ReducedSourceDossierProposal:
    return ReducedSourceDossierProposal(
        executive_synthesis="Síntese delimitada da fonte.",
        items=[
            *_profile_items(),
            _text_item(DigestionItemKind.LIMITATION, "O desenho não identifica causalidade."),
        ],
    )


@pytest.mark.asyncio
async def test_direct_dossier_is_an_immutable_budgeted_gateway_call() -> None:
    source = _source("Evidência observada. O desenho foi observacional.")
    planner = SourceDigestionPlanner()
    plan = planner.plan(project_id=uuid4(), sources=[source])
    source_plan = plan.sources[0]
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    call = factory.direct_dossier(plan=plan, source=source, source_plan=source_plan, brief=_brief())
    gateway = FakeGateway(_dossier_proposal(source_plan.sections[0]))

    dossier = await call.invoke(gateway)

    assert dossier.source_id == source.source_id
    assert dossier.digestion_mode is DigestionMode.DIRECT
    assert call.intent.prompt_sha256 == text_sha256(call.prompt)
    assert call.intent.system_instruction_sha256 == text_sha256(DIGESTION_SYSTEM_INSTRUCTION)
    assert call.intent.budget.max_output_tokens == 65_536
    assert call.intent.budget.capacity_output_tokens == 16_000
    assert gateway.calls[0][3] is SourceDossierProposal
    assert dossier.findings[0].anchors[0].locator == "section 1; paragraph 1; sentences 1"
    assert dossier.recommendations == ["Proteja o sono dos adolescentes."]
    assert dossier.methodological_facts == ["A amostra foi ponderada."]
    assert [item.model_dump() for item in dossier.other_items] == [
        {"category": "institutional_context", "text": "A fonte também exerce papel normativo."}
    ]


def test_intermediate_digest_has_no_editorial_quantity_or_prose_caps() -> None:
    source = _source("Evidência observada.")
    section = SourceDigestionPlanner().plan_source(source, source_ordinal=0).sections[0]
    items = [
        *[_finding(section) for _ in range(30)],
        *[_text_item(DigestionItemKind.METHOD, f"Método distinto {index}: " + ("detalhe " * 100)) for index in range(25)],
        *[_text_item(DigestionItemKind.LIMITATION, f"Limitação {index}") for index in range(20)],
        *[_text_item(DigestionItemKind.UNCERTAINTY, f"Incerteza {index}") for index in range(20)],
        *[_text_item(DigestionItemKind.DISTINCTIVE_CONTRIBUTION, f"Contribuição {index}") for index in range(20)],
        *[_text_item(DigestionItemKind.RECOMMENDATION, f"Recomendação {index}") for index in range(20)],
    ]
    proposal = SectionDigestProposal(synopsis="S" * 10_000, items=items)

    assert len(proposal.items) == 135


@pytest.mark.asyncio
async def test_section_preserves_recommendations_as_a_distinct_nonfinding_category() -> None:
    source = _source("A organização recomenda proteger o sono.")
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    section = plan.sources[0].sections[0]
    proposal = SectionDigestProposal(
        synopsis="Recomendação institucional.",
        items=[_text_item(DigestionItemKind.RECOMMENDATION, "Proteja o sono dos adolescentes.")],
    )
    call = DigestionCallFactory(DigestionRuntimeConfig()).section_digest(
        plan=plan,
        source=source,
        source_plan=plan.sources[0],
        section_ordinal=0,
    )

    digest = await call.invoke(FakeGateway(proposal))

    assert digest.findings == []
    assert digest.recommendations == ["Proteja o sono dos adolescentes."]


@pytest.mark.parametrize(
    "transport_value",
    ["null_finding", "null_result", "no_effect", "no_association"],
)
def test_null_outcome_labels_do_not_replace_the_causal_dimension(transport_value: str) -> None:
    assert CausalStatus(transport_value) is CausalStatus.NO_CAUSAL_CLAIM


@pytest.mark.parametrize("transport_value", ["temporal_association", "temporal"])
def test_temporal_aliases_preserve_longitudinal_causal_status(transport_value: str) -> None:
    assert CausalStatus(transport_value) is CausalStatus.LONGITUDINAL_ASSOCIATION


def test_evidence_reference_canonicalizes_reversed_transport_endpoints() -> None:
    reference = EvidenceReference(
        section_ordinal=1,
        paragraph_ordinal=2,
        first_sentence_ordinal=4,
        last_sentence_ordinal=1,
    )

    assert reference.first_sentence_ordinal == 1
    assert reference.last_sentence_ordinal == 4


@pytest.mark.parametrize("transport_value", [status.value for status in CausalStatus])
def test_causal_vocabulary_in_strength_is_epistemically_indeterminate(
    transport_value: str,
) -> None:
    assert EvidenceStrength(transport_value) is EvidenceStrength.INDETERMINATE


def test_single_anchor_object_is_canonicalized_to_transport_list() -> None:
    proposal = SourceFindingProposal.model_validate(
        {
            "finding": "Achado sustentado.",
            "causal_status": "correlational",
            "evidence_strength": "moderate",
            "anchors": {
                "first_evidence_key": "E_0123456789abcdef",
                "last_evidence_key": "E_0123456789abcdef",
            },
        }
    )

    assert len(proposal.anchors) == 1
    assert proposal.anchors[0].first_evidence_key == "E_0123456789abcdef"


def test_atomic_transport_has_one_uniform_flat_item_shape() -> None:
    proposal = SectionDigestProposal.model_validate(
        {
            "synopsis": "Síntese.",
            "items": [{
                "kind": "finding",
                "text": "Achado sustentado.",
                "causal_status": "correlational",
                "evidence_strength": "moderate",
                "first_evidence_key": "E_0123456789abcdef",
                "last_evidence_key": "E_0123456789abcdef",
                "qualifications": [],
            }],
        }
    )

    assert len(proposal.items) == 1
    assert proposal.items[0].text == "Achado sustentado."


@pytest.mark.asyncio
async def test_direct_dossier_fails_closed_on_invented_evidence_key() -> None:
    source = _source("Evidência observada.")
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    call = factory.direct_dossier(plan=plan, source=source, source_plan=plan.sources[0], brief=_brief())

    section = plan.sources[0].sections[0]
    proposal = _dossier_proposal(section)
    proposal = proposal.model_copy(
        update={
            "items": [
                proposal.items[0].model_copy(update={"last_evidence_key": "E_ffffffffffffffff"}),
                *proposal.items[1:],
            ]
        }
    )
    with pytest.raises(ValueError, match="unknown key"):
        await call.invoke(FakeGateway(proposal))


@pytest.mark.asyncio
async def test_opaque_keys_resolve_an_exact_consecutive_sentence_range() -> None:
    source = _source("Primeira frase. Segunda frase.\n\nTerceira frase.")
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    section = plan.sources[0].sections[0]
    paragraph = section.paragraphs[0]
    first, second = paragraph.sentences
    proposal = _dossier_proposal(section)
    proposal = proposal.model_copy(
        update={
            "items": [proposal.items[0].model_copy(update={
                "first_evidence_key": evidence_key(first.sentence_id),
                "last_evidence_key": evidence_key(second.sentence_id),
            }), *proposal.items[1:]]
        }
    )
    call = DigestionCallFactory(DigestionRuntimeConfig()).direct_dossier(
        plan=plan,
        source=source,
        source_plan=plan.sources[0],
        brief=_brief(),
    )

    dossier = await call.invoke(FakeGateway(proposal))
    anchor = dossier.findings[0].anchors[0]
    assert anchor.first_sentence_id == first.sentence_id
    assert anchor.last_sentence_id == second.sentence_id
    assert anchor.excerpt == paragraph.text.strip()


@pytest.mark.asyncio
async def test_cross_paragraph_key_pair_becomes_two_single_sentence_anchors() -> None:
    source = _source("Primeira frase.\n\nSegunda frase.")
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    section = plan.sources[0].sections[0]
    first = section.paragraphs[0].sentences[0]
    second = section.paragraphs[1].sentences[0]
    proposal = _dossier_proposal(section)
    proposal = proposal.model_copy(
        update={
            "items": [proposal.items[0].model_copy(update={
                "first_evidence_key": evidence_key(first.sentence_id),
                "last_evidence_key": evidence_key(second.sentence_id),
            }), *proposal.items[1:]]
        }
    )
    call = DigestionCallFactory(DigestionRuntimeConfig()).direct_dossier(
        plan=plan,
        source=source,
        source_plan=plan.sources[0],
        brief=_brief(),
    )

    dossier = await call.invoke(FakeGateway(proposal))
    anchors = dossier.findings[0].anchors
    assert len(anchors) == 2
    assert [anchor.first_sentence_id for anchor in anchors] == [
        first.sentence_id,
        second.sentence_id,
    ]
    assert all(anchor.first_sentence_id == anchor.last_sentence_id for anchor in anchors)
    assert anchors[0].paragraph_id != anchors[1].paragraph_id


@pytest.mark.asyncio
async def test_hierarchical_reducer_carries_validated_findings_without_key_transcription() -> None:
    text = ("Evidência observada.\n\n" * 30).strip()
    source = _source(text)
    planner = SourceDigestionPlanner(
        analyst_load_tokens=30,
        max_analysts_per_source=4,
        hard_section_tokens=100,
    )
    plan = planner.plan(project_id=uuid4(), sources=[source])
    source_plan = plan.sources[0]
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    digests = []
    for section in source_plan.sections:
        proposal = SectionDigestProposal(
            synopsis="Resumo.",
            items=[_finding(section)],
        )
        call = factory.section_digest(
            plan=plan,
            source=source,
            source_plan=source_plan,
            section_ordinal=section.ordinal,
        )
        digests.append(await call.invoke(FakeGateway(proposal)))
    reduce_call = factory.reduced_dossier(
        plan=plan,
        source=source,
        source_plan=source_plan,
        section_digests=digests,
        brief=_brief(),
    )
    dossier = await reduce_call.invoke(FakeGateway(_reduced_dossier_proposal()))
    assert dossier.digestion_mode is DigestionMode.HIERARCHICAL
    assert len(dossier.findings) == len(digests)
    assert {
        anchor.section_id
        for finding in dossier.findings
        for anchor in finding.anchors
    } == {digest.section_id for digest in digests}

    illegal = _reduced_dossier_proposal().model_copy(
        update={"items": [_finding(source_plan.sections[0]), *_profile_items()]}
    )
    with pytest.raises(ValueError, match="cannot create or rewrite"):
        await reduce_call.invoke(FakeGateway(illegal))


@pytest.mark.asyncio
async def test_matrix_call_preserves_source_order_and_produces_auditable_balance() -> None:
    sources = [_source("Evidência observada."), _source("Evidência observada. Segunda fonte.")]
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=sources)
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    dossiers = []
    for source, source_plan in zip(sources, plan.sources, strict=True):
        call = factory.direct_dossier(plan=plan, source=source, source_plan=source_plan, brief=_brief())
        dossiers.append(await call.invoke(FakeGateway(_dossier_proposal(source_plan.sections[0]))))
    proposal = EvidenceMatrixProposal(
        central_ideas=["Os efeitos dependem do contexto."],
        relations_and_tensions=["Associação não equivale a causalidade."],
        dominance_assessment=DominanceAssessment(
            long_source_dominance_detected=False,
            explanation="Os dossiês têm teto equivalente.",
        ),
        integrated_synthesis="Síntese conjunta.",
    )
    matrix_call = factory.evidence_matrix(plan=plan, dossiers=dossiers, brief=_brief())

    matrix = await matrix_call.invoke(FakeGateway(proposal))

    assert matrix.source_ids == [source.source_id for source in sources]
    assert len(matrix.source_balance) == 2
    assert sum(item.dossier_character_share for item in matrix.source_balance) == pytest.approx(1)
    assert matrix_call.intent.budget.max_output_tokens == 65_536
    assert matrix_call.intent.budget.capacity_output_tokens == 16_000


def test_runtime_rejects_a_prompt_that_exceeds_its_declared_input_budget() -> None:
    source = _source("Evidência observada. " * 1_000)
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    factory = DigestionCallFactory(DigestionRuntimeConfig(max_input_tokens=100))

    with pytest.raises(ValueError, match="estimated input"):
        factory.direct_dossier(plan=plan, source=source, source_plan=plan.sources[0], brief=_brief())
