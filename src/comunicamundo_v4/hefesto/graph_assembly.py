"""Durable PropertyGraph assembly/indexing for a SourceLineage at graph_extractions_ready."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, Sequence
from uuid import UUID

from .contracts import (
    GRAPH_ASSEMBLY_CONTRACT_VERSION,
    GRAPH_INDEXER_VERSION,
    GraphArtifact,
    GraphExtraction,
    GraphExtractionManifest,
    GraphExtractionUnitStatus,
    GraphReadyManifest,
    SourceLineage,
    SourceLineageState,
)
from .graph import ProjectGraph
from .graph_extraction import graph_extraction_to_llama
from .runtime import GraphIndexingRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extraction_manifest_identity_hash(manifest: GraphExtractionManifest) -> str:
    payload = {
        "source_lineage_id": str(manifest.source_lineage_id),
        "source_fingerprint": manifest.source_fingerprint,
        "contract_version": manifest.contract_version,
        "extractor_version": manifest.extractor_version,
        "model": manifest.model,
        "entries": [
            {
                "chunk_id": entry.chunk_id,
                "content_sha256": entry.content_sha256,
                "extraction_uri": entry.extraction_uri,
                "contract_version": entry.contract_version,
                "extractor_version": entry.extractor_version,
                "model": entry.model,
                "status": entry.status,
            }
            for entry in manifest.entries
        ],
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class GraphAssemblyPersistence(Protocol):
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage: ...
    def load_graph_extraction_manifest(self, uri: str) -> GraphExtractionManifest: ...
    def load_graph_extraction(self, uri: str) -> GraphExtraction: ...
    def persist_source_lineage_graph(self, lineage: SourceLineage, directory: Path) -> GraphArtifact: ...
    def restore_source_lineage_graph(self, artifact: GraphArtifact, destination: Path) -> None: ...
    def persist_graph_ready_manifest(self, lineage: SourceLineage, manifest: GraphReadyManifest) -> str: ...
    def load_graph_ready_manifest(self, uri: str) -> GraphReadyManifest: ...


class GraphAssemblyOrchestrator:
    """Advance a SourceLineage from graph_extractions_ready to graph_ready."""

    def __init__(
        self,
        persistence: GraphAssemblyPersistence,
        runtime: GraphIndexingRuntime,
        *,
        contract_version: str = GRAPH_ASSEMBLY_CONTRACT_VERSION,
        indexer_version: str = GRAPH_INDEXER_VERSION,
    ) -> None:
        self._persistence = persistence
        self._runtime = runtime
        self._contract_version = contract_version
        self._indexer_version = indexer_version

    def execute(self, source_lineage_id: UUID) -> SourceLineage:
        lineage = self._persistence.get_source_lineage(source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {source_lineage_id}")
        if lineage.graph_extractions_manifest_uri is None:
            raise ValueError("SourceLineage does not have persisted graph extractions")
        extraction_manifest = self._persistence.load_graph_extraction_manifest(lineage.graph_extractions_manifest_uri)
        self._assert_extractions_ready(lineage, extraction_manifest)
        identity_hash = extraction_manifest_identity_hash(extraction_manifest)
        if self._reusable(lineage, extraction_manifest, identity_hash):
            return lineage
        lineage = self._mark_indexing(lineage)
        extractions = self._load_extractions(lineage, extraction_manifest)
        try:
            graph = ProjectGraph.from_extractions(extractions, embed_model=self._runtime.embed_model)
            with TemporaryDirectory(prefix="hefesto-v4-graph-ready-") as temporary:
                persist_dir = Path(temporary) / "graph"
                graph.persist(persist_dir)
                artifact = self._persistence.persist_source_lineage_graph(lineage, persist_dir)
                reload_dir = Path(temporary) / "reload"
                self._persistence.restore_source_lineage_graph(artifact, reload_dir)
                reloaded = ProjectGraph.load(reload_dir, embed_model=self._runtime.embed_model)
                self._assert_reloaded(reloaded, extractions)
            manifest = GraphReadyManifest(
                contract_version=self._contract_version,
                indexer_version=self._indexer_version,
                source_lineage_id=lineage.source_lineage_id,
                project_id=lineage.project_id,
                source_fingerprint=lineage.source_fingerprint,
                extraction_manifest_uri=lineage.graph_extractions_manifest_uri,
                extraction_manifest_hash=identity_hash,
                extraction_contract_version=extraction_manifest.contract_version,
                extraction_extractor_version=extraction_manifest.extractor_version,
                extraction_model=extraction_manifest.model,
                embedding_model=self._runtime.model,
                embedding_package_version=self._runtime.package_version,
                embedding_config=dict(self._runtime.config),
                graph_artifact_id=artifact.artifact_id,
                graph_artifact_version=self._contract_version,
                graph_artifact=artifact,
            )
            uri = self._persistence.persist_graph_ready_manifest(lineage, manifest)
            return self._persistence.save_source_lineage(
                lineage.model_copy(
                    update={
                        "state": SourceLineageState.GRAPH_READY,
                        "graph_manifest_uri": uri,
                        "graph_artifact_id": artifact.artifact_id,
                        "updated_at": _now(),
                    }
                )
            )
        except Exception:
            self._persistence.save_source_lineage(
                lineage.model_copy(update={"state": SourceLineageState.GRAPH_INDEXING_IN_PROGRESS, "updated_at": _now()})
            )
            raise

    def _assert_extractions_ready(self, lineage: SourceLineage, manifest: GraphExtractionManifest) -> None:
        if manifest.source_lineage_id != lineage.source_lineage_id:
            raise ValueError("graph extraction manifesto does not belong to this SourceLineage")
        if manifest.source_fingerprint != lineage.source_fingerprint:
            raise ValueError("graph extraction manifesto source fingerprint mismatch")
        if not manifest.entries:
            raise ValueError("graph extraction manifesto is empty")
        incomplete = [entry.chunk_id for entry in manifest.entries if entry.status is not GraphExtractionUnitStatus.EXTRACTED or entry.extraction_uri is None]
        if incomplete:
            raise ValueError("SourceLineage graph extractions are incomplete")

    def _load_extractions(self, lineage: SourceLineage, manifest: GraphExtractionManifest) -> list[GraphExtraction]:
        loaded: list[GraphExtraction] = []
        for entry in manifest.entries:
            extraction = self._persistence.load_graph_extraction(entry.extraction_uri)
            if extraction.source_lineage_id != lineage.source_lineage_id:
                raise ValueError("persisted graph extraction belongs to another SourceLineage")
            if extraction.chunk_id != entry.chunk_id:
                raise ValueError("persisted graph extraction chunk_id does not match the manifesto")
            if extraction.chunk_sha256 != entry.content_sha256:
                raise ValueError("persisted graph extraction hash does not match the manifesto")
            loaded.append(extraction)
        return loaded

    def _mark_indexing(self, lineage: SourceLineage) -> SourceLineage:
        return self._persistence.save_source_lineage(
            lineage.model_copy(
                update={
                    "state": SourceLineageState.GRAPH_INDEXING_IN_PROGRESS,
                    "updated_at": _now(),
                }
            )
        )

    def _compatible(self, manifest: GraphReadyManifest, lineage: SourceLineage, extraction_manifest: GraphExtractionManifest, identity_hash: str) -> bool:
        return (
            manifest.source_lineage_id == lineage.source_lineage_id
            and manifest.source_fingerprint == lineage.source_fingerprint
            and manifest.extraction_manifest_uri == lineage.graph_extractions_manifest_uri
            and manifest.extraction_manifest_hash == identity_hash
            and manifest.extraction_contract_version == extraction_manifest.contract_version
            and manifest.extraction_extractor_version == extraction_manifest.extractor_version
            and manifest.extraction_model == extraction_manifest.model
            and manifest.embedding_model == self._runtime.model
            and manifest.embedding_package_version == self._runtime.package_version
            and manifest.embedding_config == dict(self._runtime.config)
            and manifest.contract_version == self._contract_version
            and manifest.indexer_version == self._indexer_version
            and manifest.graph_artifact_version == self._contract_version
        )

    def _reusable(self, lineage: SourceLineage, extraction_manifest: GraphExtractionManifest, identity_hash: str) -> bool:
        if lineage.state is not SourceLineageState.GRAPH_READY or lineage.graph_manifest_uri is None or lineage.graph_artifact_id is None:
            return False
        try:
            manifest = self._persistence.load_graph_ready_manifest(lineage.graph_manifest_uri)
            if not self._compatible(manifest, lineage, extraction_manifest, identity_hash):
                return False
            if manifest.graph_artifact.artifact_id != lineage.graph_artifact_id:
                return False
            with TemporaryDirectory(prefix="hefesto-v4-graph-ready-check-") as temporary:
                destination = Path(temporary) / "reload"
                self._persistence.restore_source_lineage_graph(manifest.graph_artifact, destination)
                reloaded = ProjectGraph.load(destination, embed_model=self._runtime.embed_model)
                extractions = self._load_extractions(lineage, extraction_manifest)
                self._assert_reloaded(reloaded, extractions)
        except Exception:
            return False
        return True

    def _assert_reloaded(self, graph: ProjectGraph, extractions: Sequence[GraphExtraction]) -> None:
        expected_entities: set[str] = set()
        expected_relations: set[tuple[str, str, str]] = set()
        for extraction in extractions:
            entities, relations = graph_extraction_to_llama(extraction)
            expected_entities.update(entity.id for entity in entities)
            expected_relations.update((item.source_id, item.label, item.target_id) for item in relations)
        actual_entities = {node.id for node in graph.entity_nodes()}
        actual_relations = {(item.source_id, item.label, item.target_id) for item in graph.relations()}
        if not expected_entities <= actual_entities:
            raise ValueError("reloaded PropertyGraph is missing entities from persisted extractions")
        if not expected_relations <= actual_relations:
            raise ValueError("reloaded PropertyGraph is missing relations from persisted extractions")
