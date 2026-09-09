from __future__ import annotations

from collections.abc import Callable, Sequence
from hashlib import sha256
from math import ceil
from typing import TypeVar

from .contracts import Paragraph, Passage


ItemT = TypeVar("ItemT")


def conservative_tokens(text: str) -> int:
    """A deliberately high local estimate used only to form candidate packets."""
    return max(1, ceil(len(text.encode("utf-8")) / 3))


def passages_from_paragraphs(
    paragraphs: Sequence[Paragraph], *, passage_token_target: int
) -> list[Passage]:
    if passage_token_target < 100:
        raise ValueError("passage token target is too small")
    result: list[Passage] = []
    source_ordinal = 0
    passage_ordinal = 0
    for paragraph in paragraphs:
        if paragraph.source_ordinal != source_ordinal:
            source_ordinal = paragraph.source_ordinal
            passage_ordinal = 0
        parts = _sentence_groups(paragraph, passage_token_target)
        for part in parts:
            passage_ordinal += 1
            identity = (
                f"{paragraph.source_id}:{paragraph.ordinal}:"
                f"{passage_ordinal}:{sha256(part.encode()).hexdigest()}"
            )
            result.append(
                Passage(
                    passage_id=sha256(identity.encode()).hexdigest(),
                    source_id=paragraph.source_id,
                    source_ordinal=paragraph.source_ordinal,
                    ordinal=passage_ordinal,
                    paragraph_ordinal=paragraph.ordinal,
                    text=part,
                )
            )
    return result


def candidate_packets(
    items: Sequence[ItemT],
    *,
    token_budget: int,
    render: Callable[[Sequence[ItemT]], str],
) -> list[list[ItemT]]:
    """Pack cheaply; every returned packet must still be measured by the model tokenizer."""
    if not items:
        return []
    packets: list[list[ItemT]] = []
    current: list[ItemT] = []
    for item in items:
        candidate = [*current, item]
        if current and conservative_tokens(render(candidate)) > token_budget:
            packets.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        packets.append(current)
    return packets


def split_packet(items: Sequence[ItemT]) -> tuple[list[ItemT], list[ItemT]]:
    if len(items) < 2:
        raise ValueError("one semantic unit exceeds the model token budget")
    middle = len(items) // 2
    return list(items[:middle]), list(items[middle:])


def _sentence_groups(paragraph: Paragraph, token_target: int) -> list[str]:
    if conservative_tokens(paragraph.text) <= token_target:
        return [paragraph.text]
    groups: list[str] = []
    current: list[str] = []
    for sentence in paragraph.sentences:
        if conservative_tokens(sentence.text) > token_target:
            raise ValueError(
                "one sentence exceeds the safe token budget; the source may contain malformed text"
            )
        candidate = " ".join([*current, sentence.text])
        if current and conservative_tokens(candidate) > token_target:
            groups.append(" ".join(current))
            current = [sentence.text]
        else:
            current.append(sentence.text)
    if current:
        groups.append(" ".join(current))
    return groups
