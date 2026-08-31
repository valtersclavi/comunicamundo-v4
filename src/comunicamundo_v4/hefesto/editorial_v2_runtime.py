"""Budgeted V2 calls for Narrative Axis and Editorial Project gates."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from .contracts import (
    EditorialBrief,
    EditorialProjectProposal,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
)
from .digestion_contracts import EditorialUnderstandingV2, EvidenceMatrix, canonical_sha256
from .editorial_v2_contracts import (
    AXIS_V2_PROMPT_VERSION,
    EDITORIAL_V2_ENGINE_VERSION,
    PROJECT_V2_PROMPT_VERSION,
    TIP_TIMING_V2_PROMPT_VERSION,
)
from .editorial_v2_prompts import (
    EDITORIAL_V2_SYSTEM_INSTRUCTION,
    axis_evidence_review_prompt,
    axis_proposal_prompt,
    editorial_project_prompt,
    tip_timing_prompt,
)
from .execution_contracts import LLMCallIntent, TokenBudget, text_sha256, work_unit_key
from .hefesto_tips import HefestoTipTimingResult
from .planning import fingerprint_brief
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
class EditorialV2RuntimeConfig:
    model: str = "gemini-2.5-flash"
    location: str = "global"
    temperature: float = 0.2
    context_window_tokens: int = 1_048_576
    max_input_tokens: int = 120_000
    axis_max_output_tokens: int = 2_000
    axis_thinking_tokens: int = 500
    review_max_output_tokens: int = 2_500
    review_thinking_tokens: int = 750
    project_max_output_tokens: int = 6_000
    project_thinking_tokens: int = 1_500
    tip_max_output_tokens: int = 2_000
    tip_thinking_tokens: int = 500
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.location.strip():
            raise ValueError("editorial runtime requires a model and location")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must fit the provider contract")
        if self.max_input_tokens <= 0 or self.context_window_tokens <= 0:
            raise ValueError("input and context budgets must be positive")
        for output, thinking in (
            (self.axis_max_output_tokens, self.axis_thinking_tokens),
            (self.review_max_output_tokens, self.review_thinking_tokens),
            (self.project_max_output_tokens, self.project_thinking_tokens),
            (self.tip_max_output_tokens, self.tip_thinking_tokens),
        ):
            if output <= 0 or thinking < 0 or thinking > output:
                raise ValueError("each editorial task requires a coherent output budget")
            if self.max_input_tokens + output > self.context_window_tokens:
                raise ValueError("editorial task budget exceeds the model context window")
        if not 1 <= self.max_attempts <= 8:
            raise ValueError("attempt budget must be between one and eight")

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class PreparedEditorialCall(Generic[OutputT]):
    intent: LLMCallIntent
    prompt: str
    response_schema: type[OutputT]
    output_schema: type[OutputT]
    input_sha256s: tuple[str, ...]

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
            system_instruction=EDITORIAL_V2_SYSTEM_INSTRUCTION,
            response_schema=self.response_schema,
        )
        if not isinstance(result.parsed, self.response_schema):
            raise TypeError(f"gateway did not return {self.response_schema.__name__}")
        return result.parsed


class EditorialV2CallFactory:
    def __init__(self, config: EditorialV2RuntimeConfig) -> None:
        self.config = config

    def propose_axis(
        self,
        *,
        execution_run_id,
        project_id,
        brief: EditorialBrief,
        understanding: EditorialUnderstandingV2,
        matrix: EvidenceMatrix,
        understanding_sha256: str,
        matrix_sha256: str,
    ) -> PreparedEditorialCall[NarrativeAxisProposal]:
        prompt = axis_proposal_prompt(brief=brief, understanding=understanding, matrix=matrix)
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project_id,
            task_type="narrative_axis_proposal_v2",
            prompt_version=AXIS_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=NarrativeAxisProposal,
            input_sha256s=[understanding_sha256, matrix_sha256, fingerprint_brief(brief)],
            max_output_tokens=self.config.axis_max_output_tokens,
            thinking_tokens=self.config.axis_thinking_tokens,
        )

    def review_axis(
        self,
        *,
        execution_run_id,
        project_id,
        candidate: NarrativeAxisProposal,
        understanding: EditorialUnderstandingV2,
        matrix: EvidenceMatrix,
        understanding_sha256: str,
        matrix_sha256: str,
    ) -> PreparedEditorialCall[NarrativeAxisEvidenceReview]:
        prompt = axis_evidence_review_prompt(
            candidate=candidate.statement,
            understanding=understanding,
            matrix=matrix,
        )
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project_id,
            task_type="narrative_axis_evidence_review_v2",
            prompt_version=AXIS_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=NarrativeAxisEvidenceReview,
            input_sha256s=[
                understanding_sha256,
                matrix_sha256,
                canonical_sha256(candidate.model_dump(mode="json")),
            ],
            max_output_tokens=self.config.review_max_output_tokens,
            thinking_tokens=self.config.review_thinking_tokens,
        )

    def propose_project(
        self,
        *,
        execution_run_id,
        project_id,
        axis: str,
        brief: EditorialBrief,
        understanding: EditorialUnderstandingV2,
        matrix: EvidenceMatrix,
        understanding_sha256: str,
        matrix_sha256: str,
    ) -> PreparedEditorialCall[EditorialProjectProposal]:
        prompt = editorial_project_prompt(
            axis=axis,
            brief=brief,
            understanding=understanding,
            matrix=matrix,
        )
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project_id,
            task_type="editorial_project_proposal_v2",
            prompt_version=PROJECT_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=EditorialProjectProposal,
            input_sha256s=[
                understanding_sha256,
                matrix_sha256,
                text_sha256(axis),
                fingerprint_brief(brief),
            ],
            max_output_tokens=self.config.project_max_output_tokens,
            thinking_tokens=self.config.project_thinking_tokens,
        )

    def time_tips(
        self,
        *,
        execution_run_id,
        project_id,
        brief: EditorialBrief,
        proposal: EditorialProjectProposal,
    ) -> PreparedEditorialCall[HefestoTipTimingResult]:
        prompt = tip_timing_prompt(brief=brief, proposal=proposal)
        return self._call(
            execution_run_id=execution_run_id,
            project_id=project_id,
            task_type="hefesto_tip_timing_v2",
            prompt_version=TIP_TIMING_V2_PROMPT_VERSION,
            prompt=prompt,
            response_schema=HefestoTipTimingResult,
            input_sha256s=[
                canonical_sha256(proposal.model_dump(mode="json")),
                fingerprint_brief(brief),
            ],
            max_output_tokens=self.config.tip_max_output_tokens,
            thinking_tokens=self.config.tip_thinking_tokens,
        )

    def _call(
        self,
        *,
        execution_run_id,
        project_id,
        task_type: str,
        prompt_version: str,
        prompt: str,
        response_schema: type[OutputT],
        input_sha256s: list[str],
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> PreparedEditorialCall[OutputT]:
        estimated_input = estimate_tokens(prompt) + estimate_tokens(EDITORIAL_V2_SYSTEM_INSTRUCTION)
        budget = TokenBudget(
            estimated_input_tokens=estimated_input,
            max_input_tokens=self.config.max_input_tokens,
            max_output_tokens=max_output_tokens,
            thinking_budget_tokens=thinking_tokens,
            context_window_tokens=self.config.context_window_tokens,
        )
        unit_id = work_unit_key(
            engine_version=EDITORIAL_V2_ENGINE_VERSION,
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
            system_instruction_sha256=text_sha256(EDITORIAL_V2_SYSTEM_INSTRUCTION),
            response_schema_name=f"{response_schema.__module__}.{response_schema.__qualname__}",
            model=self.config.model,
            location=self.config.location,
            temperature=self.config.temperature,
            budget=budget,
            max_attempts=self.config.max_attempts,
        )
        return PreparedEditorialCall(
            intent=intent,
            prompt=prompt,
            response_schema=response_schema,
            output_schema=response_schema,
            input_sha256s=tuple(input_sha256s),
        )
