import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field

from comunicamundo_v4.hefesto.execution_contracts import (
    LLMCallIntent,
    LLMCallStatus,
    TokenBudget,
    TokenPriceCard,
    text_sha256,
)
from comunicamundo_v4.hefesto.llm_gateway import (
    LLMCallError,
    LLMGateway,
    _provider_response_schema,
)


PROMPT = "Sintetize as evidências sem inventar causalidade."


class Proposal(BaseModel):
    synthesis: str = Field(min_length=1, max_length=1_200)


class TitledSubject(BaseModel):
    title: str = Field(min_length=1)
    development: str = Field(min_length=1)


class TitledProject(BaseModel):
    core_subjects: list[TitledSubject] = Field(min_length=1)


class MemoryLedger:
    def __init__(self) -> None:
        self.records = []

    async def append(self, record) -> None:
        self.records.append(record)


class FakeTransport:
    def __init__(self, outcomes, *, exact_tokens: int | None = None) -> None:
        self.outcomes = list(outcomes)
        self.calls = []
        self.exact_tokens = exact_tokens
        self.count_calls = []

    async def generate_content(self, *, model: str, contents: str, config: object) -> object:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def count_tokens(self, *, model: str, contents: str, config: object | None = None) -> object:
        self.count_calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(total_tokens=self.exact_tokens)


class ResourceExhausted(RuntimeError):
    status_code = 429


def _intent(
    *,
    max_attempts: int = 3,
    system_instruction: str | None = None,
    response_schema: type[BaseModel] | None = None,
) -> LLMCallIntent:
    return LLMCallIntent(
        execution_run_id=uuid4(),
        project_id=uuid4(),
        work_unit_id="source-dossier:1",
        task_type="source_dossier",
        prompt_version="hefesto.source-dossier.v1",
        prompt_sha256=text_sha256(PROMPT),
        system_instruction_sha256=text_sha256(system_instruction) if system_instruction else None,
        response_schema_name=(
            f"{response_schema.__module__}.{response_schema.__qualname__}" if response_schema else None
        ),
        model="gemini-2.5-flash",
        location="global",
        temperature=0.2,
        budget=TokenBudget(
            estimated_input_tokens=5_000,
            max_input_tokens=10_000,
            max_output_tokens=2_000,
            thinking_budget_tokens=500,
            context_window_tokens=20_000,
            output_capacity_weight=Decimal("9"),
            reasoning_capacity_weight=Decimal("9"),
        ),
        max_attempts=max_attempts,
    )


def _response(*, parsed=None, finish_reason: str | None = None):
    return SimpleNamespace(
        text='{"synthesis":"síntese"}',
        parsed=parsed,
        response_id="response-1",
        model_version="gemini-2.5-flash-001",
        candidates=(
            [SimpleNamespace(finish_reason=finish_reason)]
            if finish_reason is not None
            else []
        ),
        usage_metadata=SimpleNamespace(
            prompt_token_count=5_100,
            cached_content_token_count=100,
            candidates_token_count=600,
            thoughts_token_count=300,
            tool_use_prompt_token_count=0,
            total_token_count=6_000,
        ),
    )


def _rate_card(model: str, at: datetime) -> TokenPriceCard:
    return TokenPriceCard(
        model=model,
        effective_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_url="https://example.test/rates",
        input_per_million_usd=Decimal("1"),
        cached_input_per_million_usd=Decimal("0.1"),
        output_per_million_usd=Decimal("5"),
        reasoning_per_million_usd=Decimal("5"),
    )


@pytest.mark.asyncio
async def test_gateway_uses_explicit_budgets_and_persists_provider_usage() -> None:
    transport = FakeTransport([_response(parsed=Proposal(synthesis="síntese"))])
    ledger = MemoryLedger()
    gateway = LLMGateway(transport, ledger=ledger, price_resolver=_rate_card)

    result = await gateway.generate(
        _intent(system_instruction="Responda em pt-BR.", response_schema=Proposal),
        prompt=PROMPT,
        system_instruction="Responda em pt-BR.",
        response_schema=Proposal,
    )

    assert result.parsed == Proposal(synthesis="síntese")
    assert result.usage.prompt_tokens == 5_100
    assert result.usage.output_tokens == 600
    assert result.usage.reasoning_tokens == 300
    assert len(transport.calls) == 1
    config = transport.calls[0]["config"]
    assert config.max_output_tokens == 2_000
    assert config.thinking_config.thinking_budget == 500
    assert config.thinking_config.include_thoughts is False
    assert config.response_mime_type == "application/json"
    assert config.response_schema is None
    assert config.response_json_schema is not None
    rendered_schema = str(config.response_json_schema)
    assert "minLength" not in rendered_schema
    assert "maxLength" not in rendered_schema
    assert "title" not in rendered_schema
    assert Proposal.model_json_schema()["properties"]["synthesis"]["maxLength"] == 1_200
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.status is LLMCallStatus.SUCCEEDED
    assert record.provider_response_id == "response-1"
    assert record.rated_cost_usd == Decimal("0.009510000")


def test_provider_schema_preserves_a_property_named_title() -> None:
    schema = _provider_response_schema(TitledProject)
    subject = schema["$defs"]["TitledSubject"]

    assert set(subject["required"]) == {"title", "development"}
    assert set(subject["properties"]) == {"title", "development"}
    assert "title" not in subject["properties"]["title"]


@pytest.mark.asyncio
async def test_gateway_retries_429_with_ledger_entry_for_every_attempt() -> None:
    transport = FakeTransport([ResourceExhausted("busy"), _response()])
    ledger = MemoryLedger()
    sleeps = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    gateway = LLMGateway(transport, ledger=ledger, sleep=fake_sleep)
    result = await gateway.generate(_intent(max_attempts=2), prompt=PROMPT)

    assert result.text.startswith("{")
    assert sleeps == [1.0]
    assert [record.status for record in ledger.records] == [
        LLMCallStatus.FAILED,
        LLMCallStatus.SUCCEEDED,
    ]
    assert ledger.records[0].retryable is True
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_gateway_marks_timeout_as_unknown_outcome() -> None:
    transport = FakeTransport([asyncio.TimeoutError("provider timed out")])
    ledger = MemoryLedger()
    gateway = LLMGateway(transport, ledger=ledger)

    with pytest.raises(LLMCallError) as captured:
        await gateway.generate(_intent(max_attempts=1), prompt=PROMPT)

    assert captured.value.attempts[0].status is LLMCallStatus.UNKNOWN_OUTCOME
    assert ledger.records[0].status is LLMCallStatus.UNKNOWN_OUTCOME
    assert ledger.records[0].usage is None


@pytest.mark.asyncio
async def test_gateway_rejects_changed_prompt_before_provider_call() -> None:
    transport = FakeTransport([_response()])
    gateway = LLMGateway(transport)

    with pytest.raises(ValueError, match="immutable call intent"):
        await gateway.generate(_intent(), prompt="prompt diferente")

    assert transport.calls == []


@pytest.mark.asyncio
async def test_gateway_binds_system_instruction_and_response_schema_to_intent() -> None:
    transport = FakeTransport([_response()])
    gateway = LLMGateway(transport)

    with pytest.raises(ValueError, match="system instruction"):
        await gateway.generate(_intent(), prompt=PROMPT, system_instruction="mudou")
    with pytest.raises(ValueError, match="response schema"):
        await gateway.generate(_intent(), prompt=PROMPT, response_schema=Proposal)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_missing_provider_usage_is_explicit_and_never_rated_as_zero() -> None:
    response = _response()
    response.usage_metadata = None
    transport = FakeTransport([response])
    ledger = MemoryLedger()
    gateway = LLMGateway(transport, ledger=ledger, price_resolver=_rate_card)

    result = await gateway.generate(_intent(), prompt=PROMPT)

    assert result.usage.provider_reported is False
    assert ledger.records[0].rated_cost_usd is None
    assert ledger.records[0].rate_card_effective_at is None


@pytest.mark.asyncio
async def test_ledger_failure_never_repeats_a_successful_paid_call() -> None:
    transport = FakeTransport([_response(), _response()])

    class BrokenLedger:
        async def append(self, record) -> None:
            raise RuntimeError("ledger unavailable")

    gateway = LLMGateway(transport, ledger=BrokenLedger())
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await gateway.generate(_intent(), prompt=PROMPT)

    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_near_limit_prompt_is_counted_exactly_before_any_paid_generation() -> None:
    transport = FakeTransport([_response()], exact_tokens=10_001)
    gateway = LLMGateway(transport)
    original = _intent()
    intent = original.model_copy(
        update={
            "budget": original.budget.model_copy(
                update={"estimated_input_tokens": 8_500, "max_input_tokens": 10_000}
            )
        }
    )

    with pytest.raises(ValueError, match="exact input token count"):
        await gateway.generate(intent, prompt=PROMPT)

    assert len(transport.count_calls) == 1
    assert transport.calls == []


@pytest.mark.asyncio
async def test_exact_count_allows_a_near_limit_prompt_that_really_fits() -> None:
    transport = FakeTransport([_response()], exact_tokens=9_500)
    gateway = LLMGateway(transport)
    original = _intent()
    intent = original.model_copy(
        update={
            "budget": original.budget.model_copy(
                update={"estimated_input_tokens": 8_500, "max_input_tokens": 10_000}
            )
        }
    )

    await gateway.generate(intent, prompt=PROMPT)

    assert len(transport.count_calls) == 1
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_invalid_structured_output_is_a_failed_paid_attempt_and_is_not_retried() -> None:
    response = _response()
    response.text = '{"synthesis":"truncated'
    transport = FakeTransport([response, _response()])
    ledger = MemoryLedger()
    gateway = LLMGateway(transport, ledger=ledger, price_resolver=_rate_card)

    with pytest.raises(LLMCallError, match="Invalid JSON"):
        await gateway.generate(
            _intent(max_attempts=2, response_schema=Proposal),
            prompt=PROMPT,
            response_schema=Proposal,
        )

    assert len(transport.calls) == 1
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.status is LLMCallStatus.FAILED
    assert record.retryable is False
    assert record.usage.output_tokens == 600
    assert record.rated_cost_usd == Decimal("0.009510000")


@pytest.mark.asyncio
async def test_max_tokens_finish_reason_is_explicit_and_never_accepted_or_retried() -> None:
    transport = FakeTransport(
        [_response(parsed=Proposal(synthesis="partial"), finish_reason="MAX_TOKENS"), _response()]
    )
    ledger = MemoryLedger()
    gateway = LLMGateway(transport, ledger=ledger)

    with pytest.raises(LLMCallError, match="finish_reason=MAX_TOKENS"):
        await gateway.generate(
            _intent(max_attempts=2, response_schema=Proposal),
            prompt=PROMPT,
            response_schema=Proposal,
        )

    assert len(transport.calls) == 1
    assert ledger.records[0].status is LLMCallStatus.FAILED
    assert ledger.records[0].provider_finish_reason == "MAX_TOKENS"
    assert ledger.records[0].error_class == "ProviderOutputTerminated"
