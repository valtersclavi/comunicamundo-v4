"""Budgeted production calls using the shared Hefesto V2 gateway."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Generic, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .contracts import EditorialProject, EditorialReview, NarrativePlan
from .digestion_contracts import canonical_sha256
from .execution_contracts import LLMCallIntent, TokenBudget, text_sha256, work_unit_key
from .production_v2_contracts import (
    ARCHITECTURE_V2_PROMPT_VERSION,
    CRITIC_V2_PROMPT_VERSION,
    DRAFT_V2_PROMPT_VERSION,
    EDIT_V2_PROMPT_VERSION,
    PRODUCTION_V2_ENGINE_VERSION,
    RESEARCH_V2_PROMPT_VERSION,
    ArticleDraftV2,
    ProductionEvidenceBundle,
    SubjectResearchBundleProposal,
)
from .production_v2_prompts import (
    PRODUCTION_V2_SYSTEM_INSTRUCTION,
    architecture_prompt,
    critic_prompt,
    draft_prompt,
    edit_prompt,
    research_prompt,
)
from .source_digestion import estimate_tokens


OutputT = TypeVar("OutputT", bound=BaseModel)


class GatewayResult(Protocol):
    parsed: BaseModel | None


class ModelGateway(Protocol):
    async def generate(
        self,
        intent: LLMCallIntent,
        *,
        prompt: str,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> GatewayResult: ...


@dataclass(frozen=True, slots=True)
class ProductionV2RuntimeConfig:
    model: str = "gemini-2.5-flash"
    location: str = "global"
    temperature: float = 0.2
    context_window_tokens: int = 1_048_576
    max_input_tokens: int = 160_000
    research_max_output_tokens: int = 8_000
    research_thinking_tokens: int = 2_000
    architecture_max_output_tokens: int = 4_000
    architecture_thinking_tokens: int = 1_000
    draft_max_output_tokens: int = 20_000
    draft_thinking_tokens: int = 2_000
    critic_max_output_tokens: int = 4_000
    critic_thinking_tokens: int = 1_000
    edit_max_output_tokens: int = 20_000
    edit_thinking_tokens: int = 2_000
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.location.strip():
            raise ValueError("production runtime requires model and location")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must fit the provider contract")
        if self.max_input_tokens <= 0 or self.context_window_tokens <= 0:
            raise ValueError("production input and context budgets must be positive")
        for output, thinking in (
            (self.research_max_output_tokens, self.research_thinking_tokens),
            (self.architecture_max_output_tokens, self.architecture_thinking_tokens),
            (self.draft_max_output_tokens, self.draft_thinking_tokens),
            (self.critic_max_output_tokens, self.critic_thinking_tokens),
            (self.edit_max_output_tokens, self.edit_thinking_tokens),
        ):
            if output <= 0 or thinking < 0 or thinking > output:
                raise ValueError("production task requires a coherent output budget")
            if self.max_input_tokens + output > self.context_window_tokens:
                raise ValueError("production task budget exceeds the context window")
        if not 1 <= self.max_attempts <= 8:
            raise ValueError("attempt budget must be between one and eight")

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class PreparedProductionCall(Generic[OutputT]):
    intent: LLMCallIntent
    prompt: str
    response_schema: type[OutputT]
    output_schema: type[OutputT]
    input_sha256s: tuple[str, ...]
    finalize: Callable[[OutputT], OutputT]

    @property
    def work_unit_id(self) -> str:
        return self.intent.work_unit_id

    @property
    def reserved_capacity_tokens(self) -> int:
        return self.intent.budget.reserved_capacity_tokens

    async def invoke(self, gateway: ModelGateway) -> OutputT:
        result = await gateway.generate(
            self.intent,
            prompt=self.prompt,
            system_instruction=PRODUCTION_V2_SYSTEM_INSTRUCTION,
            response_schema=self.response_schema,
        )
        if not isinstance(result.parsed, self.response_schema):
            raise TypeError(f"gateway did not return {self.response_schema.__name__}")
        return self.finalize(result.parsed)


class ProductionV2CallFactory:
    def __init__(self, config: ProductionV2RuntimeConfig) -> None:
        self.config = config

    def research(
        self,
        *,
        execution_run_id: UUID,
        project: EditorialProject,
        evidence: ProductionEvidenceBundle,
        approved_project_sha256: str,
    ) -> PreparedProductionCall[SubjectResearchBundleProposal]:
        prompt = research_prompt(project=project, evidence=evidence)
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            task_type="production_research_bundle_v2",
            prompt_version=RESEARCH_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=SubjectResearchBundleProposal,
            input_sha256s=[approved_project_sha256, evidence.content_sha256],
            max_output_tokens=self.config.research_max_output_tokens,
            thinking_tokens=self.config.research_thinking_tokens,
            finalize=lambda value: _validate_research(value, project),
        )

    def architecture(
        self,
        *,
        execution_run_id: UUID,
        project: EditorialProject,
        research: SubjectResearchBundleProposal,
        evidence: ProductionEvidenceBundle,
    ) -> PreparedProductionCall[NarrativePlan]:
        prompt = architecture_prompt(project=project, research=research, evidence=evidence)
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            task_type="production_architecture_v2",
            prompt_version=ARCHITECTURE_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=NarrativePlan,
            input_sha256s=[
                evidence.content_sha256,
                _hash(research),
                _hash(project),
            ],
            max_output_tokens=self.config.architecture_max_output_tokens,
            thinking_tokens=self.config.architecture_thinking_tokens,
            finalize=lambda value: _validate_architecture(value, project),
        )

    def draft(
        self,
        *,
        execution_run_id: UUID,
        project: EditorialProject,
        architecture: NarrativePlan,
        research: SubjectResearchBundleProposal,
        evidence: ProductionEvidenceBundle,
    ) -> PreparedProductionCall[ArticleDraftV2]:
        prompt = draft_prompt(
            project=project,
            architecture=architecture,
            research=research,
            evidence=evidence,
        )
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            task_type="production_draft_v2",
            prompt_version=DRAFT_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=ArticleDraftV2,
            input_sha256s=[evidence.content_sha256, _hash(research), _hash(architecture), _hash(project)],
            max_output_tokens=self.config.draft_max_output_tokens,
            thinking_tokens=self.config.draft_thinking_tokens,
        )

    def critic(
        self,
        *,
        execution_run_id: UUID,
        project: EditorialProject,
        draft: ArticleDraftV2,
        research: SubjectResearchBundleProposal,
        evidence: ProductionEvidenceBundle,
        round_index: int,
    ) -> PreparedProductionCall[EditorialReview]:
        prompt = critic_prompt(
            project=project,
            draft=draft,
            research=research,
            evidence=evidence,
        )
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            task_type=f"production_critic_v2_round_{round_index}",
            prompt_version=CRITIC_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=EditorialReview,
            input_sha256s=[evidence.content_sha256, _hash(research), draft.content_sha256, _hash(project)],
            max_output_tokens=self.config.critic_max_output_tokens,
            thinking_tokens=self.config.critic_thinking_tokens,
        )

    def edit(
        self,
        *,
        execution_run_id: UUID,
        project: EditorialProject,
        draft: ArticleDraftV2,
        review: EditorialReview,
        research: SubjectResearchBundleProposal,
        evidence: ProductionEvidenceBundle,
        round_index: int,
    ) -> PreparedProductionCall[ArticleDraftV2]:
        prompt = edit_prompt(
            project=project,
            draft=draft,
            review=review,
            research=research,
            evidence=evidence,
        )
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            task_type=f"production_edit_v2_round_{round_index}",
            prompt_version=EDIT_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=ArticleDraftV2,
            input_sha256s=[
                evidence.content_sha256,
                _hash(research),
                draft.content_sha256,
                _hash(review),
                _hash(project),
            ],
            max_output_tokens=self.config.edit_max_output_tokens,
            thinking_tokens=self.config.edit_thinking_tokens,
        )

    def _call(
        self,
        *,
        execution_run_id: UUID,
        project_id: UUID,
        task_type: str,
        prompt_version: str,
        prompt: str,
        response_schema: type[OutputT],
        input_sha256s: list[str],
        max_output_tokens: int,
        thinking_tokens: int,
        finalize: Callable[[OutputT], OutputT] | None = None,
    ) -> PreparedProductionCall[OutputT]:
        estimated_input = estimate_tokens(prompt) + estimate_tokens(PRODUCTION_V2_SYSTEM_INSTRUCTION)
        budget = TokenBudget(
            estimated_input_tokens=estimated_input,
            max_input_tokens=self.config.max_input_tokens,
            max_output_tokens=max_output_tokens,
            thinking_budget_tokens=thinking_tokens,
            context_window_tokens=self.config.context_window_tokens,
        )
        unit_id = work_unit_key(
            engine_version=PRODUCTION_V2_ENGINE_VERSION,
            unit_type=task_type,
            input_sha256s=input_sha256s,
            prompt_version=prompt_version,
            model=self.config.model,
            model_config_sha256=self.config.config_sha256,
            cache_scope=f"run:{execution_run_id}",
        )
        intent = LLMCallIntent(
            execution_run_id=execution_run_id,
            project_id=project_id,
            work_unit_id=unit_id,
            task_type=task_type,
            prompt_version=prompt_version,
            prompt_sha256=text_sha256(prompt),
            system_instruction_sha256=text_sha256(PRODUCTION_V2_SYSTEM_INSTRUCTION),
            response_schema_name=f"{response_schema.__module__}.{response_schema.__qualname__}",
            model=self.config.model,
            location=self.config.location,
            temperature=self.config.temperature,
            budget=budget,
            max_attempts=self.config.max_attempts,
        )
        return PreparedProductionCall(
            intent=intent,
            prompt=prompt,
            response_schema=response_schema,
            output_schema=response_schema,
            input_sha256s=tuple(input_sha256s),
            finalize=finalize or _identity,
        )


def _hash(value: BaseModel) -> str:
    return canonical_sha256(value.model_dump(mode="json"))


def _identity(value: OutputT) -> OutputT:
    return value


def _validate_research(
    value: SubjectResearchBundleProposal,
    project: EditorialProject,
) -> SubjectResearchBundleProposal:
    expected = [subject.subject_id for subject in project.core_subjects]
    actual = [subject.subject_id for subject in value.subjects]
    if actual != expected:
        raise ValueError("research bundle must cover every core subject exactly once in Project order")
    return value


def _validate_architecture(value: NarrativePlan, project: EditorialProject) -> NarrativePlan:
    expected = [subject.subject_id for subject in project.core_subjects]
    actual = [beat.subject_id for beat in value.beats]
    if actual != expected:
        raise ValueError("narrative architecture must cover every core subject exactly once in Project order")
    return value
