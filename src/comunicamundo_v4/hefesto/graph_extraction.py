"""Durable per-chunk graph extraction for a prepared SourceLineage."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from llama_index.core.graph_stores.types import EntityNode, Relation
from llama_index.core.indices.property_graph.utils import default_parse_triplets_fn
from llama_index.core.prompts.default_prompts import DEFAULT_KG_TRIPLET_EXTRACT_PROMPT
from llama_index.core.schema import MetadataMode, TextNode

from .contracts import (
    GRAPH_EXTRACTION_CONTRACT_VERSION,
    GRAPH_EXTRACTOR_VERSION,
    ChunkManifest,
    GraphEntity,
    GraphExtraction,
    GraphExtractionManifest,
    GraphExtractionManifestEntry,
    GraphExtractionUnit,
    GraphExtractionUnitStatus,
    GraphRelation,
    PreparedChunk,
    SourceLineage,
    SourceLineageState,
)
from .runtime import GraphExtractionRuntime

MAX_PATHS_PER_CHUNK = 10

GRAPH_EXTRACTION_IDENTITY_FIELDS = (
    "source_lineage_id",
    "source_id",
    "chunk_id",
    "chunk_sha256",
    "contract_version",
    "extractor_version",
    "model",
)


def _now() -> datetime:
    return datetime.now(UTC)


class GraphExtractionIdentityError(ValueError):
    """Raised when a GraphExtraction does not belong to the requested unit and contract."""


class ChunkGraphExtractor(Protocol):
    def extract(self, unit: GraphExtractionUnit) -> GraphExtraction: ...


class GraphExtractionPersistence(Protocol):
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage: ...
    def load_chunk_manifest(self, uri: str) -> ChunkManifest: ...
    def load_chunk(self, uri: str) -> PreparedChunk: ...
    def persist_graph_extraction(self, lineage: SourceLineage, extraction: GraphExtraction) -> str: ...
    def persist_graph_extraction_manifest(self, lineage: SourceLineage, manifest: GraphExtractionManifest) -> str: ...
    def load_graph_extraction_manifest(self, uri: str) -> GraphExtractionManifest: ...
    def load_graph_extraction(self, uri: str) -> GraphExtraction: ...


def graph_extraction_to_llama(extraction: GraphExtraction) -> tuple[list[EntityNode], list[Relation]]:
    """Convert a persisted GraphExtraction into LlamaIndex graph objects without an LLM."""
    provenance = {
        "chunk_id": extraction.chunk_id,
        "source_id": str(extraction.source_id),
        "source_lineage_id": str(extraction.source_lineage_id),
    }
    entities = [
        EntityNode(name=entity.name, label=entity.label, properties={**entity.properties, **provenance})
        for entity in extraction.entities
    ]
    by_name = {entity.name: entity for entity in entities}
    relations: list[Relation] = []
    for item in extraction.relations:
        source = by_name.get(item.source)
        target = by_name.get(item.target)
        relations.append(
            Relation(
                label=item.relation,
                source_id=source.id if source is not None else item.source.replace('"', " "),
                target_id=target.id if target is not None else item.target.replace('"', " "),
                properties={**item.properties, **provenance},
            )
        )
    return entities, relations


class LlamaIndexChunkGraphExtractor:
    """Thin adapter over LlamaIndex's pinned one-node path extractor."""

    def __init__(self, runtime: GraphExtractionRuntime) -> None:
        if runtime.context_window <= 0 or runtime.max_tokens <= 0:
            raise ValueError("graph extraction runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def extract(self, unit: GraphExtractionUnit) -> GraphExtraction:
        node = TextNode(
            text=unit.text,
            id_=unit.chunk_id,
            metadata={
                "chunk_id": unit.chunk_id,
                "source_id": str(unit.source_id),
                "source_lineage_id": str(unit.source_lineage_id),
            },
        )
        llm_response = self._runtime.llm.predict(
            DEFAULT_KG_TRIPLET_EXTRACT_PROMPT,
            text=node.get_content(metadata_mode=MetadataMode.NONE),
            max_knowledge_triplets=MAX_PATHS_PER_CHUNK,
        )
        triples = default_parse_triplets_fn(llm_response)
        provenance = {
            "chunk_id": unit.chunk_id,
            "source_id": str(unit.source_id),
            "source_lineage_id": str(unit.source_lineage_id),
        }
        entities: list[GraphEntity] = []
        seen: set[str] = set()
        relations: list[GraphRelation] = []
        for subject, relation, obj in triples:
            for name in (subject, obj):
                if name in seen:
                    continue
                seen.add(name)
                entities.append(GraphEntity(name=name, label="entity", properties=dict(provenance)))
            relations.append(GraphRelation(source=subject, relation=relation, target=obj, properties=dict(provenance)))
        return GraphExtraction(
            contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
            extractor_version=GRAPH_EXTRACTOR_VERSION,
            model=self._runtime.model,
            source_lineage_id=unit.source_lineage_id,
            source_id=unit.source_id,
            chunk_id=unit.chunk_id,
            chunk_sha256=unit.content_sha256,
            entities=entities,
            relations=relations,
        )


class GraphExtractionOrchestrator:
    """Advance a SourceLineage from prepared chunks to graph_extractions_ready."""

    def __init__(
        self,
        persistence: GraphExtractionPersistence,
        extractor: ChunkGraphExtractor,
        *,
        model: str,
        contract_version: str = GRAPH_EXTRACTION_CONTRACT_VERSION,
        extractor_version: str = GRAPH_EXTRACTOR_VERSION,
    ) -> None:
        self._persistence = persistence
        self._extractor = extractor
        self._model = model
        self._contract_version = contract_version
        self._extractor_version = extractor_version

    def execute(self, source_lineage_id: UUID) -> SourceLineage:
        lineage = self._persistence.get_source_lineage(source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {source_lineage_id}")
        if lineage.chunk_manifest_uri is None:
            raise ValueError("SourceLineage does not have prepared chunks")
        chunk_manifest = self._persistence.load_chunk_manifest(lineage.chunk_manifest_uri)
        manifest = self._reconcile(lineage, chunk_manifest)
        lineage = self._checkpoint(lineage, manifest, allow_ready=False)
        for index, entry in enumerate(list(manifest.entries)):
            chunk = self._persistence.load_chunk(entry.chunk_uri)
            unit = self._unit_for(lineage, chunk)
            if self._reusable(entry, unit):
                continue
            try:
                extraction = self._extractor.extract(unit)
                self._assert_identity(unit, extraction)
                extraction_uri = self._persistence.persist_graph_extraction(lineage, extraction)
                manifest.entries[index] = entry.model_copy(
                    update={
                        "status": GraphExtractionUnitStatus.EXTRACTED,
                        "content_sha256": chunk.content_sha256,
                        "extraction_uri": extraction_uri,
                        "contract_version": extraction.contract_version,
                        "extractor_version": extraction.extractor_version,
                        "model": extraction.model,
                        "error": None,
                    }
                )
            except Exception as exc:
                manifest.entries[index] = entry.model_copy(
                    update={"status": GraphExtractionUnitStatus.FAILED, "error": str(exc)}
                )
                self._checkpoint(lineage, manifest, allow_ready=False)
                raise
            lineage = self._checkpoint(lineage, manifest, allow_ready=False)
        return self._checkpoint(lineage, manifest, allow_ready=True)

    def _unit_for(self, lineage: SourceLineage, chunk: PreparedChunk) -> GraphExtractionUnit:
        return GraphExtractionUnit(
            source_lineage_id=lineage.source_lineage_id,
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            text=chunk.text,
            content_sha256=chunk.content_sha256,
            source_ordinal=chunk.source_ordinal,
            chunk_ordinal=chunk.chunk_ordinal,
            sequence=chunk.sequence,
        )

    def _expected_identity(self, unit: GraphExtractionUnit) -> dict[str, object]:
        return {
            "source_lineage_id": unit.source_lineage_id,
            "source_id": unit.source_id,
            "chunk_id": unit.chunk_id,
            "chunk_sha256": unit.content_sha256,
            "contract_version": self._contract_version,
            "extractor_version": self._extractor_version,
            "model": self._model,
        }

    def _identity_mismatches(self, unit: GraphExtractionUnit, extraction: GraphExtraction) -> dict[str, tuple[object, object]]:
        expected = self._expected_identity(unit)
        mismatches: dict[str, tuple[object, object]] = {}
        for field in GRAPH_EXTRACTION_IDENTITY_FIELDS:
            actual = getattr(extraction, field)
            want = expected[field]
            if actual != want:
                mismatches[field] = (actual, want)
        return mismatches

    def _assert_identity(self, unit: GraphExtractionUnit, extraction: GraphExtraction) -> None:
        mismatches = self._identity_mismatches(unit, extraction)
        if not mismatches:
            return
        details = ", ".join(f"{field} got {actual!r} expected {expected!r}" for field, (actual, expected) in mismatches.items())
        raise GraphExtractionIdentityError(f"graph extraction does not belong to the requested unit: {details}")

    def _reusable(self, entry: GraphExtractionManifestEntry, unit: GraphExtractionUnit) -> bool:
        if entry.status is not GraphExtractionUnitStatus.EXTRACTED or entry.extraction_uri is None:
            return False
        try:
            extraction = self._persistence.load_graph_extraction(entry.extraction_uri)
        except Exception:
            return False
        return not self._identity_mismatches(unit, extraction)

    def _reconcile(self, lineage: SourceLineage, chunk_manifest: ChunkManifest) -> GraphExtractionManifest:
        existing: GraphExtractionManifest | None = None
        if lineage.graph_extractions_manifest_uri is not None:
            existing = self._persistence.load_graph_extraction_manifest(lineage.graph_extractions_manifest_uri)
        previous = {entry.chunk_id: entry for entry in existing.entries} if existing is not None else {}
        entries: list[GraphExtractionManifestEntry] = []
        for chunk in chunk_manifest.chunks:
            prior = previous.get(chunk.chunk_id)
            if prior is None:
                entries.append(
                    GraphExtractionManifestEntry(
                        chunk_id=chunk.chunk_id,
                        source_id=chunk.source_id,
                        content_sha256=chunk.content_sha256,
                        chunk_uri=chunk.chunk_uri,
                    )
                )
                continue
            entries.append(
                prior.model_copy(update={"chunk_uri": chunk.chunk_uri, "content_sha256": chunk.content_sha256, "source_id": chunk.source_id})
            )
        return GraphExtractionManifest(
            source_lineage_id=lineage.source_lineage_id,
            project_id=lineage.project_id,
            source_fingerprint=lineage.source_fingerprint,
            contract_version=self._contract_version,
            extractor_version=self._extractor_version,
            model=self._model,
            chunk_count=len(entries),
            extracted_count=sum(1 for entry in entries if entry.status is GraphExtractionUnitStatus.EXTRACTED),
            failed_count=sum(1 for entry in entries if entry.status is GraphExtractionUnitStatus.FAILED),
            entries=entries,
            created_at=existing.created_at if existing is not None else _now(),
            updated_at=_now(),
        )

    def _checkpoint(self, lineage: SourceLineage, manifest: GraphExtractionManifest, *, allow_ready: bool) -> SourceLineage:
        extracted = sum(1 for entry in manifest.entries if entry.status is GraphExtractionUnitStatus.EXTRACTED)
        failed = sum(1 for entry in manifest.entries if entry.status is GraphExtractionUnitStatus.FAILED)
        ready = allow_ready and extracted == len(manifest.entries) and failed == 0
        updated_manifest = manifest.model_copy(
            update={
                "extracted_count": extracted,
                "failed_count": failed,
                "chunk_count": len(manifest.entries),
                "updated_at": _now(),
            }
        )
        manifest.extracted_count = extracted
        manifest.failed_count = failed
        manifest.chunk_count = len(manifest.entries)
        manifest.updated_at = updated_manifest.updated_at
        uri = self._persistence.persist_graph_extraction_manifest(lineage, updated_manifest)
        state = SourceLineageState.GRAPH_EXTRACTIONS_READY if ready else SourceLineageState.GRAPH_EXTRACTIONS_IN_PROGRESS
        return self._persistence.save_source_lineage(
            lineage.model_copy(
                update={
                    "state": state,
                    "graph_extractions_manifest_uri": uri,
                    "chunk_count": updated_manifest.chunk_count,
                    "extracted_count": extracted,
                    "failed_count": failed,
                    "updated_at": _now(),
                }
            )
        )
