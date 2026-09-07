from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from .contracts import NormalizedSource, Paragraph, Sentence, content_hash


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
_SENTENCE_END = re.compile(r"[.!?…。！？]+(?:[\"'”’»）)\]]+)?(?=\s+|$)")


def segment_sources(sources: Sequence[NormalizedSource]) -> list[Paragraph]:
    result: list[Paragraph] = []
    for source_ordinal, source in enumerate(sources, start=1):
        text = source.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        raw_paragraphs = [part.strip() for part in _PARAGRAPH_BREAK.split(text) if part.strip()]
        for paragraph_ordinal, paragraph_text in enumerate(raw_paragraphs, start=1):
            paragraph_id = content_hash(
                f"{source.source_id}:{paragraph_ordinal}:{content_hash(paragraph_text)}"
            )
            result.append(
                Paragraph(
                    paragraph_id=paragraph_id,
                    source_id=source.source_id,
                    source_ordinal=source_ordinal,
                    ordinal=paragraph_ordinal,
                    text=paragraph_text,
                    sentences=_sentences(paragraph_id, paragraph_text),
                )
            )
    if not result:
        raise ValueError("normalized sources contain no paragraphs")
    return result


def _sentences(paragraph_id: str, text: str) -> list[Sentence]:
    boundaries = [match.end() for match in _SENTENCE_END.finditer(text)]
    starts = [0, *boundaries]
    ends = [*boundaries, len(text)]
    parts = [text[start:end].strip() for start, end in zip(starts, ends) if text[start:end].strip()]
    if not parts:
        parts = [text]
    return [
        Sentence(
            sentence_id=content_hash(f"{paragraph_id}:{ordinal}:{content_hash(value)}"),
            ordinal=ordinal,
            text=value,
        )
        for ordinal, value in enumerate(parts, start=1)
    ]


@dataclass(frozen=True, slots=True)
class ParagraphBatch:
    batch_id: str
    paragraphs: tuple[Paragraph, ...]
    estimated_tokens: int


def make_batches(paragraphs: Sequence[Paragraph], *, target_tokens: int = 12_000) -> list[ParagraphBatch]:
    """Pack adjacent whole paragraphs. Batch boundaries never change semantic cardinality."""
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    batches: list[ParagraphBatch] = []
    current: list[Paragraph] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        identity = "|".join(item.paragraph_id for item in current)
        batches.append(
            ParagraphBatch(
                batch_id=content_hash(identity),
                paragraphs=tuple(current),
                estimated_tokens=current_tokens,
            )
        )
        current = []
        current_tokens = 0

    for paragraph in paragraphs:
        estimate = max(1, (len(paragraph.text) + 2) // 3)
        if current and (
            paragraph.source_id != current[-1].source_id
            or current_tokens + estimate > target_tokens
        ):
            flush()
        current.append(paragraph)
        current_tokens += estimate
    flush()
    flattened = [item.paragraph_id for batch in batches for item in batch.paragraphs]
    expected = [item.paragraph_id for item in paragraphs]
    if flattened != expected or len(flattened) != len(set(flattened)):
        raise AssertionError("each paragraph must belong to exactly one ordered batch")
    return batches

