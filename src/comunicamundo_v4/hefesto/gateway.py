from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import asyncio
import json
from time import perf_counter
from typing import Generic, Protocol, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from .contracts import CallUsage


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ModelResult(Generic[OutputT]):
    value: OutputT
    usage: CallUsage


class ModelGateway(Protocol):
    async def count_tokens(self, text: str) -> int: ...

    async def generate(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: type[OutputT],
        system_instruction: str,
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> ModelResult[OutputT]: ...


class ModelCallError(RuntimeError):
    def __init__(
        self,
        message: str,
        usage: CallUsage | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.retryable = retryable


class VertexGateway:
    """One provider attempt per semantic call; paid malformed output is never retried implicitly."""

    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str = "gemini-2.5-flash",
        timeout_seconds: int = 240,
    ) -> None:
        self.model = model
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._timeout_seconds = timeout_seconds

    async def count_tokens(self, text: str) -> int:
        try:
            async with asyncio.timeout(30):
                result = await self._client.aio.models.count_tokens(
                    model=self.model, contents=text
                )
        except Exception as error:
            raise ModelCallError(f"token measurement failed: {error}", retryable=True) from error
        total = int(getattr(result, "total_tokens", 0) or 0)
        if total <= 0:
            raise ModelCallError("token measurement returned no token count", retryable=True)
        return total

    async def generate(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: type[OutputT],
        system_instruction: str,
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> ModelResult[OutputT]:
        config = types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(
                thinking_budget=thinking_tokens,
                include_thoughts=False,
            ),
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=schema,
        )
        started = perf_counter()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
        except Exception as error:
            latency = max(0, int((perf_counter() - started) * 1000))
            message = f"provider call failed: {error}"
            retryable = any(
                marker in message.upper()
                for marker in ("429", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "500", "502", "503", "504")
            )
            usage = CallUsage(
                task_type=task_type,
                prompt_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                total_tokens=0,
                latency_ms=latency,
                rated_cost_usd="0.000000000",
                status="failed",
                error_message=message[:2000],
            )
            raise ModelCallError(message, usage, retryable=retryable) from error
        latency = max(0, int((perf_counter() - started) * 1000))
        usage = _usage(task_type, response, latency)
        finish_reason = _finish_reason(response)
        if finish_reason not in {None, "STOP"}:
            raise ModelCallError(
                f"{task_type}: provider output ended with {finish_reason}",
                usage,
            )
        try:
            parsed = getattr(response, "parsed", None)
            value = schema.model_validate(parsed) if parsed is not None else schema.model_validate_json(
                str(getattr(response, "text", "") or "")
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            raise ModelCallError(f"invalid {schema.__name__}: {error}", usage) from error
        return ModelResult(value=value, usage=usage)


def _usage(task_type: str, response: object, latency_ms: int) -> CallUsage:
    metadata = getattr(response, "usage_metadata", None)
    prompt = _integer(metadata, "prompt_token_count")
    cached = _integer(metadata, "cached_content_token_count")
    output = _integer(metadata, "candidates_token_count")
    reasoning = _integer(metadata, "thoughts_token_count")
    total = _integer(metadata, "total_token_count") or prompt + output + reasoning
    cost = (
        Decimal(max(prompt - cached, 0)) * Decimal("0.30")
        + Decimal(cached) * Decimal("0.03")
        + Decimal(output + reasoning) * Decimal("2.50")
    ) / Decimal(1_000_000)
    return CallUsage(
        task_type=task_type,
        prompt_tokens=prompt,
        cached_tokens=cached,
        output_tokens=output,
        reasoning_tokens=reasoning,
        total_tokens=total,
        latency_ms=latency_ms,
        rated_cost_usd=str(cost.quantize(Decimal("0.000000001"))),
        status="succeeded",
    )


def _integer(value: object, name: str) -> int:
    return int(getattr(value, name, 0) or 0) if value is not None else 0


def _finish_reason(response: object) -> str | None:
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    raw = getattr(reason, "value", None) or getattr(reason, "name", None) or reason
    if raw is None:
        return None
    return str(raw).rsplit(".", 1)[-1].upper()
