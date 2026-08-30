"""Durable, provider-neutral contracts for the Hefesto V2 execution layer."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import Field, computed_field, model_validator

from .contracts import StrictModel


SHA256_PATTERN = r"^[0-9a-f]{64}$"
MILLION = Decimal("1000000")


def text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class LLMCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class TokenBudget(StrictModel):
    """Admission envelope for one call, independent from provider pricing."""

    estimated_input_tokens: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    thinking_budget_tokens: int = Field(ge=0)
    context_window_tokens: int = Field(ge=2)
    input_capacity_weight: Decimal = Field(default=Decimal("1"), gt=0)
    output_capacity_weight: Decimal = Field(default=Decimal("1"), gt=0)
    reasoning_capacity_weight: Decimal = Field(default=Decimal("1"), gt=0)

    @model_validator(mode="after")
    def _fits_model_envelope(self) -> "TokenBudget":
        if self.estimated_input_tokens > self.max_input_tokens:
            raise ValueError("estimated input exceeds its explicit maximum")
        if self.thinking_budget_tokens > self.max_output_tokens:
            raise ValueError("thinking budget is part of, and cannot exceed, max output")
        if self.max_input_tokens + self.max_output_tokens > self.context_window_tokens:
            raise ValueError("input and output budgets exceed the model context window")
        return self

    @computed_field
    @property
    def reserved_capacity_tokens(self) -> int:
        visible_output = self.max_output_tokens - self.thinking_budget_tokens
        weighted = (
            Decimal(self.estimated_input_tokens) * self.input_capacity_weight
            + Decimal(visible_output) * self.output_capacity_weight
            + Decimal(self.thinking_budget_tokens) * self.reasoning_capacity_weight
        )
        return int(weighted.quantize(Decimal("1"), rounding=ROUND_CEILING))


class LLMCallIntent(StrictModel):
    """Immutable description of a model call before it enters the gateway."""

    call_id: UUID = Field(default_factory=uuid4)
    execution_run_id: UUID
    project_id: UUID
    work_unit_id: str = Field(min_length=1, max_length=200)
    task_type: str = Field(min_length=1, max_length=100)
    prompt_version: str = Field(min_length=1, max_length=200)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    system_instruction_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    response_schema_name: str | None = Field(default=None, min_length=1, max_length=300)
    model: str = Field(min_length=1)
    location: str = Field(default="global", min_length=1)
    temperature: float = Field(ge=0, le=2)
    budget: TokenBudget
    max_attempts: int = Field(default=3, ge=1, le=8)
    priority: int = Field(default=100, ge=0, le=1000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LLMUsage(StrictModel):
    provider_reported: bool = True
    prompt_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    tool_prompt_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _cached_is_part_of_prompt(self) -> "LLMUsage":
        if self.cached_tokens > self.prompt_tokens:
            raise ValueError("cached tokens cannot exceed total prompt tokens")
        return self


class TokenPriceCard(StrictModel):
    """Versioned public rate card; billing exports reconcile discounts later."""

    model: str = Field(min_length=1)
    effective_at: datetime
    source_url: str = Field(min_length=1)
    input_per_million_usd: Decimal = Field(ge=0)
    cached_input_per_million_usd: Decimal = Field(ge=0)
    output_per_million_usd: Decimal = Field(ge=0)
    reasoning_per_million_usd: Decimal = Field(ge=0)

    def rate(self, usage: LLMUsage) -> Decimal:
        if not usage.provider_reported:
            raise ValueError("cannot rate a call without provider-reported usage")
        uncached = max(usage.prompt_tokens - usage.cached_tokens, 0)
        total = (
            Decimal(uncached) * self.input_per_million_usd
            + Decimal(usage.cached_tokens) * self.cached_input_per_million_usd
            + Decimal(usage.output_tokens) * self.output_per_million_usd
            + Decimal(usage.reasoning_tokens) * self.reasoning_per_million_usd
        ) / MILLION
        return total.quantize(Decimal("0.000000001"))


class LLMAttemptRecord(StrictModel):
    """One append-only provider attempt, including ambiguous timeouts."""

    attempt_id: UUID = Field(default_factory=uuid4)
    call_id: UUID
    execution_run_id: UUID
    project_id: UUID
    work_unit_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    location: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    status: LLMCallStatus
    queued_at: datetime
    started_at: datetime
    finished_at: datetime
    queue_wait_ms: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=1)
    reserved_capacity_tokens: int = Field(ge=1)
    usage: LLMUsage | None = None
    provider_response_id: str | None = None
    provider_model_version: str | None = None
    retryable: bool = False
    error_class: str | None = None
    error_message: str | None = None
    rate_card_effective_at: datetime | None = None
    rated_cost_usd: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _coherent_attempt(self) -> "LLMAttemptRecord":
        if self.finished_at < self.started_at or self.started_at < self.queued_at:
            raise ValueError("attempt timestamps must be monotonic")
        if self.status is LLMCallStatus.SUCCEEDED:
            if self.usage is None:
                raise ValueError("successful attempt requires provider usage")
            if self.error_class or self.error_message:
                raise ValueError("successful attempt cannot contain an error")
        elif not self.error_class:
            raise ValueError("failed or unknown attempt requires an error class")
        if (self.rated_cost_usd is None) != (self.rate_card_effective_at is None):
            raise ValueError("rated cost and price-card timestamp must be recorded together")
        return self
