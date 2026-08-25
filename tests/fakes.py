from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from comunicamundo_v4.hefesto.contracts import (
    ChunkManifest,
    ChunkManifestEntry,
    GraphExtraction,
    GraphExtractionManifest,
    PlanningRequest,
    PlanningRun,
    PreparedChunk,
    SourceLineage,
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
        self.intents: dict[tuple[UUID, str], UUID] = {}
        self.lineages: dict[UUID, SourceLineage] = {}
        self.lineage_keys: dict[tuple[UUID, str], UUID] = {}
        self.objects: dict[str, object] = {}
        self.request_writes = 0
        self.chunk_writes = 0
        self.manifest_writes = 0
        self.extraction_writes = 0
        self.extraction_manifest_writes = 0

    def save_run(self, run: PlanningRun) -> PlanningRun:
        updated = run.model_copy(update={"updated_at": datetime.now(UTC)})
        self.runs[updated.planning_run_id] = updated
        self.intents[(updated.project_id, updated.request_fingerprint)] = updated.planning_run_id
        return updated

    def get_run(self, planning_run_id: UUID) -> PlanningRun | None:
        stored = self.runs.get(planning_run_id)
        return None if stored is None else stored.model_copy()

    def find_run(self, project_id: UUID, request_fingerprint: str) -> PlanningRun | None:
        run_id = self.intents.get((project_id, request_fingerprint))
        if run_id is None:
            return None
        return self.get_run(run_id)

    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage:
        updated = lineage.model_copy(update={"updated_at": datetime.now(UTC)})
        self.lineages[updated.source_lineage_id] = updated
        self.lineage_keys[(updated.project_id, updated.source_fingerprint)] = updated.source_lineage_id
        return updated

    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None:
        stored = self.lineages.get(source_lineage_id)
        return None if stored is None else stored.model_copy()

    def find_source_lineage(self, project_id: UUID, source_fingerprint: str) -> SourceLineage | None:
        lineage_id = self.lineage_keys.get((project_id, source_fingerprint))
        if lineage_id is None:
            return None
        return self.get_source_lineage(lineage_id)

    def persist_request(self, run: PlanningRun, request: PlanningRequest) -> str:
        self.request_writes += 1
        uri = f"gs://memory/{run.planning_run_id}/request.json"
        self.objects[uri] = request.model_copy()
        return uri

    def persist_chunks(self, lineage: SourceLineage, chunks: list[PreparedChunk]) -> str:
        if lineage.chunk_manifest_uri is not None:
            raise ValueError("source lineage chunks already persisted")
        self.chunk_writes += 1
        entries: list[ChunkManifestEntry] = []
        for chunk in chunks:
            chunk_uri = f"gs://memory/sources/{lineage.source_lineage_id}/chunks/{chunk.chunk_id}.json"
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
            source_lineage_id=lineage.source_lineage_id,
            project_id=lineage.project_id,
            source_fingerprint=lineage.source_fingerprint,
            chunk_count=len(entries),
            chunks=entries,
        )
        uri = f"gs://memory/sources/{lineage.source_lineage_id}/chunks/manifest.json"
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

    def persist_graph_extraction(self, lineage: SourceLineage, extraction: GraphExtraction) -> str:
        self.extraction_writes += 1
        uri = f"gs://memory/sources/{lineage.source_lineage_id}/graph-extractions/{extraction.chunk_id}.json"
        self.objects[uri] = extraction.model_copy()
        return uri

    def persist_graph_extraction_manifest(self, lineage: SourceLineage, manifest: GraphExtractionManifest) -> str:
        self.extraction_manifest_writes += 1
        uri = f"gs://memory/sources/{lineage.source_lineage_id}/graph-extractions/manifest.json"
        self.objects[uri] = manifest.model_copy()
        return uri

    def load_graph_extraction_manifest(self, uri: str) -> GraphExtractionManifest:
        manifest = self.objects[uri]
        assert isinstance(manifest, GraphExtractionManifest)
        return manifest.model_copy()

    def load_graph_extraction(self, uri: str) -> GraphExtraction:
        extraction = self.objects[uri]
        assert isinstance(extraction, GraphExtraction)
        return extraction.model_copy()
