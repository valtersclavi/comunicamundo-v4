"""Small, checkpointed extraction of source claims from already selected passages.

This module never reads or modifies ingestion. Passage references are resolved against
the existing PostgreSQL paragraph IDs before a batch is committed.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from hashlib import sha256
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .batching import candidate_packets, conservative_tokens
from .contracts import EditorialBrief, Paragraph, Passage


EXTRACTION_VERSION = "matrix-extract-v1"
INPUT_TOKEN_LIMIT = 2_400


class MatrixEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passage_id: str
    role: str = Field(pattern="^(supports|limits|contradicts)$")


class MatrixClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(idea|datum|qualification|limit|contradiction)$")
    statement: str = Field(min_length=1)
    evidence: list[MatrixEvidence] = Field(min_length=1)


class MatrixExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[MatrixClaim] = Field(default_factory=list)


def _chunks(passage: Passage) -> list[Passage]:
    """Bound one long selected passage without changing its stored identity."""
    if conservative_tokens(passage.text) <= 850:
        return [passage]
    sentences = re.split(r"(?<=[.!?])\s+", passage.text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if conservative_tokens(sentence) > 850:
            if current:
                pieces.append(current)
                current = ""
            # Exceptional unpunctuated text still needs a bounded call.
            words = sentence.split()
            group = ""
            for word in words:
                candidate = f"{group} {word}".strip()
                if group and conservative_tokens(candidate) > 850:
                    pieces.append(group)
                    group = word
                else:
                    group = candidate
            if group:
                pieces.append(group)
            continue
        candidate = f"{current} {sentence}".strip()
        if current and conservative_tokens(candidate) > 850:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return [passage.model_copy(update={"text": item}) for item in pieces if item.strip()]


def _reference_id(passage: Passage) -> str:
    return sha256((passage.passage_id + ":" + passage.text).encode()).hexdigest()


def _prompt(brief: EditorialBrief, passages: Sequence[Passage], titles: dict[int, str | None]) -> str:
    source = passages[0].source_ordinal
    material = "\n\n".join(
        f"PASSAGE {_reference_id(item)}: {item.text}" for item in passages
    )
    return f"""Extract distinct editorial contributions from the selected source material.
Return every distinct, complete claim relevant to the brief. Split compound ideas into separate claims.\nInclude important qualifications,
limits and contradictions, not only affirmative ideas. If a passage contains no
contribution relevant to the brief, an empty list is valid. Do not invent evidence
or cite a passage outside this batch. Every claim must cite its supporting,
limiting or contradicting passage ID. Do not produce a summary of the whole
source. Preserve each substantive detail; split further when needed.

BRIEF: {brief.model_dump_json()}
SOURCE: {source} — {titles.get(source) or "untitled"}
{material}"""


def _batch_id(passages: Sequence[Passage]) -> str:
    identity = EXTRACTION_VERSION + ":" + ":".join(
        item.passage_id + ":" + sha256(item.text.encode()).hexdigest()
        for item in passages
    )
    return sha256(identity.encode()).hexdigest()


async def extract_selected_matrix(
    execution_run_id: UUID,
    brief: EditorialBrief,
    selected: Sequence[Passage],
    paragraphs: Sequence[Paragraph],
    titles: dict[int, str | None],
    *,
    count_tokens: Callable[[str], Awaitable[int]],
    invoke: Callable[[str], Awaitable[MatrixExtraction]],
    repository: object,
) -> None:
    """Persist complete batches; resume skips them, and failures retain their cause."""
    paragraph_ids = {
        (item.source_ordinal, item.ordinal): item.paragraph_id
        for item in paragraphs
    }
    existing = set(await repository.completed_matrix_batches(execution_run_id))
    split_batches = set(await repository.matrix_split_batches(execution_run_id))
    by_source: dict[int, list[Passage]] = {}
    for passage in selected:
        by_source.setdefault(passage.source_ordinal, []).extend(_chunks(passage))
    for source_passages in by_source.values():
        packets = candidate_packets(
            source_passages,
            token_budget=1_500,
            render=lambda items: _prompt(brief, items, titles),
        )
        for packet in packets:
            await _extract_packet(
                execution_run_id, brief, packet, titles, paragraph_ids,
                existing, split_batches, count_tokens, invoke, repository,
            )


async def _extract_packet(
    execution_run_id: UUID,
    brief: EditorialBrief,
    packet: list[Passage],
    titles: dict[int, str | None],
    paragraph_ids: dict[tuple[int, int], str],
    existing: set[str],
    split_batches: set[str],
    count_tokens: Callable[[str], Awaitable[int]],
    invoke: Callable[[str], Awaitable[MatrixExtraction]],
    repository: object,
) -> None:
    batch_id = _batch_id(packet)
    if batch_id in existing:
        return
    if batch_id in split_batches:
        await _split_limited_packet(
            execution_run_id, brief, packet, titles, paragraph_ids,
            existing, split_batches, count_tokens, invoke, repository,
        )
        return
    prompt = _prompt(brief, packet, titles)
    tokens = await count_tokens(prompt)
    if tokens > INPUT_TOKEN_LIMIT:
        await _split_limited_packet(
            execution_run_id, brief, packet, titles, paragraph_ids,
            existing, split_batches, count_tokens, invoke, repository,
        )
        return
    try:
        result = await invoke(prompt)
        lookup = {_reference_id(item): item for item in packet}
        validated: list[dict[str, object]] = []
        for index, claim in enumerate(result.claims):
            if any(item.passage_id not in lookup for item in claim.evidence):
                repair_prompt = (
                    "Repair the evidence IDs for exactly this claim. Return one claim "
                    "with the same kind and statement, citing IDs copied exactly "
                    "from the passages below. Choose only passages that actually "
                    "support, limit or contradict the statement. Return an empty "
                    "claims list if no passage supports it. Do not add ideas.\n"
                    f"CLAIM: {claim.model_dump_json()}\n"
                    + "\n".join(
                        f"PASSAGE {_reference_id(item)}: {item.text}"
                        for item in packet
                    )
                )
                repaired = await invoke(repair_prompt)
                if (len(repaired.claims) != 1 or repaired.claims[0].kind != claim.kind
                        or repaired.claims[0].statement != claim.statement):
                    raise ValueError("matrix evidence repair could not ground the claim")
                claim = claim.model_copy(update={
                    "evidence": repaired.claims[0].evidence,
                })
            references: list[dict[str, object]] = []
            for evidence in claim.evidence:
                passage = lookup.get(evidence.passage_id)
                if passage is None:
                    raise ValueError(f"matrix evidence is not in batch: {evidence.passage_id}")
                paragraph_id = paragraph_ids.get(
                    (passage.source_ordinal, passage.paragraph_ordinal)
                )
                if paragraph_id is None:
                    raise ValueError("matrix evidence has no persisted paragraph")
                references.append({
                    "passage_id": passage.passage_id,
                    "paragraph_id": paragraph_id,
                    "source_id": passage.source_id,
                    "source_ordinal": passage.source_ordinal,
                    "role": evidence.role,
                })
            claim_id = sha256(
                f"{execution_run_id}:{EXTRACTION_VERSION}:{batch_id}:{index}:"
                f"{claim.kind}:{claim.statement}".encode()
            ).hexdigest()
            validated.append({
                "claim_id": claim_id,
                "kind": claim.kind,
                "statement": claim.statement,
                "evidence": references,
            })
        coverage = []
        for passage in packet:
            paragraph_id = paragraph_ids.get(
                (passage.source_ordinal, passage.paragraph_ordinal)
            )
            if paragraph_id is None:
                raise ValueError("matrix coverage has no persisted paragraph")
            coverage.append({
                "passage_id": passage.passage_id,
                "paragraph_id": paragraph_id,
                "source_id": passage.source_id,
                "source_ordinal": passage.source_ordinal,
            })
        await repository.save_matrix_batch(
            execution_run_id, batch_id, EXTRACTION_VERSION, tokens,
            coverage, validated,
        )
        existing.add(batch_id)
    except Exception as error:
        await repository.fail_matrix_batch(
            execution_run_id, batch_id, EXTRACTION_VERSION, tokens,
            str(error)[:2000],
        )
        splittable = (
            "MAX_TOKENS" in str(error).upper()
            or "TOO_LONG" in str(error).upper()
            or "matrix evidence is not in batch" in str(error)
            or "matrix evidence repair could not ground" in str(error)
        )
        if splittable:
            await repository.fail_matrix_batch(
                execution_run_id, batch_id, EXTRACTION_VERSION, tokens,
                "MATRIX_SPLIT: " + str(error)[:1900],
            )
            split_batches.add(batch_id)
            await _split_limited_packet(
                execution_run_id, brief, packet, titles, paragraph_ids,
                existing, split_batches, count_tokens, invoke, repository,
            )
            return
        raise


async def _split_limited_packet(
    execution_run_id: UUID,
    brief: EditorialBrief,
    packet: list[Passage],
    titles: dict[int, str | None],
    paragraph_ids: dict[tuple[int, int], str],
    existing: set[str],
    split_batches: set[str],
    count_tokens: Callable[[str], Awaitable[int]],
    invoke: Callable[[str], Awaitable[MatrixExtraction]],
    repository: object,
) -> None:
    if len(packet) > 1:
        middle = len(packet) // 2
        parts = (packet[:middle], packet[middle:])
    else:
        words = packet[0].text.split()
        if len(words) < 2:
            raise ValueError("matrix passage cannot be split further")
        middle = len(words) // 2
        parts = (
            [packet[0].model_copy(update={"text": " ".join(words[:middle])})],
            [packet[0].model_copy(update={"text": " ".join(words[middle:])})],
        )
    for part in parts:
        await _extract_packet(
            execution_run_id, brief, part, titles, paragraph_ids,
            existing, split_batches, count_tokens, invoke, repository,
        )
