from datetime import UTC, datetime
from decimal import Decimal
import json

import pytest

from comunicamundo_v4.hefesto.pricing import PRICE_CARDS_ENV, price_resolver_from_environment


def _card(*, effective_at: str, input_price: str) -> dict:
    return {
        "model": "gemini-2.5-flash",
        "effective_at": effective_at,
        "source_url": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
        "input_per_million_usd": input_price,
        "cached_input_per_million_usd": "0.03",
        "output_per_million_usd": "2.50",
        "reasoning_per_million_usd": "2.50",
    }


def test_price_resolver_selects_latest_applicable_exact_model_card() -> None:
    cards = [_card(effective_at="2026-01-01T00:00:00Z", input_price="0.10")]
    cards.append(_card(effective_at="2026-08-01T00:00:00Z", input_price="0.20"))
    resolver = price_resolver_from_environment({PRICE_CARDS_ENV: json.dumps(cards)})

    assert resolver is not None
    selected = resolver("gemini-2.5-flash", datetime(2026, 8, 31, tzinfo=UTC))
    assert selected is not None
    assert selected.input_per_million_usd == Decimal("0.20")
    assert resolver("another-model", datetime(2026, 8, 31, tzinfo=UTC)) is None


def test_price_resolver_does_not_guess_when_configuration_is_absent() -> None:
    assert price_resolver_from_environment({}) is None


@pytest.mark.parametrize("raw", ["not-json", "[]", "{}"])
def test_price_resolver_fails_closed_on_invalid_explicit_configuration(raw: str) -> None:
    with pytest.raises(ValueError, match=PRICE_CARDS_ENV):
        price_resolver_from_environment({PRICE_CARDS_ENV: raw})
