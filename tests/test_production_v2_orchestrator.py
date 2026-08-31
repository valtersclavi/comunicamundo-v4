from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import UUID

import pytest

from comunicamundo_v4.hefesto.contracts import (
    EditorialReview,
    NarrativeBeat,
    NarrativePlan,
    SubjectResearch,
)
from comunicamundo_v4.hefesto.execution_scheduler import BoundedFairExecutor, SmoothedTokenBucket
from comunicamundo_v4.hefesto.execution_contracts import LLMCallStatus
from comunicamundo_v4.hefesto.llm_gateway import LLMCallError
from comunicamundo_v4.hefesto.production_evidence import ProductionEvidenceBuilder
from comunicamundo_v4.hefesto.production_v2_contracts import (
    ArticleDraftV2,
    ProductionV2State,
    SubjectResearchBundleProposal,
)
from comunicamundo_v4.hefesto.production_v2_orchestrator import ProductionV2Orchestrator
from comunicamundo_v4.hefesto.production_v2_runtime import (
    ProductionV2CallFactory,
    ProductionV2RuntimeConfig,
)
from test_editorial_v2_orchestrator import MemoryRepository, ResponsiveGateway, _system


class ProductionGateway(ResponsiveGateway):
    def __init__(self, *, ready_after: int = 2, fail_once_task: str | None = None):
        super().__init__()
        self.critic_count = 0
        self.ready_after = ready_after
        self.fail_once_task = fail_once_task
        self.failed_once = False

    async def generate(self, intent, *, prompt, system_instruction=None, response_schema=None):
        if response_schema not in {
            SubjectResearchBundleProposal,
            NarrativePlan,
            ArticleDraftV2,
            EditorialReview,
        }:
            return await super().generate(
                intent,
                prompt=prompt,
                system_instruction=system_instruction,
                response_schema=response_schema,
            )
        if intent.task_type == self.fail_once_task and not self.failed_once:
            self.failed_once = True
            self.calls.append(intent.task_type)
            self.prompts.append((intent.task_type, prompt, system_instruction))
            raise LLMCallError(
                "temporary provider failure",
                (SimpleNamespace(status=LLMCallStatus.FAILED, retryable=True),),
            )
        self.calls.append(intent.task_type)
        self.prompts.append((intent.task_type, prompt, system_instruction))
        subject_ids = list(
            dict.fromkeys(
                UUID(value)
                for value in re.findall(
                    r'"subject_id"\s*:\s*"([0-9a-f-]{36})"',
                    prompt,
                )
            )
        )
        if response_schema is SubjectResearchBundleProposal:
            proposal = SubjectResearchBundleProposal(
                subjects=[
                    SubjectResearch(
                        subject_id=subject_id,
                        synthesis="A evidência apoia uma interpretação contextual e não causal.",
                    )
                    for subject_id in subject_ids
                ]
            )
        elif response_schema is NarrativePlan:
            proposal = NarrativePlan(
                title="Além do tempo de tela",
                opening_move="Começar pela diferença entre adolescentes.",
                beats=[
                    NarrativeBeat(
                        subject_id=subject_id,
                        narrative_role="Explicar a heterogeneidade.",
                        transition="Passar do padrão geral aos limites da evidência.",
                    )
                    for subject_id in subject_ids
                ],
                closing_move="Terminar com uma leitura contextual.",
            )
        elif response_schema is ArticleDraftV2:
            suffix = " revisado" if "Editorial review:" in prompt else ""
            proposal = ArticleDraftV2(
                body_markdown=(
                    "# Além do tempo de tela\n\n"
                    "Os estudos descrevem associações heterogêneas, não uma causa uniforme."
                    f"{suffix}"
                )
            )
        elif response_schema is EditorialReview:
            self.critic_count += 1
            ready = self.critic_count >= self.ready_after
            proposal = EditorialReview(
                ready_to_publish=ready,
                quality_score=92 if ready else 72,
                strengths=["Distingue associação de causalidade."],
                problems=[] if ready else ["A síntese precisa explicitar melhor os limites."],
                revision_instructions=[] if ready else ["Tornar o limite metodológico mais claro."],
            )
        else:
            raise AssertionError(response_schema)
        return SimpleNamespace(parsed=proposal)


@pytest.mark.asyncio
async def test_full_v2_production_is_bounded_revisable_and_replay_safe() -> None:
    gateway = ProductionGateway()
    digestion, editorial, editorial_runs, work, gateway, artifacts = await _system(gateway)
    approved = await editorial.approve_project_without_tips(
        digestion.execution_run.execution_run_id
    )
    before_production = len(gateway.calls)
    production_runs = MemoryRepository()
    executor = BoundedFairExecutor(
        SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5),
        max_concurrency=4,
        per_project_concurrency=4,
    )
    production = ProductionV2Orchestrator(
        evidence_builder=ProductionEvidenceBuilder(),
        calls=ProductionV2CallFactory(ProductionV2RuntimeConfig()),
        gateway=gateway,
        executor=executor,
        digestion_artifacts=artifacts,
        digestion_runs=editorial._digestion_runs,
        editorial_artifacts=artifacts,
        editorial_runs=editorial_runs,
        production_artifacts=artifacts,
        production_runs=production_runs,
        work=work,
        worker_id="test-worker",
    )

    result = await production.run(digestion.execution_run.execution_run_id)

    assert result.execution_run.state is ProductionV2State.ARTICLE_READY
    assert result.execution_run.revision_rounds == 1
    assert result.execution_run.completed_model_calls == 6
    assert result.production_record.final_article.revision_rounds == 1
    assert result.production_record.final_article.quality_score == 92
    assert len(result.production_record.reviews) == 2
    assert result.production_record.final_article.body_markdown.endswith("revisado")
    assert len(gateway.calls) - before_production == 6
    assert gateway.calls[-6:] == [
        "production_research_bundle_v2",
        "production_architecture_v2",
        "production_draft_v2",
        "production_critic_v2_round_0",
        "production_edit_v2_round_1",
        "production_critic_v2_round_1",
    ]
    production_prompts = "\n".join(prompt for task, prompt, _ in gateway.prompts if task.startswith("production_"))
    assert "Dicas do Hefesto or enrichment opportunities" in production_prompts
    assert approved.editorial_project_record.generated_tips[0].text not in result.production_record.final_article.body_markdown

    call_count = len(gateway.calls)
    object_count = len(artifacts.objects)
    replay = await production.run(digestion.execution_run.execution_run_id)
    assert replay == result
    assert len(gateway.calls) == call_count
    assert len(artifacts.objects) == object_count


@pytest.mark.asyncio
async def test_first_review_can_finish_production_in_four_calls() -> None:
    gateway = ProductionGateway(ready_after=1)
    digestion, editorial, editorial_runs, work, gateway, artifacts = await _system(gateway)
    await editorial.approve_project_without_tips(digestion.execution_run.execution_run_id)
    production = ProductionV2Orchestrator(
        evidence_builder=ProductionEvidenceBuilder(),
        calls=ProductionV2CallFactory(ProductionV2RuntimeConfig()),
        gateway=gateway,
        executor=BoundedFairExecutor(
            SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5),
            max_concurrency=4,
            per_project_concurrency=4,
        ),
        digestion_artifacts=artifacts,
        digestion_runs=editorial._digestion_runs,
        editorial_artifacts=artifacts,
        editorial_runs=editorial_runs,
        production_artifacts=artifacts,
        production_runs=MemoryRepository(),
        work=work,
        worker_id="test-worker",
    )
    before = len(gateway.calls)

    result = await production.run(digestion.execution_run.execution_run_id)

    assert result.execution_run.completed_model_calls == 4
    assert result.execution_run.revision_rounds == 0
    assert len(gateway.calls) - before == 4


@pytest.mark.asyncio
async def test_production_has_a_hard_ceiling_of_two_revisions_and_eight_calls() -> None:
    gateway = ProductionGateway(ready_after=99)
    digestion, editorial, editorial_runs, work, gateway, artifacts = await _system(gateway)
    await editorial.approve_project_without_tips(digestion.execution_run.execution_run_id)
    production = ProductionV2Orchestrator(
        evidence_builder=ProductionEvidenceBuilder(),
        calls=ProductionV2CallFactory(ProductionV2RuntimeConfig()),
        gateway=gateway,
        executor=BoundedFairExecutor(
            SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5),
            max_concurrency=4,
            per_project_concurrency=4,
        ),
        digestion_artifacts=artifacts,
        digestion_runs=editorial._digestion_runs,
        editorial_artifacts=artifacts,
        editorial_runs=editorial_runs,
        production_artifacts=artifacts,
        production_runs=MemoryRepository(),
        work=work,
        worker_id="test-worker",
    )
    before = len(gateway.calls)

    result = await production.run(digestion.execution_run.execution_run_id)

    assert result.execution_run.completed_model_calls == 8
    assert result.execution_run.revision_rounds == 2
    assert len(result.production_record.reviews) == 3
    assert result.production_record.final_article.quality_score == 72
    assert len(gateway.calls) - before == 8


@pytest.mark.asyncio
async def test_retryable_mid_production_failure_resumes_without_repeating_completed_calls() -> None:
    gateway = ProductionGateway(fail_once_task="production_critic_v2_round_1")
    digestion, editorial, editorial_runs, work, gateway, artifacts = await _system(gateway)
    await editorial.approve_project_without_tips(digestion.execution_run.execution_run_id)
    production_runs = MemoryRepository()
    production = ProductionV2Orchestrator(
        evidence_builder=ProductionEvidenceBuilder(),
        calls=ProductionV2CallFactory(ProductionV2RuntimeConfig()),
        gateway=gateway,
        executor=BoundedFairExecutor(
            SmoothedTokenBucket(tokens_per_minute=100_000_000, burst_window_seconds=5),
            max_concurrency=4,
            per_project_concurrency=4,
        ),
        digestion_artifacts=artifacts,
        digestion_runs=editorial._digestion_runs,
        editorial_artifacts=artifacts,
        editorial_runs=editorial_runs,
        production_artifacts=artifacts,
        production_runs=production_runs,
        work=work,
        worker_id="test-worker",
    )

    with pytest.raises(LLMCallError, match="temporary"):
        await production.run(digestion.execution_run.execution_run_id)

    failed = await production_runs.get(digestion.execution_run.execution_run_id)
    assert failed.state is ProductionV2State.FAILED
    assert failed.revision_rounds == 1

    result = await production.run(digestion.execution_run.execution_run_id)

    assert result.execution_run.state is ProductionV2State.ARTICLE_READY
    assert result.execution_run.completed_model_calls == 6
    production_calls = [item for item in gateway.calls if item.startswith("production_")]
    assert production_calls.count("production_research_bundle_v2") == 1
    assert production_calls.count("production_architecture_v2") == 1
    assert production_calls.count("production_draft_v2") == 1
    assert production_calls.count("production_edit_v2_round_1") == 1
    assert production_calls.count("production_critic_v2_round_1") == 2
