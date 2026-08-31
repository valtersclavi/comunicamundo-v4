"""Versioned model pricing loaded explicitly by the runtime operator."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
import json
import os

from pydantic import TypeAdapter

from .execution_contracts import TokenPriceCard


PRICE_CARDS_ENV = "HEFESTO_V2_PRICE_CARDS_JSON"
VERTEX_PRICING_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"

# Verified against the official Vertex pricing table on 2026-08-31. Gemini
# 2.5 Flash charges text response and reasoning tokens at the same output rate.
# An explicit environment catalog can replace this card without a code deploy.
BUNDLED_PRICE_CARDS = (
    TokenPriceCard(
        model="gemini-2.5-flash",
        effective_at=datetime(2026, 8, 31, tzinfo=UTC),
        source_url=VERTEX_PRICING_URL,
        input_per_million_usd=Decimal("0.30"),
        cached_input_per_million_usd=Decimal("0.03"),
        output_per_million_usd=Decimal("2.50"),
        reasoning_per_million_usd=Decimal("2.50"),
    ),
)


class PriceCatalog:
    """Selects the newest applicable exact-model rate card."""

    def __init__(self, cards: list[TokenPriceCard]) -> None:
        self._cards = tuple(sorted(cards, key=lambda card: card.effective_at))

    def resolve(self, model: str, at: datetime) -> TokenPriceCard | None:
        applicable = [card for card in self._cards if card.model == model and card.effective_at <= at]
        return applicable[-1] if applicable else None


def price_resolver_from_environment(
    environ: dict[str, str] | None = None,
) -> Callable[[str, datetime], TokenPriceCard | None] | None:
    """Use a verified bundled card unless an explicit catalog replaces it."""

    values = os.environ if environ is None else environ
    raw = values.get(PRICE_CARDS_ENV, "").strip()
    if not raw:
        return PriceCatalog(list(BUNDLED_PRICE_CARDS)).resolve
    try:
        payload = json.loads(raw)
        cards = TypeAdapter(list[TokenPriceCard]).validate_python(payload)
    except Exception as error:
        raise ValueError(f"invalid {PRICE_CARDS_ENV}") from error
    if not cards:
        raise ValueError(f"{PRICE_CARDS_ENV} must contain at least one price card")
    return PriceCatalog(cards).resolve
