"""Versioned model pricing loaded explicitly by the runtime operator."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os

from pydantic import TypeAdapter

from .execution_contracts import TokenPriceCard


PRICE_CARDS_ENV = "HEFESTO_V2_PRICE_CARDS_JSON"


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
    """Return no resolver when pricing was not supplied; never invent a rate."""

    values = os.environ if environ is None else environ
    raw = values.get(PRICE_CARDS_ENV, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        cards = TypeAdapter(list[TokenPriceCard]).validate_python(payload)
    except Exception as error:
        raise ValueError(f"invalid {PRICE_CARDS_ENV}") from error
    if not cards:
        raise ValueError(f"{PRICE_CARDS_ENV} must contain at least one price card")
    return PriceCatalog(cards).resolve
