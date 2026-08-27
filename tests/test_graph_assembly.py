from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.graph_stores.types import EntityNode, Relation
from llama_index.core.indices.property_graph.transformations.simple_llm import SimpleLLMPathExtractor

from comunicamundo_v4.hefesto.contracts import (
    GRAPH_ASSEMBLY_CONTRACT_VERSION,
    GRAPH_EXTRACTION_CONTRACT_VERSION,
    GRAPH_EXTRACTOR_VERSION,
    GRAPH_INDEXER_VERSION,
    EditorialBrief,
    GraphEntity,
    GraphExtraction,
    GraphExtractionUnit,
    GraphRelation,
    NormalizedSource,
    PlanningRequest,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.graph_assembly import GraphAssemblyOrchestrator, extraction_manifest_identity_hash
from comunicamundo_v4.hefesto.graph_extraction import GraphExtractionOrchestrator
from comunicamundo_v4.hefesto.persistence import FirestoreEditorialRepository, GcsArtifactStore, GcpEditorialPersistence, GcpPersistenceSettings
from comunicamundo_v4.hefesto.planning import PlanningOrchestrator
from comunicamundo_v4.hefesto.runtime import GraphIndexingRuntime
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence

GRAPH_ASSEMBLY_PY = Path("src/comunicamundo_v4/hefesto/graph_assembly.py").read_text()
GRAPH_PY = Path("src/comunicamundo_v4/hefesto/graph.py").read_text()
FAKE_MODEL = "fake-graph-llm"
FAKE_EMBED = "fake-embed"


class RecordingEmbedding(MockEmbedding):
    def __init__(self) -> None:
        super().__init__(embed_dim=8, model_name=FAKE_EMBED)
        object.__setattr__(self, "_recorded", [])

    @property
    def texts(self) -> list[str]:
        return self._recorded

    def get_text_embedding_batch(self, texts, **kwargs):
        self._recorded.extend(list(texts))
        return super().get_text_embedding_batch(texts, **kwargs)


class FailingEmbedding(RecordingEmbedding):
    def get_text_embedding_batch(self, texts, **kwargs):
        self._recorded.extend(list(texts))
        raise RuntimeError("embedding failed")


class RecordingExtractor:
    def __init__(self) -> None:
        self.calls: list[GraphExtractionUnit] = []

    def extract(self, unit: GraphExtractionUnit) -> GraphExtraction:
        self.calls.append(unit)
        subject = f"Entidade-{unit.sequence}"
        return GraphExtraction(
            contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
            extractor_version=GRAPH_EXTRACTOR_VERSION,
            model=FAKE_MODEL,
            source_lineage_id=unit.source_lineage_id,
            source_id=unit.source_id,
            chunk_id=unit.chunk_id,
            chunk_sha256=unit.content_sha256,
            entities=[GraphEntity(name=subject), GraphEntity(name="Fonte")],
            relations=[GraphRelation(source=subject, relation="APARECE_EM", target="Fonte")],
        )


def _brief(**overrides) -> EditorialBrief:
    payload = {"theme": "Luto infantil", "approach": "sensível e prático", "duration_minutes": 8, **overrides}
    return EditorialBrief.model_validate(payload)


def _source(text: str, *, source_id=None) -> NormalizedSource:
    return NormalizedSource(source_id=source_id or uuid4(), text=text, language="pt-BR", title="Fonte")


def _request(*texts: str, project_id=None, brief: EditorialBrief | None = None, sources: list[NormalizedSource] | None = None) -> PlanningRequest:
    return PlanningRequest(
        project_id=project_id or uuid4(),
        brief=brief or _brief(),
        sources=sources or [_source(text) for text in texts],
    )


def _gcp_persistence() -> tuple[GcpEditorialPersistence, FakeStorageClient]:
    storage = FakeStorageClient()
    firestore = FakeFirestore()
    settings = GcpPersistenceSettings(project="test-project", bucket="test-bucket")
    persistence = GcpEditorialPersistence(
        artifacts=GcsArtifactStore(storage, bucket_name=settings.bucket, root_prefix=settings.root_prefix),
        repository=FirestoreEditorialRepository(
            firestore,
            axes_collection=settings.axes_collection,
            projects_collection=settings.projects_collection,
            articles_collection=settings.articles_collection,
            planning_runs_collection=settings.planning_runs_collection,
            planning_intents_collection=settings.planning_intents_collection,
            source_lineages_collection=settings.source_lineages_collection,
            source_lineage_keys_collection=settings.source_lineage_keys_collection,
        ),
    )
    return persistence, storage


def _runtime(embed: MockEmbedding | None = None) -> tuple[GraphIndexingRuntime, RecordingEmbedding | MockEmbedding]:
    embed_model = embed if embed is not None else RecordingEmbedding()
    return (
        GraphIndexingRuntime(
            embed_model=embed_model,
            model=FAKE_EMBED,
            package_version="test-embed-1",
            config={"model_name": FAKE_EMBED, "location": "us-central1", "embed_kg_nodes": True},
        ),
        embed_model,
    )


def _prepare_extracted(store: MemoryPlanningPersistence, *texts: str, brief: EditorialBrief | None = None, project_id=None, sources=None):
    request = _request(*texts, brief=brief, project_id=project_id, sources=sources)
    run = PlanningOrchestrator(store).execute(request)
    extractor = RecordingExtractor()
    lineage = GraphExtractionOrchestrator(store, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    return run, lineage, extractor


def test_assembly_module_does_not_construct_extraction_llm() -> None:
    forbidden = ("SimpleLLMPathExtractor", "from_documents", "GoogleGenAI(", "build_graph_extraction_runtime", "build_vertex_runtime", "LlamaIndexChunkGraphExtractor")
    for token in forbidden:
        assert token not in GRAPH_ASSEMBLY_PY
    assert "SimpleLLMPathExtractor" in GRAPH_PY
    assert "PersistedKgTransform" in GRAPH_PY
    assert "kg_extractors=persisted_kg_extractors()" in GRAPH_PY


def test_assembly_uses_persisted_extractions_and_does_not_call_extraction_llm() -> None:
    store = MemoryPlanningPersistence()
    extractor = RecordingExtractor()
    run = PlanningOrchestrator(store).execute(_request("chunk um da criança.", "chunk dois da família.", "chunk três da presença."))
    extracted = GraphExtractionOrchestrator(store, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    extract_calls = list(extractor.calls)
    extract_writes = store.extraction_writes
    runtime, embed = _runtime()
    result = GraphAssemblyOrchestrator(store, runtime).execute(extracted.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_READY
    assert result.graph_artifact_id is not None
    assert result.graph_manifest_uri is not None
    assert extractor.calls == extract_calls
    assert store.extraction_writes == extract_writes
    assert embed.texts
    manifest = store.load_graph_ready_manifest(result.graph_manifest_uri)
    assert manifest.contract_version == GRAPH_ASSEMBLY_CONTRACT_VERSION
    assert manifest.indexer_version == GRAPH_INDEXER_VERSION
    assert manifest.embedding_model == FAKE_EMBED
    assert manifest.extraction_manifest_hash == extraction_manifest_identity_hash(store.load_graph_extraction_manifest(extracted.graph_extractions_manifest_uri))
    assert manifest.graph_artifact.artifact_id == result.graph_artifact_id


def test_embedding_failure_keeps_extractions_and_stays_below_graph_ready() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, extractor = _prepare_extracted(store, "primeiro bloco.", "segundo bloco.", "terceiro bloco.")
    extract_writes = store.extraction_writes
    extract_calls = list(extractor.calls)
    runtime, embed = _runtime(FailingEmbedding())
    with pytest.raises(RuntimeError, match="embedding failed"):
        GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    updated = store.get_source_lineage(lineage.source_lineage_id)
    assert updated.state is SourceLineageState.GRAPH_INDEXING_IN_PROGRESS
    assert updated.state is not SourceLineageState.GRAPH_READY
    assert updated.graph_artifact_id is None
    assert updated.graph_manifest_uri is None
    assert updated.extracted_count == 3
    assert store.extraction_writes == extract_writes
    assert extractor.calls == extract_calls
    assert store.graph_writes == 0
    assert store.graph_manifest_writes == 0
    extraction_manifest = store.load_graph_extraction_manifest(updated.graph_extractions_manifest_uri)
    assert extraction_manifest.extracted_count == 3
    assert all(store.load_graph_extraction(entry.extraction_uri).chunk_id == entry.chunk_id for entry in extraction_manifest.entries)
    assert embed.texts


def test_rerun_after_indexing_failure_does_not_reextract() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, extractor = _prepare_extracted(store, "primeiro bloco.", "segundo bloco.", "terceiro bloco.")
    failing, _embed = _runtime(FailingEmbedding())
    with pytest.raises(RuntimeError, match="embedding failed"):
        GraphAssemblyOrchestrator(store, failing).execute(lineage.source_lineage_id)
    extract_calls = list(extractor.calls)
    extract_writes = store.extraction_writes
    runtime, embed = _runtime()
    result = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_READY
    assert extractor.calls == extract_calls
    assert store.extraction_writes == extract_writes
    assert embed.texts
    assert store.graph_writes == 1
    assert store.graph_manifest_writes == 1


def test_compatible_graph_ready_rerun_does_not_reindex() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, extractor = _prepare_extracted(store, "um.", "dois.", "três.")
    runtime, embed = _runtime()
    first = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    texts = list(embed.texts)
    writes = store.graph_writes
    manifest_writes = store.graph_manifest_writes
    extract_writes = store.extraction_writes
    second = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    assert first.state is SourceLineageState.GRAPH_READY
    assert second.state is SourceLineageState.GRAPH_READY
    assert second.source_lineage_id == first.source_lineage_id
    assert second.graph_artifact_id == first.graph_artifact_id
    assert embed.texts == texts
    assert store.graph_writes == writes
    assert store.graph_manifest_writes == manifest_writes
    assert store.extraction_writes == extract_writes
    assert extractor.calls


def test_different_brief_reuses_the_same_graph_ready_lineage() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    sources = [_source("fonte estável um."), _source("fonte estável dois."), _source("fonte estável três.")]
    first_run, lineage, extractor = _prepare_extracted(store, sources=sources, project_id=project_id)
    runtime, embed = _runtime()
    ready = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    texts = list(embed.texts)
    graph_writes = store.graph_writes
    extract_writes = store.extraction_writes
    second_run = PlanningOrchestrator(store).execute(_request(sources=sources, project_id=project_id, brief=_brief(theme="Outro recorte")))
    assert second_run.planning_run_id != first_run.planning_run_id
    assert second_run.source_lineage_id == first_run.source_lineage_id
    reused = GraphAssemblyOrchestrator(store, runtime).execute(second_run.source_lineage_id)
    assert reused.state is SourceLineageState.GRAPH_READY
    assert reused.source_lineage_id == ready.source_lineage_id
    assert reused.graph_artifact_id == ready.graph_artifact_id
    assert embed.texts == texts
    assert store.graph_writes == graph_writes
    assert store.extraction_writes == extract_writes
    assert len(extractor.calls) == 3


def test_persisted_graph_reloads_entities_and_relations_from_extractions() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, _extractor = _prepare_extracted(store, "A criança percebe a ausência.", "O adulto acompanha.")
    runtime, _embed = _runtime()
    ready = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    manifest = store.load_graph_ready_manifest(ready.graph_manifest_uri)
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temporary:
        destination = Path(temporary) / "reload"
        store.restore_source_lineage_graph(manifest.graph_artifact, destination)
        graph = ProjectGraph.load(destination, embed_model=runtime.embed_model)
    names = {node.name for node in graph.entity_nodes()}
    relations = {(item.source_id, item.label, item.target_id) for item in graph.relations()}
    assert "Entidade-0" in names
    assert "Entidade-1" in names
    assert "Fonte" in names
    assert ("Entidade-0", "APARECE_EM", "Fonte") in relations
    assert ("Entidade-1", "APARECE_EM", "Fonte") in relations
    assert all(isinstance(node, EntityNode) for node in graph.entity_nodes())
    assert all(isinstance(item, Relation) for item in graph.relations())


def test_from_extractions_rejects_empty_and_does_not_use_llm_extractor(monkeypatch) -> None:
    with pytest.raises(ValueError):
        ProjectGraph.from_extractions([], embed_model=RecordingEmbedding())

    def boom(*args, **kwargs):
        raise AssertionError("SimpleLLMPathExtractor must not be constructed")

    monkeypatch.setattr("llama_index.core.indices.property_graph.base.SimpleLLMPathExtractor", boom)
    extraction = GraphExtraction(
        contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
        extractor_version=GRAPH_EXTRACTOR_VERSION,
        model=FAKE_MODEL,
        source_lineage_id=uuid4(),
        source_id=uuid4(),
        chunk_id="a" * 64,
        chunk_sha256="b" * 64,
        entities=[GraphEntity(name="Criança"), GraphEntity(name="Ausência")],
        relations=[GraphRelation(source="Criança", relation="PERCEBE", target="Ausência")],
    )
    graph = ProjectGraph.from_extractions([extraction], embed_model=RecordingEmbedding())
    assert {node.name for node in graph.entity_nodes()} == {"Criança", "Ausência"}
    assert [(item.source_id, item.label, item.target_id) for item in graph.relations()] == [("Criança", "PERCEBE", "Ausência")]
    assert SimpleLLMPathExtractor is not None


def test_incompatible_embedding_identity_reindexes_without_reextraction() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, extractor = _prepare_extracted(store, "um.", "dois.", "três.")
    runtime, embed = _runtime()
    first = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    extract_writes = store.extraction_writes
    extract_calls = list(extractor.calls)
    changed = GraphIndexingRuntime(
        embed_model=RecordingEmbedding(),
        model="other-embed",
        package_version="test-embed-1",
        config={"model_name": "other-embed", "location": "us-central1", "embed_kg_nodes": True},
    )
    second = GraphAssemblyOrchestrator(store, changed).execute(lineage.source_lineage_id)
    assert second.state is SourceLineageState.GRAPH_READY
    assert second.graph_artifact_id != first.graph_artifact_id
    assert store.extraction_writes == extract_writes
    assert extractor.calls == extract_calls
    assert store.graph_writes == 2
    assert changed.embed_model.texts


def test_invalid_graph_artifact_resumes_indexing_without_deleting_extractions() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, extractor = _prepare_extracted(store, "um.", "dois.", "três.")
    runtime, _embed = _runtime()
    ready = GraphAssemblyOrchestrator(store, runtime).execute(lineage.source_lineage_id)
    manifest = store.load_graph_ready_manifest(ready.graph_manifest_uri)
    first_file = manifest.graph_artifact.files[0]
    store.objects[f"gs://memory/{manifest.graph_artifact.prefix}/files/{first_file.path}"] = b"corrupted"
    extract_writes = store.extraction_writes
    extract_calls = list(extractor.calls)
    resumed_runtime, embed = _runtime()
    resumed = GraphAssemblyOrchestrator(store, resumed_runtime).execute(lineage.source_lineage_id)
    assert resumed.state is SourceLineageState.GRAPH_READY
    assert resumed.graph_artifact_id != ready.graph_artifact_id
    assert store.extraction_writes == extract_writes
    assert extractor.calls == extract_calls
    assert embed.texts
    assert store.graph_writes == 2


def test_gcp_persists_graph_artifact_under_the_source_lineage() -> None:
    persistence, storage = _gcp_persistence()
    extractor = RecordingExtractor()
    run = PlanningOrchestrator(persistence).execute(_request("um.", "dois.", "três."))
    extracted = GraphExtractionOrchestrator(persistence, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    runtime, _embed = _runtime()
    result = GraphAssemblyOrchestrator(persistence, runtime).execute(extracted.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_READY
    names = list(storage.bucket("test-bucket").blobs)
    assert any(name.endswith("graph/manifest.json") for name in names)
    assert any("/sources/" in name and "/graph/" in name and "/files/" in name for name in names)
    loaded = persistence.load_graph_ready_manifest(result.graph_manifest_uri)
    assert loaded.graph_artifact_id == result.graph_artifact_id
    assert loaded.source_lineage_id == result.source_lineage_id
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temporary:
        destination = Path(temporary) / "reload"
        persistence.restore_source_lineage_graph(loaded.graph_artifact, destination)
        graph = ProjectGraph.load(destination, embed_model=runtime.embed_model)
    assert {node.name for node in graph.entity_nodes()} >= {"Entidade-0", "Entidade-1", "Entidade-2", "Fonte"}
    extraction_names = [name for name in names if "/graph-extractions/" in name]
    assert len([name for name in extraction_names if name.endswith(".json")]) == 4
