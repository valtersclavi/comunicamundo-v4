from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from comunicamundo_v4.hefesto.contracts import (
    ChunkManifest,
    ChunkManifestEntry,
    PlanningRequest,
    PlanningRun,
    PreparedChunk,
)


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        if self._data is None:
            return None
        return json.loads(json.dumps(self._data))


class FakeDocument:
    def __init__(self, store: dict[tuple[str, str], dict[str, Any]], collection: str, doc_id: str) -> None:
        self._store = store
        self._key = (collection, doc_id)

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = json.loads(json.dumps(data))

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._store.get(self._key))


class FakeCollection:
    def __init__(self, store: dict[tuple[str, str], dict[str, Any]], name: str) -> None:
        self._store = store
        self._name = name

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self._store, self._name, doc_id)


class FakeFirestore:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict[str, Any]] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self._store, name)


class FakeBlob:
    def __init__(self) -> None:
        self._data = b""

    def upload_from_string(self, data: bytes | str, content_type: str | None = None) -> None:
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")

    def download_as_bytes(self) -> bytes:
        return self._data


class FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob())


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


class MemoryPlanningPersistence:
    def __init__(self) -> None:
        self.runs: dict[UUID, PlanningRun] = {}
        self.lineages: dict[tuple[UUID, str], UUID] = {}
        self.objects: dict[str, object] = {}
        self.request_writes = 0
        self.chunk_writes = 0
        self.manifest_writes = 0

    def save_run(self, run: PlanningRun) -> PlanningRun:
        updated = run.model_copy(update={"updated_at": datetime.now(UTC)})
        self.runs[updated.planning_run_id] = updated
        self.lineages[(updated.project_id, updated.source_fingerprint)] = updated.planning_run_id
        return updated

    def get_run(self, planning_run_id: UUID) -> PlanningRun | None:
        stored = self.runs.get(planning_run_id)
        return None if stored is None else stored.model_copy()

    def find_run(self, project_id: UUID, source_fingerprint: str) -> PlanningRun | None:
        run_id = self.lineages.get((project_id, source_fingerprint))
        if run_id is None:
            return None
        return self.get_run(run_id)

    def persist_request(self, run: PlanningRun, request: PlanningRequest) -> str:
        self.request_writes += 1
        uri = f"gs://memory/{run.planning_run_id}/request.json"
        self.objects[uri] = request.model_copy()
        return uri

    def persist_chunks(self, run: PlanningRun, chunks: list[PreparedChunk]) -> str:
        self.chunk_writes += 1
        entries: list[ChunkManifestEntry] = []
        for chunk in chunks:
            chunk_uri = f"gs://memory/{run.planning_run_id}/chunks/{chunk.chunk_id}.json"
            self.objects[chunk_uri] = chunk.model_copy()
            entries.append(
                ChunkManifestEntry(
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    source_ordinal=chunk.source_ordinal,
                    chunk_ordinal=chunk.chunk_ordinal,
                    sequence=chunk.sequence,
                    content_sha256=chunk.content_sha256,
                    status=chunk.status,
                    chunk_uri=chunk_uri,
                    extraction_ref=chunk.extraction_ref,
                )
            )
        manifest = ChunkManifest(
            planning_run_id=run.planning_run_id,
            project_id=run.project_id,
            source_fingerprint=run.source_fingerprint,
            chunk_count=len(entries),
            chunks=entries,
        )
        uri = f"gs://memory/{run.planning_run_id}/chunks/manifest.json"
        self.objects[uri] = manifest
        self.manifest_writes += 1
        return uri

    def load_chunk_manifest(self, uri: str) -> ChunkManifest:
        manifest = self.objects[uri]
        assert isinstance(manifest, ChunkManifest)
        return manifest.model_copy()

    def load_chunk(self, uri: str) -> PreparedChunk:
        chunk = self.objects[uri]
        assert isinstance(chunk, PreparedChunk)
        return chunk.model_copy()
