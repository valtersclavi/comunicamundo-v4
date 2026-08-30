from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.execution_contracts import (
    LLMUsage,
    TokenBudget,
    TokenPriceCard,
)


def test_token_budget_reserves_weighted_output_and_reasoning_capacity() -> None:
    budget = TokenBudget(
        estimated_input_tokens=10_000,
        max_input_tokens=20_000,
        max_output_tokens=2_000,
        thinking_budget_tokens=1_000,
        context_window_tokens=30_000,
        input_capacity_weight=Decimal("1"),
        output_capacity_weight=Decimal("5"),
        reasoning_capacity_weight=Decimal("9"),
    )

    assert budget.reserved_capacity_tokens == 24_000


@pytest.mark.parametrize(
    "updates",
    [
        {"estimated_input_tokens": 20_001},
        {"thinking_budget_tokens": 2_001},
        {"max_input_tokens": 29_000},
    ],
)
def test_token_budget_fails_closed_when_envelope_is_impossible(updates) -> None:
    data = {
        "estimated_input_tokens": 10_000,
        "max_input_tokens": 20_000,
        "max_output_tokens": 2_000,
        "thinking_budget_tokens": 1_000,
        "context_window_tokens": 30_000,
    }
    data.update(updates)

    with pytest.raises(ValidationError):
        TokenBudget(**data)


def test_price_card_uses_uncached_input_and_separate_reasoning_rate() -> None:
    card = TokenPriceCard(
        model="test-model",
        effective_at=datetime(2026, 8, 30, tzinfo=UTC),
        source_url="https://example.test/rates",
        input_per_million_usd=Decimal("1"),
        cached_input_per_million_usd=Decimal("0.1"),
        output_per_million_usd=Decimal("5"),
        reasoning_per_million_usd=Decimal("7"),
    )
    usage = LLMUsage(
        prompt_tokens=100_000,
        cached_tokens=20_000,
        output_tokens=10_000,
        reasoning_tokens=5_000,
        total_tokens=115_000,
    )

    assert card.rate(usage) == Decimal("0.167000000")


def test_usage_rejects_impossible_cached_token_count() -> None:
    with pytest.raises(ValidationError):
        LLMUsage(prompt_tokens=10, cached_tokens=11)


def test_price_card_refuses_to_invent_zero_cost_when_usage_is_missing() -> None:
    card = TokenPriceCard(
        model="test-model",
        effective_at=datetime(2026, 8, 30, tzinfo=UTC),
        source_url="https://example.test/rates",
        input_per_million_usd=Decimal("1"),
        cached_input_per_million_usd=Decimal("0.1"),
        output_per_million_usd=Decimal("5"),
        reasoning_per_million_usd=Decimal("7"),
    )

    with pytest.raises(ValueError, match="provider-reported usage"):
        card.rate(LLMUsage(provider_reported=False))
