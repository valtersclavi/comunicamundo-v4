"""Durable planning orchestrator: request validation → source_chunks_ready."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Protocol, Sequence
from uuid import UUID

from .contracts import (
    ChunkManifest,
    ChunkStatus,
    EditorialBrief,
    NormalizedSource,
    PlanningArtifactRefs,
    PlanningRequest,
    PlanningRun,
    PlanningRunState,
    PreparedChunk,
    SourceLineage,
)

CHUNK_SIZE_CHARS = 4000
CHUNK_OVERLAP_CHARS = 400
_BREAK_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def fingerprint_sources(sources: Sequence[NormalizedSource]) -> str:
    payload = [
        {
            "source_id": str(source.source_id),
            "content_sha256": _sha256_text(source.text),
            "language": source.language,
            "title": source.title or "",
            "metadata": source.metadata,
        }
        for source in sources
    ]
    return _sha256_text(_canonical_json(payload))


def fingerprint_brief(brief: EditorialBrief) -> str:
    return _sha256_text(_canonical_json(brief.model_dump(mode="json")))


def fingerprint_request(*, project_id: UUID, source_fingerprint: str, brief_fingerprint: str) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "project_id": str(project_id),
                "source_fingerprint": source_fingerprint,
                "brief_fingerprint": brief_fingerprint,
            }
        )
    )


def _break_length(window: str) -> int:
    if len(window) < 2:
        return len(window)
    floor = max(1, len(window) // 2)
    for separator in _BREAK_SEPARATORS:
        index = window.rfind(separator)
        if index >= floor:
            return index + len(separator)
    return len(window)


def split_source_text(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        raise ValueError("source text cannot be empty")
    if len(normalized) <= chunk_size:
        return [normalized]
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            end = start + max(_break_length(normalized[start:end]), 1)
        chunks.append(normalized[start:end])
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def prepare_source_chunks(sources: Sequence[NormalizedSource]) -> list[PreparedChunk]:
    if not sources:
        raise ValueError("at least one normalized source is required")
    prepared: list[PreparedChunk] = []
    sequence = 0
    for source_ordinal, source in enumerate(sources):
        pieces = split_source_text(source.text)
        for chunk_ordinal, text in enumerate(pieces):
            content_sha256 = _sha256_text(text)
            chunk_id = _sha256_text(f"{source.source_id}|{source_ordinal}|{chunk_ordinal}|{content_sha256}")
            prepared.append(
                PreparedChunk(
                    chunk_id=chunk_id,
                    source_id=source.source_id,
                    source_ordinal=source_ordinal,
                    chunk_ordinal=chunk_ordinal,
                    sequence=sequence,
                    text=text,
                    content_sha256=content_sha256,
                    status=ChunkStatus.PREPARED,
                )
            )
            sequence += 1
    return prepared


class PlanningPersistence(Protocol):
    def save_run(self, run: PlanningRun) -> PlanningRun: ...
    def get_run(self, planning_run_id: UUID) -> PlanningRun | None: ...
    def find_run(self, project_id: UUID, request_fingerprint: str) -> PlanningRun | None: ...
    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage: ...
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def find_source_lineage(self, project_id: UUID, source_fingerprint: str) -> SourceLineage | None: ...
    def persist_request(self, run: PlanningRun, request: PlanningRequest) -> str: ...
    def persist_chunks(self, lineage: SourceLineage, chunks: list[PreparedChunk]) -> str: ...
    def load_chunk_manifest(self, uri: str) -> ChunkManifest: ...
    def load_chunk(self, uri: str) -> PreparedChunk: ...


class PlanningOrchestrator:
    """Advance a PlanningRun through implemented stages only.

    Entrega 1 stops at source_chunks_ready. Later stages are contract-only.
    SourceLineage owns reusable chunk artifacts. PlanningRun is the editorial request.
    """

    terminal_state = PlanningRunState.SOURCE_CHUNKS_READY

    def __init__(self, persistence: PlanningPersistence) -> None:
        self._persistence = persistence

    def execute(self, request: PlanningRequest) -> PlanningRun:
        run = self._ensure_request_validated(request)
        return self._ensure_source_chunks_ready(run, request)

    def _ensure_request_validated(self, request: PlanningRequest) -> PlanningRun:
        source_fingerprint = fingerprint_sources(request.sources)
        brief_fingerprint = fingerprint_brief(request.brief)
        request_fingerprint = fingerprint_request(
            project_id=request.project_id,
            source_fingerprint=source_fingerprint,
            brief_fingerprint=brief_fingerprint,
        )
        existing = self._persistence.find_run(request.project_id, request_fingerprint)
        if existing is not None:
            return existing
        lineage = self._persistence.find_source_lineage(request.project_id, source_fingerprint)
        if lineage is None:
            lineage = self._persistence.save_source_lineage(
                SourceLineage(project_id=request.project_id, source_fingerprint=source_fingerprint)
            )
        run = PlanningRun(
            project_id=request.project_id,
            source_lineage_id=lineage.source_lineage_id,
            state=PlanningRunState.REQUEST_VALIDATED,
            source_fingerprint=source_fingerprint,
            brief_fingerprint=brief_fingerprint,
            request_fingerprint=request_fingerprint,
        )
        request_uri = self._persistence.persist_request(run, request)
        return self._persistence.save_run(
            run.model_copy(update={"artifacts": PlanningArtifactRefs(request_uri=request_uri), "updated_at": _now()})
        )

    def _ensure_source_chunks_ready(self, run: PlanningRun, request: PlanningRequest) -> PlanningRun:
        if run.state is not PlanningRunState.REQUEST_VALIDATED:
            return run
        lineage = self._persistence.get_source_lineage(run.source_lineage_id)
        if lineage is None:
            raise ValueError("PlanningRun points to a missing SourceLineage")
        try:
            if lineage.chunk_manifest_uri is None:
                chunks = prepare_source_chunks(request.sources)
                manifest_uri = self._persistence.persist_chunks(lineage, chunks)
                lineage = self._persistence.save_source_lineage(
                    lineage.model_copy(
                        update={
                            "chunk_manifest_uri": manifest_uri,
                            "chunk_count": len(chunks),
                            "updated_at": _now(),
                        }
                    )
                )
            return self._persistence.save_run(
                run.model_copy(
                    update={
                        "state": PlanningRunState.SOURCE_CHUNKS_READY,
                        "artifacts": run.artifacts.model_copy(update={"chunk_manifest_uri": lineage.chunk_manifest_uri}),
                        "chunk_count": lineage.chunk_count,
                        "error": None,
                        "updated_at": _now(),
                    }
                )
            )
        except Exception as exc:
            self._persistence.save_run(run.model_copy(update={"error": str(exc), "updated_at": _now()}))
            raise
