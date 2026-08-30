"""Durable, provider-neutral contracts for the Hefesto V2 execution layer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from hashlib import sha256
import json
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field, computed_field, model_validator

from .contracts import StrictModel


SHA256_PATTERN = r"^[0-9a-f]{64}$"
MILLION = Decimal("1000000")
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GcsUri = Annotated[str, Field(pattern=r"^gs://")]


def text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class LLMCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class WorkUnitStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    RETRYABLE = "retryable"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def work_unit_key(
    *,
    engine_version: str,
    unit_type: str,
    input_sha256s: list[str],
    prompt_version: str,
    model: str,
    model_config_sha256: str,
    cache_scope: str,
) -> str:
    canonical = json.dumps(
        {
            "cache_scope": cache_scope,
            "engine_version": engine_version,
            "input_sha256s": input_sha256s,
            "model": model,
            "model_config_sha256": model_config_sha256,
            "prompt_version": prompt_version,
            "unit_type": unit_type,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return text_sha256(canonical)


class WorkUnit(StrictModel):
    """Durable V2 state machine; large inputs and outputs remain in GCS."""

    work_unit_id: str = Field(pattern=SHA256_PATTERN)
    execution_run_id: UUID
    project_id: UUID
    engine_version: str = Field(min_length=1)
    unit_type: str = Field(min_length=1)
    dependencies: list[Sha256] = Field(default_factory=list)
    input_uris: list[GcsUri] = Field(default_factory=list)
    input_sha256s: list[Sha256] = Field(default_factory=list)
    prompt_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_config_sha256: str = Field(pattern=SHA256_PATTERN)
    cache_scope: str = Field(min_length=1)
    reserved_capacity_tokens: int = Field(ge=1)
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=8)
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_expires_at: datetime | None = None
    output_uri: str | None = Field(default=None, pattern=r"^gs://")
    output_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _coherent_state(self) -> "WorkUnit":
        has_lease = self.lease_owner is not None and self.lease_expires_at is not None
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("lease owner and expiry must be set together")
        if self.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING} and not has_lease:
            raise ValueError("leased or running work requires a lease")
        if self.status not in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING} and has_lease:
            raise ValueError("terminal or waiting work cannot retain a lease")
        if (self.output_uri is None) != (self.output_sha256 is None):
            raise ValueError("output URI and hash must be set together")
        if self.status is WorkUnitStatus.SUCCEEDED and self.output_uri is None:
            raise ValueError("succeeded work requires an immutable output")
        if self.status is not WorkUnitStatus.SUCCEEDED and self.output_uri is not None:
            raise ValueError("only succeeded work can publish an output")
        if self.status in {WorkUnitStatus.RETRYABLE, WorkUnitStatus.FAILED} and not self.error_class:
            raise ValueError("failed work requires an error class")
        if self.status not in {WorkUnitStatus.RETRYABLE, WorkUnitStatus.FAILED} and (
            self.error_class or self.error_message
        ):
            raise ValueError("non-failed work cannot retain an error")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt count exceeds configured maximum")
        return self

    def lease(self, *, owner: str, now: datetime, ttl: timedelta) -> "WorkUnit":
        if not owner.strip() or ttl.total_seconds() <= 0:
            raise ValueError("lease requires an owner and positive TTL")
        if self.status in {WorkUnitStatus.SUCCEEDED, WorkUnitStatus.FAILED}:
            raise ValueError("terminal work cannot be leased")
        active_lease = self.lease_expires_at is not None and self.lease_expires_at > now
        if self.status in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING} and active_lease:
            raise ValueError("work unit already has an active lease")
        next_attempt = self.attempt_count + 1
        if next_attempt > self.max_attempts:
            raise ValueError("work unit exhausted its attempts")
        return self._transition(
            {
                "status": WorkUnitStatus.LEASED,
                "attempt_count": next_attempt,
                "lease_owner": owner,
                "lease_expires_at": now + ttl,
                "error_class": None,
                "error_message": None,
                "updated_at": now,
            }
        )

    def start(self, *, owner: str, now: datetime) -> "WorkUnit":
        self._require_live_owner(owner, now)
        if self.status is not WorkUnitStatus.LEASED:
            raise ValueError("only leased work can start")
        return self._transition({"status": WorkUnitStatus.RUNNING, "updated_at": now})

    def succeed(self, *, owner: str, now: datetime, output_uri: str, output_sha256: str) -> "WorkUnit":
        self._require_live_owner(owner, now)
        if self.status is not WorkUnitStatus.RUNNING:
            raise ValueError("only running work can succeed")
        return self._transition(
            {
                "status": WorkUnitStatus.SUCCEEDED,
                "lease_owner": None,
                "lease_expires_at": None,
                "output_uri": output_uri,
                "output_sha256": output_sha256,
                "updated_at": now,
            }
        )

    def fail(self, *, owner: str, now: datetime, error: Exception, retryable: bool) -> "WorkUnit":
        self._require_live_owner(owner, now)
        if self.status is not WorkUnitStatus.RUNNING:
            raise ValueError("only running work can fail")
        can_retry = retryable and self.attempt_count < self.max_attempts
        return self._transition(
            {
                "status": WorkUnitStatus.RETRYABLE if can_retry else WorkUnitStatus.FAILED,
                "lease_owner": None,
                "lease_expires_at": None,
                "error_class": type(error).__name__,
                "error_message": str(error)[:4000],
                "updated_at": now,
            }
        )

    def _require_live_owner(self, owner: str, now: datetime) -> None:
        if self.lease_owner != owner:
            raise ValueError("work unit lease belongs to another worker")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise ValueError("work unit lease expired")

    def _transition(self, updates: dict[str, object]) -> "WorkUnit":
        return WorkUnit.model_validate({**self.model_dump(), **updates})


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
