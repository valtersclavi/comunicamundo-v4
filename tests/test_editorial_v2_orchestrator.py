from __future__ import annotations

from hashlib import sha256
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectProposal,
    EditorialProjectStatus,
    EditorialSubjectProposal,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    NormalizedSource,
    PlanningRequest,
)
from comunicamundo_v4.hefesto.digestion_contracts import (
    ArtifactPointer,
    CausalStatus,
    DominanceAssessment,
    DigestionItemKind,
    DigestionItemProposal,
    EvidenceMatrixProposal,
    EvidenceStrength,
    SourceDossierProposal,
)
from comunicamundo_v4.hefesto.digestion_orchestrator import DigestionOrchestrator
from comunicamundo_v4.hefesto.digestion_runtime import DigestionCallFactory, DigestionRuntimeConfig
from comunicamundo_v4.hefesto.editorial_v2_contracts import (
    EditorialProjectRecordV2,
    EditorialV2State,
    HefestoTipV2,
    NarrativeAxisRecordV2,
)
from comunicamundo_v4.hefesto.editorial_v2_orchestrator import EditorialV2Orchestrator
from comunicamundo_v4.hefesto.editorial_v2_runtime import (
    EditorialV2CallFactory,
    EditorialV2RuntimeConfig,
)
from comunicamundo_v4.hefesto.execution_scheduler import BoundedFairExecutor, SmoothedTokenBucket
from comunicamundo_v4.hefesto.hefesto_tips import HefestoTipTiming, HefestoTipTimingResult
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner
from comunicamundo_v4.hefesto.production_v2_contracts import (
    ArticleDraftV2,
    ProductionEvidenceBundle,
    ProductionRecordV2,
)
from comunicamundo_v4.hefesto.contracts import EditorialReview, FinalArticle, NarrativePlan
from comunicamundo_v4.hefesto.production_v2_contracts import SubjectResearchBundleProposal


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

    async def load(self, pointer, schema):
        data = self.objects[pointer.uri]
        if sha256(data).hexdigest() != pointer.sha256:
            raise ValueError("integrity")
        return schema.model_validate_json(data)

    async def persist_request(self, plan, request):
        return await self._persist(request, f"{plan.execution_run_id}/digestion/request.json")

    async def persist_plan(self, plan):
        return await self._persist(plan, f"{plan.execution_run_id}/digestion/plan.json")

    async def persist_section_digest(self, plan, digest):
        return await self._persist(digest, f"{plan.execution_run_id}/digestion/sections/{digest.section_id}.json")

    async def persist_source_dossier(self, plan, dossier):
        return await self._persist(dossier, f"{plan.execution_run_id}/digestion/dossiers/{dossier.source_id}.json")

    async def persist_evidence_matrix(self, plan, matrix):
        return await self._persist(matrix, f"{plan.execution_run_id}/digestion/evidence-matrix.json")

    async def persist_understanding(self, plan, understanding):
        return await self._persist(understanding, f"{plan.execution_run_id}/digestion/understanding.json")

    async def persist_axis_candidate(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/editorial/axis/candidate.json")

    async def persist_axis_review(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/editorial/axis/evidence-review.json")

    async def persist_axis_record(self, run_id, project_id, value, *, approved):
        state = "approved" if approved else "proposed"
        return await self._persist(value, f"{run_id}/editorial/axis/{state}.json")

    async def persist_project_proposal(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/editorial/project/proposal.json")

    async def persist_tip_timing(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/editorial/project/tip-timing.json")

    async def persist_project_record(self, run_id, project_id, value, *, approved):
        state = "approved" if approved else "proposed"
        return await self._persist(value, f"{run_id}/editorial/project/{state}.json")

    async def find_axis_record(self, run_id, project_id, *, approved):
        state = "approved" if approved else "proposed"
        return await self._find(f"{run_id}/editorial/axis/{state}.json", NarrativeAxisRecordV2)

    async def find_project_record(self, run_id, project_id, *, approved):
        state = "approved" if approved else "proposed"
        return await self._find(f"{run_id}/editorial/project/{state}.json", EditorialProjectRecordV2)

    async def _find(self, path, schema):
        uri = f"gs://memory/{path}"
        data = self.objects.get(uri)
        if data is None:
            return None
        pointer = ArtifactPointer(uri=uri, sha256=sha256(data).hexdigest())
        return pointer, schema.model_validate_json(data)

    async def persist_evidence_bundle(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/production/evidence-bundle.json")

    async def persist_research(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/production/research.json")

    async def persist_architecture(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/production/architecture.json")

    async def persist_draft(self, run_id, project_id, value, *, round_index):
        return await self._persist(value, f"{run_id}/production/drafts/{round_index:02d}.json")

    async def persist_review(self, run_id, project_id, value, *, round_index):
        return await self._persist(value, f"{run_id}/production/reviews/{round_index:02d}.json")

    async def persist_final_article(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/production/final-article.json")

    async def persist_production_record(self, run_id, project_id, value):
        return await self._persist(value, f"{run_id}/production/production-record.json")

    async def find_evidence_bundle(self, run_id, project_id):
        return await self._find(f"{run_id}/production/evidence-bundle.json", ProductionEvidenceBundle)

    async def find_final_article(self, run_id, project_id):
        return await self._find(f"{run_id}/production/final-article.json", FinalArticle)

    async def find_production_record(self, run_id, project_id):
        return await self._find(f"{run_id}/production/production-record.json", ProductionRecordV2)


class MemoryRepository:
    def __init__(self):
        self.values = {}

    async def create(self, value):
        key = value.execution_run_id
        existing = self.values.get(key)
        if existing is not None and existing != value:
            raise ValueError("conflict")
        self.values[key] = value
        return value

    async def get(self, key):
        return self.values.get(key)

    async def transition(self, key, change):
        updated = change(self.values[key])
        self.values[key] = updated
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
    axis_statement = (
        "O impacto das redes sociais não é uniforme: depende da vulnerabilidade, "
        "do estágio de desenvolvimento e do modo de uso."
    )

    def __init__(self):
        self.calls = []
        self.prompts = []

    async def generate(self, intent, *, prompt, system_instruction=None, response_schema=None):
        self.calls.append(intent.task_type)
        self.prompts.append((intent.task_type, prompt, system_instruction))
        if response_schema is SourceDossierProposal:
            evidence_match = re.search(
                r"\[EVIDENCE_KEY (E_[0-9a-f]{16})\]", prompt
            ) or re.search(r'"first_evidence_key": "(E_[0-9a-f]{16})"', prompt)
            evidence_value = evidence_match.group(1)
            proposal = SourceDossierProposal(
                executive_synthesis="A fonte descreve uma associação heterogênea.",
                items=[
                    DigestionItemProposal(
                        kind=DigestionItemKind.FINDING,
                        text="A associação varia entre adolescentes.",
                        causal_status=CausalStatus.CORRELATIONAL,
                        evidence_strength=EvidenceStrength.MODERATE,
                        first_evidence_key=evidence_value,
                        last_evidence_key=evidence_value,
                        qualifications=[],
                    ),
                    *[
                        DigestionItemProposal(
                            kind=kind, text=text, causal_status="", evidence_strength="",
                            first_evidence_key="", last_evidence_key="", qualifications=[],
                        )
                        for kind, text in (
                            ("source_kind", "estudo"),
                            ("design", "observacional"),
                            ("population_or_scope", "adolescentes"),
                            ("measures_or_material", "questionários"),
                            ("temporal_scope", "um período"),
                            ("limitation", "O desenho não demonstra causalidade."),
                        )
                    ],
                ],
            )
        elif response_schema is EvidenceMatrixProposal:
            proposal = EvidenceMatrixProposal(
                central_ideas=["O impacto varia conforme contexto e vulnerabilidade."],
                relations_and_tensions=["Associação não equivale a causalidade."],
                source_limits=["O desenho observacional limita inferências causais."],
                dominance_assessment=DominanceAssessment(
                    long_source_dominance_detected=False,
                    explanation="Há uma única fonte neste teste de unidade.",
                ),
                integrated_synthesis="A evidência exige uma leitura contextual e não causal.",
            )
        elif response_schema is NarrativeAxisProposal:
            proposal = NarrativeAxisProposal(statement=self.axis_statement)
        elif response_schema is NarrativeAxisEvidenceReview:
            proposal = NarrativeAxisEvidenceReview(
                candidate_statement=self.axis_statement,
                revision_required=False,
                final_statement=self.axis_statement,
            )
        elif response_schema is EditorialProjectProposal:
            proposal = EditorialProjectProposal(
                working_title="Além do tempo de tela",
                editorial_promise="Explicar por que contexto e vulnerabilidade importam.",
                core_subjects=[
                    EditorialSubjectProposal(
                        title="Efeitos heterogêneos",
                        development="Distinguir padrões de uso e vulnerabilidades.",
                    )
                ],
                llm_subjects=["Como famílias podem conversar sobre o tema"],
                enrichment_opportunities=["Mais evidência experimental"],
            )
        elif response_schema is HefestoTipTimingResult:
            proposal = HefestoTipTimingResult(timings=[HefestoTipTiming(incremental_minutes=3)])
        else:
            raise AssertionError(response_schema)
        return SimpleNamespace(parsed=proposal)


def _request():
    return PlanningRequest(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="O impacto das redes sociais na saúde mental dos adolescentes",
            approach="Neutra",
            duration_minutes=15,
            output_language="pt-BR",
            audience="Leitores não especialistas",
            product="article",
        ),
        sources=[
            NormalizedSource(
                source_id=uuid4(),
                text="Evidence line.",
                language="en",
                title="Study",
            )
        ],
    )


async def _system(gateway=None):
    artifacts = MemoryArtifacts()
    digestion_runs = MemoryRepository()
    editorial_runs = MemoryRepository()
    work = MemoryWork()
    gateway = gateway or ResponsiveGateway()
    executor = BoundedFairExecutor(
        SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5),
        max_concurrency=4,
        per_project_concurrency=4,
    )
    digestion = DigestionOrchestrator(
        planner=SourceDigestionPlanner(),
        calls=DigestionCallFactory(DigestionRuntimeConfig()),
        gateway=gateway,
        executor=executor,
        artifacts=artifacts,
        runs=digestion_runs,
        work=work,
        worker_id="test-worker",
    )
    result = await digestion.start(_request())
    editorial = EditorialV2Orchestrator(
        calls=EditorialV2CallFactory(EditorialV2RuntimeConfig()),
        gateway=gateway,
        digestion_artifacts=artifacts,
        digestion_runs=digestion_runs,
        editorial_artifacts=artifacts,
        editorial_runs=editorial_runs,
        work=work,
        executor=executor,
        worker_id="test-worker",
    )
    return result, editorial, editorial_runs, work, gateway, artifacts


@pytest.mark.asyncio
async def test_direct_gate_approvals_preserve_generated_tips_but_accept_none() -> None:
    digestion, editorial, editorial_runs, work, gateway, artifacts = await _system()
    digestion_call_count = len(gateway.calls)

    axis_result = await editorial.propose_axis(digestion.execution_run.execution_run_id)
    assert axis_result.execution_run.state is EditorialV2State.AXIS_PROPOSED
    assert axis_result.narrative_axis_record.narrative_axis.status is NarrativeAxisStatus.PROPOSED

    project_result = await editorial.approve_axis_and_propose_project(
        digestion.execution_run.execution_run_id
    )
    assert project_result.execution_run.state is EditorialV2State.PROJECT_PROPOSED
    assert project_result.narrative_axis_record.narrative_axis.status is NarrativeAxisStatus.APPROVED
    assert project_result.editorial_project_record.generated_tips[0].incremental_minutes == 3
    assert project_result.editorial_project_record.accepted_tips == []

    approved = await editorial.approve_project_without_tips(digestion.execution_run.execution_run_id)
    record = approved.editorial_project_record
    assert approved.execution_run.state is EditorialV2State.PROJECT_APPROVED
    assert record.editorial_project.status is EditorialProjectStatus.APPROVED
    assert record.accepted_tips == []
    assert record.generated_tips[0].display_text in record.editorial_project.llm_subjects
    assert len(gateway.calls) - digestion_call_count == 4
    assert approved.execution_run.completed_model_calls == 4
    assert approved.execution_run.artifacts.axis_candidate
    assert approved.execution_run.artifacts.axis_evidence_review
    assert approved.execution_run.artifacts.project_proposal
    assert approved.execution_run.artifacts.tip_timing
    assert len(work.values) == digestion.execution_run.completed_model_calls + 4

    object_count = len(artifacts.objects)
    call_count = len(gateway.calls)
    repeated = await editorial.approve_project_without_tips(digestion.execution_run.execution_run_id)
    assert repeated == approved
    assert len(gateway.calls) == call_count
    assert len(artifacts.objects) == object_count
    assert await editorial_runs.get(digestion.execution_run.execution_run_id) == approved.execution_run


@pytest.mark.asyncio
async def test_axis_and_project_prompts_use_matrix_and_explicit_causality_boundaries() -> None:
    digestion, editorial, _, _, gateway, _ = await _system()

    await editorial.approve_axis_and_propose_project(digestion.execution_run.execution_run_id)

    editorial_tasks = gateway.calls[digestion.execution_run.completed_model_calls :]
    assert editorial_tasks == [
        "narrative_axis_proposal_v2",
        "narrative_axis_evidence_review_v2",
        "editorial_project_proposal_v2",
        "hefesto_tip_timing_v2",
    ]
    prompt_by_task = {task: prompt for task, prompt, _ in gateway.prompts}
    assert "cross-source evidence matrix" in prompt_by_task["narrative_axis_proposal_v2"]
    assert "causality assessments" in prompt_by_task["narrative_axis_proposal_v2"]
    assert "do not simply concatenate" in prompt_by_task["editorial_project_proposal_v2"]
    assert "Dicas do Hefesto and are NOT part" in prompt_by_task["editorial_project_proposal_v2"]
    assert "one or two sentences" not in prompt_by_task["narrative_axis_proposal_v2"]


def test_editorial_v2_outputs_have_no_editorial_size_or_quantity_caps() -> None:
    def assert_no_editorial_caps(schema) -> None:
        serialized = str(schema.model_json_schema())
        assert "maxLength" not in serialized
        assert "maxItems" not in serialized

    for schema in (
        NarrativeAxisProposal,
        NarrativeAxisEvidenceReview,
        EditorialProjectProposal,
        NarrativeAxis,
        EditorialProject,
        HefestoTipV2,
        HefestoTipTimingResult,
    ):
        assert_no_editorial_caps(schema)

    long_statement = "Eixo sustentado pela evidência. " * 100
    many_subjects = [
        EditorialSubjectProposal(title=f"Assunto {index}", development="Desenvolvimento editorial.")
        for index in range(30)
    ]
    axis = NarrativeAxisProposal(statement=long_statement)
    project = EditorialProjectProposal(
        working_title="Projeto amplo",
        editorial_promise="Preservar toda distinção substantiva.",
        core_subjects=many_subjects,
        llm_subjects=[f"Dica {index}" for index in range(20)],
        enrichment_opportunities=[f"Oportunidade {index}" for index in range(20)],
    )

    assert axis.statement == long_statement.strip()
    assert len(project.core_subjects) == 30
