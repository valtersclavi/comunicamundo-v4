from __future__ import annotations

from hashlib import sha256
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import EditorialBrief, NormalizedSource, PlanningRequest
from comunicamundo_v4.hefesto.digestion_contracts import (
    ArtifactPointer,
    CausalStatus,
    DigestionExecutionRun,
    DigestionRunState,
    DominanceAssessment,
    EvidenceKeyReference,
    EvidenceMatrixProposal,
    EvidenceStrength,
    MethodProfile,
    SectionDigestProposal,
    SourceDossierProposal,
    SourceFindingProposal,
)
from comunicamundo_v4.hefesto.digestion_orchestrator import DigestionExecutionError, DigestionOrchestrator
from comunicamundo_v4.hefesto.digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
from comunicamundo_v4.hefesto.execution_scheduler import BoundedFairExecutor, SmoothedTokenBucket
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner


class MemoryArtifacts:
    def __init__(self):
        self.objects = {}

    async def _persist(self, model, path):
        data = model.model_dump_json().encode()
        pointer = ArtifactPointer(uri=f"gs://memory/{path}", sha256=sha256(data).hexdigest())
        existing = self.objects.get(pointer.uri)
        if existing is not None and existing != data:
            raise ValueError("conflicting immutable artifact")
        self.objects[pointer.uri] = data
        return pointer

    async def persist_request(self, plan, request):
        return await self._persist(request, f"{plan.execution_run_id}/request.json")

    async def persist_plan(self, plan):
        return await self._persist(plan, f"{plan.execution_run_id}/plan.json")

    async def persist_section_digest(self, plan, digest):
        return await self._persist(digest, f"{plan.execution_run_id}/sections/{digest.section_id}.json")

    async def persist_source_dossier(self, plan, dossier):
        return await self._persist(dossier, f"{plan.execution_run_id}/dossiers/{dossier.source_id}.json")

    async def persist_evidence_matrix(self, plan, matrix):
        return await self._persist(matrix, f"{plan.execution_run_id}/matrix.json")

    async def persist_understanding(self, plan, understanding):
        return await self._persist(understanding, f"{plan.execution_run_id}/understanding.json")

    async def load(self, pointer, schema):
        data = self.objects[pointer.uri]
        if sha256(data).hexdigest() != pointer.sha256:
            raise ValueError("integrity")
        return schema.model_validate_json(data)


class MemoryRuns:
    def __init__(self):
        self.values = {}

    async def create(self, run):
        existing = self.values.get(run.execution_run_id)
        if existing is not None and existing != run:
            raise ValueError("conflict")
        self.values[run.execution_run_id] = run
        return run

    async def get(self, run_id):
        return self.values.get(run_id)

    async def transition(self, run_id, change):
        updated = change(self.values[run_id])
        self.values[run_id] = updated
        return updated


class MemoryWork:
    def __init__(self):
        self.values = {}

    async def create(self, unit):
        key = (unit.execution_run_id, unit.work_unit_id)
        existing = self.values.get(key)
        if existing is not None and existing != unit:
            raise ValueError("conflict")
        self.values[key] = unit
        return unit

    async def get(self, run_id, work_id):
        return self.values.get((run_id, work_id))

    async def transition(self, run_id, work_id, change):
        key = (run_id, work_id)
        updated = change(self.values[key])
        self.values[key] = updated
        return updated


class ResponsiveGateway:
    def __init__(self):
        self.calls = []

    async def generate(self, intent, *, prompt, system_instruction=None, response_schema=None):
        self.calls.append(intent.work_unit_id)
        evidence_match = re.search(r"\[EVIDENCE_KEY (E_[0-9a-f]{16})\]", prompt) or re.search(
            r'"first_evidence_key": "(E_[0-9a-f]{16})"', prompt
        )
        if response_schema is SectionDigestProposal:
            proposal = SectionDigestProposal(
                synopsis="Resumo da seção.",
                findings=[_finding(evidence_match.group(1))],
            )
        elif response_schema is SourceDossierProposal:
            proposal = SourceDossierProposal(
                executive_synthesis="Síntese da fonte.",
                method_profile=MethodProfile(
                    source_kind="estudo",
                    design="observacional",
                    population_or_scope="adolescentes",
                    measures_or_material="questionário",
                    temporal_scope="um período",
                ),
                findings=[_finding(evidence_match.group(1))],
                limitations=["Não identifica causalidade."],
            )
        elif response_schema is EvidenceMatrixProposal:
            proposal = EvidenceMatrixProposal(
                central_ideas=["Os efeitos variam conforme o contexto."],
                relations_and_tensions=["Associação não prova causalidade."],
                dominance_assessment=DominanceAssessment(
                    long_source_dominance_detected=False,
                    explanation="Os dossiês têm o mesmo teto.",
                ),
                integrated_synthesis="Síntese integrada das fontes.",
            )
        else:
            raise AssertionError(response_schema)
        return SimpleNamespace(parsed=proposal)


def _finding(evidence_key):
    return SourceFindingProposal(
        finding="Foi observada uma associação.",
        causal_status=CausalStatus.CORRELATIONAL,
        evidence_strength=EvidenceStrength.MODERATE,
        anchors=[
            EvidenceKeyReference(
                first_evidence_key=evidence_key,
                last_evidence_key=evidence_key,
            )
        ],
    )


def _request():
    return PlanningRequest(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="Redes sociais e saúde mental",
            approach="Neutra",
            duration_minutes=15,
            output_language="pt-BR",
        ),
        sources=[
            NormalizedSource(source_id=uuid4(), text="Evidence line.", language="en", title="Short"),
            NormalizedSource(
                source_id=uuid4(),
                text=("Evidence line.\n\n" * 100).strip(),
                language="en",
                title="Long",
            ),
        ],
    )


def _orchestrator():
    artifacts = MemoryArtifacts()
    runs = MemoryRuns()
    work = MemoryWork()
    gateway = ResponsiveGateway()
    planner = SourceDigestionPlanner(
        analyst_load_tokens=100,
        max_analysts_per_source=4,
        hard_section_tokens=150,
    )
    bucket = SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5)
    executor = BoundedFairExecutor(bucket, max_concurrency=4, per_project_concurrency=4)
    orchestrator = DigestionOrchestrator(
        planner=planner,
        calls=DigestionCallFactory(DigestionRuntimeConfig()),
        gateway=gateway,
        executor=executor,
        artifacts=artifacts,
        runs=runs,
        work=work,
        worker_id="test-worker",
    )
    return orchestrator, artifacts, runs, work, gateway


@pytest.mark.asyncio
async def test_full_digestion_is_checkpointed_and_resume_makes_no_model_calls() -> None:
    orchestrator, artifacts, runs, work, gateway = _orchestrator()
    request = _request()

    result = await orchestrator.start(request)

    assert result.execution_run.state is DigestionRunState.UNDERSTANDING_READY
    assert len(result.source_dossiers) == 2
    assert result.execution_run.completed_model_calls == result.digestion_plan.planned_model_calls
    assert len(gateway.calls) == result.digestion_plan.planned_model_calls
    assert len(work.values) == result.digestion_plan.planned_model_calls
    assert all(unit.status.value == "succeeded" for unit in work.values.values())
    assert result.execution_run.artifacts.section_digests
    assert len(result.execution_run.artifacts.source_dossiers) == 2
    object_count = len(artifacts.objects)
    call_count = len(gateway.calls)

    resumed = await orchestrator.resume(result.execution_run.execution_run_id)

    assert resumed.evidence_matrix == result.evidence_matrix
    assert resumed.editorial_understanding == result.editorial_understanding
    assert len(gateway.calls) == call_count
    assert len(artifacts.objects) == object_count
    assert await runs.get(result.execution_run.execution_run_id) == result.execution_run


@pytest.mark.asyncio
async def test_identical_text_in_distinct_sources_never_collides_or_merges_dossiers() -> None:
    orchestrator, _, _, work, gateway = _orchestrator()
    request = _request()
    repeated = "Evidence line."
    request = request.model_copy(
        update={
            "sources": [
                NormalizedSource(source_id=uuid4(), text=repeated, language="en", title="One"),
                NormalizedSource(source_id=uuid4(), text=repeated, language="en", title="Two"),
            ]
        }
    )

    result = await orchestrator.start(request)

    assert len({item.source_id for item in result.source_dossiers}) == 2
    assert len(work.values) == 3  # two direct dossiers + one evidence matrix
    assert len(gateway.calls) == 3


@pytest.mark.asyncio
async def test_post_provider_persistence_failure_is_terminal_and_never_repeats_paid_call() -> None:
    orchestrator, artifacts, runs, work, gateway = _orchestrator()
    request = _request().model_copy(update={"sources": [_request().sources[0]]})

    async def fail_after_provider(plan, dossier):
        raise RuntimeError("GCS unavailable after provider response")

    artifacts.persist_source_dossier = fail_after_provider

    with pytest.raises(DigestionExecutionError):
        await orchestrator.start(request)

    assert len(gateway.calls) == 1
    run = next(iter(runs.values.values()))
    assert run.state is DigestionRunState.FAILED
    assert next(iter(work.values.values())).status.value == "failed"

    with pytest.raises(DigestionExecutionError):
        await orchestrator.resume(run.execution_run_id)

    assert len(gateway.calls) == 1
