"""Single asynchronous gateway for all new Hefesto model traffic."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, TypeVar
from uuid import NAMESPACE_URL, uuid5

from google import genai
from google.genai import types
from pydantic import BaseModel

from .execution_contracts import (
    LLMAttemptRecord,
    LLMCallIntent,
    LLMCallStatus,
    LLMUsage,
    TokenPriceCard,
    text_sha256,
)


ParsedT = TypeVar("ParsedT", bound=BaseModel)


class AsyncModelsTransport(Protocol):
    async def generate_content(self, *, model: str, contents: str, config: object) -> object: ...
    async def count_tokens(self, *, model: str, contents: str, config: object | None = None) -> object: ...


class UsageLedger(Protocol):
    async def append(self, record: LLMAttemptRecord) -> None: ...


class NullUsageLedger:
    async def append(self, record: LLMAttemptRecord) -> None:
        return None


PriceResolver = Callable[[str, datetime], TokenPriceCard | None]
Sleep = Callable[[float], Awaitable[None]]
UtcNow = Callable[[], datetime]
Monotonic = Callable[[], float]


@dataclass(frozen=True, slots=True)
class LLMGatewayResult:
    text: str
    parsed: BaseModel | None
    response_id: str | None
    model_version: str | None
    finish_reason: str | None
    usage: LLMUsage
    final_attempt: LLMAttemptRecord


class LLMCallError(RuntimeError):
    def __init__(self, message: str, attempts: tuple[LLMAttemptRecord, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts


class InputBudgetExceeded(ValueError):
    pass


class ProviderOutputTerminated(RuntimeError):
    """The provider returned a response that cannot be treated as complete."""


class VertexGenAITransport:
    """Owns the direct Google Gen AI client; LlamaIndex is not in this path."""

    def __init__(self, *, project: str, location: str) -> None:
        self._client = genai.Client(vertexai=True, project=project, location=location)

    async def generate_content(self, *, model: str, contents: str, config: object) -> object:
        return await self._client.aio.models.generate_content(model=model, contents=contents, config=config)

    async def count_tokens(self, *, model: str, contents: str, config: object | None = None) -> object:
        return await self._client.aio.models.count_tokens(model=model, contents=contents, config=config)


class LLMGateway:
    def __init__(
        self,
        transport: AsyncModelsTransport,
        *,
        ledger: UsageLedger | None = None,
        price_resolver: PriceResolver | None = None,
        sleep: Sleep = asyncio.sleep,
        utc_now: UtcNow | None = None,
        monotonic: Monotonic = perf_counter,
        exact_count_threshold: float = 0.8,
    ) -> None:
        if not 0 < exact_count_threshold <= 1:
            raise ValueError("exact token count threshold must be in (0, 1]")
        self._transport = transport
        self._ledger = ledger or NullUsageLedger()
        self._price_resolver = price_resolver
        self._sleep = sleep
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._exact_count_threshold = exact_count_threshold

    async def generate(
        self,
        intent: LLMCallIntent,
        *,
        prompt: str,
        system_instruction: str | None = None,
        response_schema: type[ParsedT] | None = None,
    ) -> LLMGatewayResult:
        if text_sha256(prompt) != intent.prompt_sha256:
            raise ValueError("prompt does not match the immutable call intent")
        supplied_system_hash = text_sha256(system_instruction) if system_instruction else None
        if supplied_system_hash != intent.system_instruction_sha256:
            raise ValueError("system instruction does not match the immutable call intent")
        supplied_schema_name = _schema_name(response_schema)
        if supplied_schema_name != intent.response_schema_name:
            raise ValueError("response schema does not match the immutable call intent")

        await self._validate_input_budget(intent, prompt=prompt, system_instruction=system_instruction)

        config_kwargs: dict[str, Any] = {
            "temperature": intent.temperature,
            "max_output_tokens": intent.budget.max_output_tokens,
            "thinking_config": types.ThinkingConfig(
                thinking_budget=intent.budget.thinking_budget_tokens,
                include_thoughts=False,
            ),
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_schema is not None:
            config_kwargs.update(
                response_mime_type="application/json",
                response_json_schema=_provider_response_schema(response_schema),
            )
        config = types.GenerateContentConfig(**config_kwargs)

        queued_at = self._utc_now()
        attempts: list[LLMAttemptRecord] = []
        for attempt_number in range(1, intent.max_attempts + 1):
            started_at = self._utc_now()
            started_monotonic = self._monotonic()
            try:
                response = await self._transport.generate_content(
                    model=intent.model,
                    contents=prompt,
                    config=config,
                )
            except Exception as error:
                finished_monotonic = self._monotonic()
                finished_at = self._utc_now()
                unknown = isinstance(error, (asyncio.TimeoutError, TimeoutError))
                retryable = unknown or _is_retryable(error)
                record = self._record(
                    intent,
                    attempt_number=attempt_number,
                    status=LLMCallStatus.UNKNOWN_OUTCOME if unknown else LLMCallStatus.FAILED,
                    queued_at=queued_at,
                    started_at=started_at,
                    finished_at=finished_at,
                    elapsed_ms=_elapsed_ms(started_monotonic, finished_monotonic),
                    retryable=retryable,
                    error=error,
                )
                await self._ledger.append(record)
                attempts.append(record)
                if not retryable or attempt_number >= intent.max_attempts:
                    raise LLMCallError(str(error), tuple(attempts)) from error
                await self._sleep(_backoff_seconds(attempt_number))
                queued_at = self._utc_now()
                continue

            finished_monotonic = self._monotonic()
            finished_at = self._utc_now()
            usage = _usage_from_response(response)
            finish_reason = _finish_reason_from_response(response)
            try:
                if finish_reason not in {None, "STOP"}:
                    raise ProviderOutputTerminated(
                        f"provider output ended with finish_reason={finish_reason}; the response was not accepted"
                    )
                parsed = _parsed_response(response, response_schema)
            except Exception as error:
                record = self._record(
                    intent,
                    attempt_number=attempt_number,
                    status=LLMCallStatus.FAILED,
                    queued_at=queued_at,
                    started_at=started_at,
                    finished_at=finished_at,
                    elapsed_ms=_elapsed_ms(started_monotonic, finished_monotonic),
                    usage=usage,
                    response=response,
                    finish_reason=finish_reason,
                    retryable=False,
                    error=error,
                )
                # A model response was already paid for. Persist the terminal
                # parse/termination failure and never repeat it implicitly.
                await self._ledger.append(record)
                attempts.append(record)
                raise LLMCallError(str(error), tuple(attempts)) from error

            record = self._record(
                intent,
                attempt_number=attempt_number,
                status=LLMCallStatus.SUCCEEDED,
                queued_at=queued_at,
                started_at=started_at,
                finished_at=finished_at,
                elapsed_ms=_elapsed_ms(started_monotonic, finished_monotonic),
                usage=usage,
                response=response,
                finish_reason=finish_reason,
            )
            # A ledger failure must fail closed and must never repeat a paid call.
            await self._ledger.append(record)
            return LLMGatewayResult(
                text=str(getattr(response, "text", "") or ""),
                parsed=parsed,
                response_id=_optional_string(getattr(response, "response_id", None)),
                model_version=_optional_string(getattr(response, "model_version", None)),
                finish_reason=finish_reason,
                usage=usage,
                final_attempt=record,
            )
        raise AssertionError("unreachable retry loop")

    async def _validate_input_budget(
        self,
        intent: LLMCallIntent,
        *,
        prompt: str,
        system_instruction: str | None,
    ) -> None:
        threshold = int(intent.budget.max_input_tokens * self._exact_count_threshold)
        if intent.budget.estimated_input_tokens < threshold:
            return
        counter = getattr(self._transport, "count_tokens", None)
        if not callable(counter):
            raise InputBudgetExceeded("near-limit input requires an exact provider token counter")
        config = types.CountTokensConfig(system_instruction=system_instruction) if system_instruction else None
        response = await counter(model=intent.model, contents=prompt, config=config)
        exact_tokens = int(getattr(response, "total_tokens", 0) or 0)
        if exact_tokens <= 0:
            raise InputBudgetExceeded("provider did not return a usable exact input token count")
        if exact_tokens > intent.budget.max_input_tokens:
            raise InputBudgetExceeded(
                f"exact input token count {exact_tokens} exceeds maximum {intent.budget.max_input_tokens}"
            )

    def _record(
        self,
        intent: LLMCallIntent,
        *,
        attempt_number: int,
        status: LLMCallStatus,
        queued_at: datetime,
        started_at: datetime,
        finished_at: datetime,
        elapsed_ms: int,
        usage: LLMUsage | None = None,
        response: object | None = None,
        finish_reason: str | None = None,
        retryable: bool = False,
        error: Exception | None = None,
    ) -> LLMAttemptRecord:
        price_card = (
            self._price_resolver(intent.model, finished_at)
            if self._price_resolver and usage and usage.provider_reported
            else None
        )
        return LLMAttemptRecord(
            attempt_id=uuid5(NAMESPACE_URL, f"hefesto:{intent.call_id}:attempt:{attempt_number}"),
            call_id=intent.call_id,
            execution_run_id=intent.execution_run_id,
            project_id=intent.project_id,
            work_unit_id=intent.work_unit_id,
            task_type=intent.task_type,
            prompt_version=intent.prompt_version,
            model=intent.model,
            location=intent.location,
            attempt=attempt_number,
            status=status,
            queued_at=queued_at,
            started_at=started_at,
            finished_at=finished_at,
            queue_wait_ms=_datetime_ms(queued_at, started_at),
            latency_ms=elapsed_ms,
            estimated_input_tokens=intent.budget.estimated_input_tokens,
            reserved_capacity_tokens=intent.budget.reserved_capacity_tokens,
            usage=usage,
            provider_response_id=_optional_string(getattr(response, "response_id", None)),
            provider_model_version=_optional_string(getattr(response, "model_version", None)),
            provider_finish_reason=finish_reason,
            retryable=retryable,
            error_class=type(error).__name__ if error else None,
            error_message=str(error)[:4000] if error else None,
            rate_card_effective_at=price_card.effective_at if price_card else None,
            rate_card_source_url=price_card.source_url if price_card else None,
            rated_cost_usd=price_card.rate(usage) if price_card and usage else None,
        )


_PROVIDER_SCHEMA_OMIT_KEYS = frozenset(
    {
        "$schema",
        "const",
        "contentEncoding",
        "contentMediaType",
        "default",
        "description",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "propertyNames",
        "title",
        "uniqueItems",
    }
)


def _provider_response_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Keep output shape while omitting constraints that explode provider grammar states."""

    return _strip_provider_schema_constraints(schema.model_json_schema())


def _strip_provider_schema_constraints(value: Any, *, named_schema_map: bool = False) -> Any:
    if isinstance(value, dict):
        if named_schema_map:
            # Keys under `properties` and `$defs` are user/model field names,
            # not JSON Schema keywords. A legitimate field named `title` must
            # survive even though schema metadata named `title` is omitted.
            return {
                key: _strip_provider_schema_constraints(item)
                for key, item in value.items()
            }
        return {
            key: _strip_provider_schema_constraints(
                item,
                named_schema_map=key in {"properties", "$defs", "definitions"},
            )
            for key, item in value.items()
            if key not in _PROVIDER_SCHEMA_OMIT_KEYS
        }
    if isinstance(value, list):
        return [_strip_provider_schema_constraints(item) for item in value]
    return value


def _usage_from_response(response: object) -> LLMUsage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return LLMUsage(provider_reported=False)
    prompt = _integer(metadata, "prompt_token_count")
    cached = _integer(metadata, "cached_content_token_count")
    output = _integer(metadata, "candidates_token_count")
    reasoning = _integer(metadata, "thoughts_token_count")
    tool_prompt = _integer(metadata, "tool_use_prompt_token_count")
    provider_total = _integer(metadata, "total_token_count")
    component_total = prompt + output + reasoning + tool_prompt
    return LLMUsage(
        prompt_tokens=prompt,
        cached_tokens=cached,
        output_tokens=output,
        reasoning_tokens=reasoning,
        tool_prompt_tokens=tool_prompt,
        total_tokens=provider_total or component_total,
    )


def _parsed_response(response: object, schema: type[ParsedT] | None) -> BaseModel | None:
    if schema is None:
        return None
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    if parsed is not None:
        return schema.model_validate(parsed)
    return schema.model_validate_json(str(getattr(response, "text", "")))


def _finish_reason_from_response(response: object) -> str | None:
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    raw = getattr(reason, "value", None) or getattr(reason, "name", None) or str(reason)
    normalized = str(raw).rsplit(".", maxsplit=1)[-1].strip().upper()
    return normalized or None


def _integer(value: object, name: str) -> int:
    raw = getattr(value, name, 0)
    return int(raw or 0)


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _schema_name(schema: type[BaseModel] | None) -> str | None:
    if schema is None:
        return None
    return f"{schema.__module__}.{schema.__qualname__}"


def _datetime_ms(start: datetime, finish: datetime) -> int:
    return max(0, int((finish - start).total_seconds() * 1000))


def _elapsed_ms(start: float, finish: float) -> int:
    return max(0, int((finish - start) * 1000))


def _backoff_seconds(attempt_number: int) -> float:
    return min(30.0, float(2 ** (attempt_number - 1)))


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    if callable(code):
        code = code()
    values = {str(item).lower() for item in (status, code) if item is not None}
    if values & {"429", "500", "502", "503", "504", "resource_exhausted", "unavailable"}:
        return True
    rendered = f"{type(error).__name__}: {error}".lower()
    return any(marker in rendered for marker in ("resource exhausted", "resource_exhausted", "too many requests"))
