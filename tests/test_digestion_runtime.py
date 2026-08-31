from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import EditorialBrief, NormalizedSource
from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DigestionMode,
    DominanceAssessment,
    EvidenceAnchor,
    EvidenceMatrixProposal,
    EvidenceStrength,
    MethodProfile,
    SectionDigestProposal,
    SourceDossierProposal,
    SourceFinding,
)
from comunicamundo_v4.hefesto.digestion_prompts import DIGESTION_SYSTEM_INSTRUCTION
from comunicamundo_v4.hefesto.digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
from comunicamundo_v4.hefesto.execution_contracts import text_sha256
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner


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


def _finding(section_id: str, excerpt: str = "Evidência observada.") -> SourceFinding:
    return SourceFinding(
        finding="Foi observada uma associação.",
        causal_status=CausalStatus.CORRELATIONAL,
        evidence_strength=EvidenceStrength.MODERATE,
        anchors=[EvidenceAnchor(section_id=section_id, locator="trecho", excerpt=excerpt)],
    )


def _dossier_proposal(section_id: str, excerpt: str = "Evidência observada.") -> SourceDossierProposal:
    return SourceDossierProposal(
        executive_synthesis="Síntese delimitada da fonte.",
        method_profile=MethodProfile(
            source_kind="estudo",
            design="observacional",
            population_or_scope="adolescentes",
            measures_or_material="questionários",
            temporal_scope="um período",
        ),
        findings=[_finding(section_id, excerpt)],
        limitations=["O desenho não identifica causalidade."],
    )


@pytest.mark.asyncio
async def test_direct_dossier_is_an_immutable_budgeted_gateway_call() -> None:
    source = _source("Evidência observada. O desenho foi observacional.")
    planner = SourceDigestionPlanner()
    plan = planner.plan(project_id=uuid4(), sources=[source])
    source_plan = plan.sources[0]
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    call = factory.direct_dossier(plan=plan, source=source, source_plan=source_plan, brief=_brief())
    gateway = FakeGateway(_dossier_proposal(source_plan.sections[0].section_id))

    dossier = await call.invoke(gateway)

    assert dossier.source_id == source.source_id
    assert dossier.digestion_mode is DigestionMode.DIRECT
    assert call.intent.prompt_sha256 == text_sha256(call.prompt)
    assert call.intent.system_instruction_sha256 == text_sha256(DIGESTION_SYSTEM_INSTRUCTION)
    assert call.intent.budget.max_output_tokens == 8_000
    assert gateway.calls[0][3] is SourceDossierProposal


@pytest.mark.asyncio
async def test_direct_dossier_fails_closed_on_invented_quote() -> None:
    source = _source("Evidência observada.")
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    call = factory.direct_dossier(plan=plan, source=source, source_plan=plan.sources[0], brief=_brief())

    with pytest.raises(ValueError, match="absent"):
        await call.invoke(FakeGateway(_dossier_proposal(plan.sources[0].sections[0].section_id, "Citação inventada.")))


@pytest.mark.asyncio
async def test_hierarchical_reducer_can_only_reuse_section_digest_anchors() -> None:
    text = ("Evidência observada.\n\n" * 30).strip()
    source = _source(text)
    planner = SourceDigestionPlanner(direct_source_tokens=10, target_section_tokens=30, max_section_tokens=40)
    plan = planner.plan(project_id=uuid4(), sources=[source])
    source_plan = plan.sources[0]
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    digests = []
    for section in source_plan.sections:
        proposal = SectionDigestProposal(
            synopsis="Resumo.",
            findings=[_finding(section.section_id)],
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
    first_id = source_plan.sections[0].section_id

    dossier = await reduce_call.invoke(FakeGateway(_dossier_proposal(first_id)))
    assert dossier.digestion_mode is DigestionMode.HIERARCHICAL

    changed_locator = _dossier_proposal(first_id).model_copy(
        update={
            "findings": [
                _finding(first_id).model_copy(
                    update={"anchors": [EvidenceAnchor(section_id=first_id, locator="inventado", excerpt="Evidência observada.")]}
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="absent from its section digests"):
        await reduce_call.invoke(FakeGateway(changed_locator))


@pytest.mark.asyncio
async def test_matrix_call_preserves_source_order_and_produces_auditable_balance() -> None:
    sources = [_source("Evidência observada."), _source("Evidência observada. Segunda fonte.")]
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=sources)
    factory = DigestionCallFactory(DigestionRuntimeConfig())
    dossiers = []
    for source, source_plan in zip(sources, plan.sources, strict=True):
        call = factory.direct_dossier(plan=plan, source=source, source_plan=source_plan, brief=_brief())
        dossiers.append(await call.invoke(FakeGateway(_dossier_proposal(source_plan.sections[0].section_id))))
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
    assert matrix_call.intent.budget.max_output_tokens == 12_000


def test_runtime_rejects_a_prompt_that_exceeds_its_declared_input_budget() -> None:
    source = _source("Evidência observada. " * 1_000)
    plan = SourceDigestionPlanner().plan(project_id=uuid4(), sources=[source])
    factory = DigestionCallFactory(DigestionRuntimeConfig(max_input_tokens=100))

    with pytest.raises(ValueError, match="estimated input"):
        factory.direct_dossier(plan=plan, source=source, source_plan=plan.sources[0], brief=_brief())

