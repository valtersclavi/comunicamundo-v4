from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from llama_index.core.graph_stores.types import EntityNode, Relation

from comunicamundo_v4.hefesto.contracts import (
    GRAPH_EXTRACTION_CONTRACT_VERSION,
    GRAPH_EXTRACTOR_VERSION,
    EditorialBrief,
    GraphEntity,
    GraphExtraction,
    GraphExtractionUnit,
    GraphExtractionUnitStatus,
    GraphRelation,
    NormalizedSource,
    PlanningRequest,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.graph_extraction import (
    GraphExtractionOrchestrator,
    LlamaIndexChunkGraphExtractor,
    graph_extraction_to_llama,
)
from comunicamundo_v4.hefesto.persistence import FirestoreEditorialRepository, GcsArtifactStore, GcpEditorialPersistence, GcpPersistenceSettings
from comunicamundo_v4.hefesto.planning import PlanningOrchestrator
from comunicamundo_v4.hefesto.runtime import GraphExtractionRuntime, VertexRuntimeSettings, build_graph_extraction_runtime
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence

GRAPH_EXTRACTION_PY = Path("src/comunicamundo_v4/hefesto/graph_extraction.py").read_text()
FAKE_MODEL = "fake-graph-llm"


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


def _extraction_for(unit: GraphExtractionUnit, *, model: str = FAKE_MODEL, extractor_version: str = GRAPH_EXTRACTOR_VERSION) -> GraphExtraction:
    subject = f"Entidade-{unit.sequence}"
    return GraphExtraction(
        contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
        extractor_version=extractor_version,
        model=model,
        source_lineage_id=unit.source_lineage_id,
        source_id=unit.source_id,
        chunk_id=unit.chunk_id,
        chunk_sha256=unit.content_sha256,
        entities=[GraphEntity(name=subject, label="entity", properties={"sequence": unit.sequence})],
        relations=[GraphRelation(source=subject, relation="APARECE_EM", target="Fonte")],
    )


class RecordingExtractor:
    def __init__(self, *, fail_on: set[str] | None = None, model: str = FAKE_MODEL) -> None:
        self.calls: list[GraphExtractionUnit] = []
        self.fail_on = set(fail_on or [])
        self.model = model

    def extract(self, unit: GraphExtractionUnit) -> GraphExtraction:
        assert isinstance(unit, GraphExtractionUnit)
        self.calls.append(unit)
        if unit.chunk_id in self.fail_on:
            raise RuntimeError(f"extraction failed for {unit.chunk_id}")
        return _extraction_for(unit, model=self.model)

    @property
    def chunk_ids(self) -> list[str]:
        return [unit.chunk_id for unit in self.calls]


def _prepare_lineage(store: MemoryPlanningPersistence, *texts: str, brief: EditorialBrief | None = None, project_id=None, sources=None):
    request = _request(*texts, brief=brief, project_id=project_id, sources=sources)
    run = PlanningOrchestrator(store).execute(request)
    lineage = store.get_source_lineage(run.source_lineage_id)
    assert lineage is not None
    return run, lineage, store.load_chunk_manifest(lineage.chunk_manifest_uri)


def _orchestrator(store, extractor: RecordingExtractor) -> GraphExtractionOrchestrator:
    return GraphExtractionOrchestrator(store, extractor, model=extractor.model)


def test_graph_extraction_module_does_not_assemble_property_graph() -> None:
    assert "PropertyGraphIndex.from_documents" not in GRAPH_EXTRACTION_PY
    assert "embed_kg_nodes" not in GRAPH_EXTRACTION_PY
    assert "GoogleGenAIEmbedding" not in GRAPH_EXTRACTION_PY


def test_three_chunks_reach_graph_extractions_ready() -> None:
    store = MemoryPlanningPersistence()
    extractor = RecordingExtractor()
    _run, lineage, manifest = _prepare_lineage(store, "chunk um da criança.", "chunk dois da família.", "chunk três da presença.")
    assert lineage.state is SourceLineageState.CHUNKS_READY
    assert manifest.chunk_count == 3
    result = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert result.extracted_count == 3
    assert result.failed_count == 0
    assert extractor.chunk_ids == [entry.chunk_id for entry in manifest.chunks]
    assert all(isinstance(call, GraphExtractionUnit) for call in extractor.calls)
    extraction_manifest = store.load_graph_extraction_manifest(result.graph_extractions_manifest_uri)
    assert extraction_manifest.extracted_count == 3
    for entry in extraction_manifest.entries:
        assert entry.status is GraphExtractionUnitStatus.EXTRACTED
        extraction = store.load_graph_extraction(entry.extraction_uri)
        assert extraction.chunk_id == entry.chunk_id
        assert extraction.chunk_sha256 == entry.content_sha256
    assert store.extraction_writes == 3


def test_failure_on_second_chunk_keeps_first_extraction_durable() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, manifest = _prepare_lineage(store, "primeiro bloco.", "segundo bloco.", "terceiro bloco.")
    failed_id = manifest.chunks[1].chunk_id
    extractor = RecordingExtractor(fail_on={failed_id})
    with pytest.raises(RuntimeError, match="extraction failed"):
        _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    updated = store.get_source_lineage(lineage.source_lineage_id)
    assert updated.state is SourceLineageState.GRAPH_EXTRACTIONS_IN_PROGRESS
    assert updated.extracted_count == 1
    assert updated.failed_count == 1
    extraction_manifest = store.load_graph_extraction_manifest(updated.graph_extractions_manifest_uri)
    assert extraction_manifest.entries[0].status is GraphExtractionUnitStatus.EXTRACTED
    assert store.load_graph_extraction(extraction_manifest.entries[0].extraction_uri).chunk_id == manifest.chunks[0].chunk_id
    assert extraction_manifest.entries[1].status is GraphExtractionUnitStatus.FAILED
    assert extraction_manifest.entries[2].status is GraphExtractionUnitStatus.PENDING
    assert extractor.chunk_ids == [manifest.chunks[0].chunk_id, failed_id]
    assert store.extraction_writes == 1


def test_rerun_after_failure_extracts_only_missing_and_failed_units() -> None:
    store = MemoryPlanningPersistence()
    _run, lineage, manifest = _prepare_lineage(store, "primeiro bloco.", "segundo bloco.", "terceiro bloco.")
    failed_id = manifest.chunks[1].chunk_id
    extractor = RecordingExtractor(fail_on={failed_id})
    with pytest.raises(RuntimeError):
        _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    extractor.fail_on.clear()
    result = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert extractor.chunk_ids == [
        manifest.chunks[0].chunk_id,
        failed_id,
        failed_id,
        manifest.chunks[2].chunk_id,
    ]
    assert store.extraction_writes == 3


def test_rerun_of_complete_lineage_makes_zero_new_extractions() -> None:
    store = MemoryPlanningPersistence()
    extractor = RecordingExtractor()
    _run, lineage, _manifest = _prepare_lineage(store, "um.", "dois.", "três.")
    first = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    writes = store.extraction_writes
    calls = list(extractor.chunk_ids)
    second = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    assert first.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert second.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert second.source_lineage_id == first.source_lineage_id
    assert extractor.chunk_ids == calls
    assert store.extraction_writes == writes


def test_different_brief_reuses_source_lineage_extractions() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    sources = [_source("fonte estável um."), _source("fonte estável dois."), _source("fonte estável três.")]
    first_run, lineage, _manifest = _prepare_lineage(store, sources=sources, project_id=project_id)
    extractor = RecordingExtractor()
    ready = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    calls = list(extractor.chunk_ids)
    writes = store.extraction_writes
    second_run = PlanningOrchestrator(store).execute(_request(sources=sources, project_id=project_id, brief=_brief(theme="Outro recorte")))
    assert second_run.planning_run_id != first_run.planning_run_id
    assert second_run.source_lineage_id == first_run.source_lineage_id
    reused = _orchestrator(store, extractor).execute(second_run.source_lineage_id)
    assert reused.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert reused.source_lineage_id == ready.source_lineage_id
    assert extractor.chunk_ids == calls
    assert store.extraction_writes == writes


def test_chunk_hash_or_version_change_invalidates_only_that_unit() -> None:
    store = MemoryPlanningPersistence()
    extractor = RecordingExtractor()
    _run, lineage, _manifest = _prepare_lineage(store, "um.", "dois.", "três.")
    ready = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    extraction_manifest = store.load_graph_extraction_manifest(ready.graph_extractions_manifest_uri)
    version_target = extraction_manifest.entries[1]
    stored = store.load_graph_extraction(version_target.extraction_uri)
    store.objects[version_target.extraction_uri] = stored.model_copy(update={"extractor_version": "stale-extractor"})
    result = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert extractor.chunk_ids[-1] == version_target.chunk_id
    assert extractor.chunk_ids.count(version_target.chunk_id) == 2
    assert extractor.chunk_ids.count(extraction_manifest.entries[0].chunk_id) == 1
    assert extractor.chunk_ids.count(extraction_manifest.entries[2].chunk_id) == 1
    hash_target = store.load_graph_extraction_manifest(result.graph_extractions_manifest_uri).entries[2]
    chunk = store.load_chunk(hash_target.chunk_uri)
    store.objects[hash_target.chunk_uri] = chunk.model_copy(update={"content_sha256": "e" * 64, "text": chunk.text + " mudança"})
    hashed = _orchestrator(store, extractor).execute(lineage.source_lineage_id)
    assert hashed.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert extractor.chunk_ids[-1] == hash_target.chunk_id
    assert extractor.chunk_ids.count(hash_target.chunk_id) == 2
    assert extractor.chunk_ids.count(extraction_manifest.entries[0].chunk_id) == 1
    refreshed = store.load_graph_extraction(store.load_graph_extraction_manifest(hashed.graph_extractions_manifest_uri).entries[1].extraction_uri)
    assert refreshed.extractor_version == GRAPH_EXTRACTOR_VERSION
    assert store.load_graph_extraction(store.load_graph_extraction_manifest(hashed.graph_extractions_manifest_uri).entries[2].extraction_uri).chunk_sha256 == "e" * 64


def test_persisted_extraction_converts_to_llama_index_nodes_without_llm() -> None:
    extraction = GraphExtraction(
        contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
        extractor_version=GRAPH_EXTRACTOR_VERSION,
        model=FAKE_MODEL,
        source_lineage_id=uuid4(),
        source_id=uuid4(),
        chunk_id="a" * 64,
        chunk_sha256="b" * 64,
        entities=[
            GraphEntity(name="Criança", label="entity", properties={"role": "subject"}),
            GraphEntity(name="Ausência", label="entity"),
        ],
        relations=[GraphRelation(source="Criança", relation="PERCEBE", target="Ausência")],
    )
    entities, relations = graph_extraction_to_llama(extraction)
    assert [type(node) for node in entities] == [EntityNode, EntityNode]
    assert [type(item) for item in relations] == [Relation]
    assert entities[0].name == "Criança"
    assert entities[0].id == "Criança"
    assert relations[0].label == "PERCEBE"
    assert relations[0].source_id == entities[0].id
    assert relations[0].target_id == entities[1].id
    assert entities[0].properties["chunk_id"] == extraction.chunk_id
    assert entities[0].properties["source_id"] == str(extraction.source_id)


def test_llama_index_extractor_uses_one_predict_call_per_chunk() -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def predict(self, prompt, **kwargs):
            self.calls.append({"prompt": prompt, **kwargs})
            return "(Criança, percebe, Ausência)\n(Adulto, tenta, Proteção)"

    llm = FakeLLM()
    extractor = LlamaIndexChunkGraphExtractor(
        GraphExtractionRuntime(llm=llm, model="gemini-2.5-flash", context_window=1_048_576, max_tokens=2_048)
    )
    unit = GraphExtractionUnit(
        source_lineage_id=uuid4(),
        chunk_id="c" * 64,
        source_id=uuid4(),
        text="A criança percebe a ausência mesmo quando o adulto tenta proteger.",
        content_sha256="d" * 64,
        source_ordinal=0,
        chunk_ordinal=0,
        sequence=0,
    )
    extraction = extractor.extract(unit)
    assert len(llm.calls) == 1
    assert llm.calls[0]["text"] == unit.text
    assert llm.calls[0]["max_knowledge_triplets"] == 10
    assert extraction.model == "gemini-2.5-flash"
    assert [entity.name for entity in extraction.entities] == ["Criança", "Ausência", "Adulto", "Proteção"]
    assert [(rel.source, rel.relation, rel.target) for rel in extraction.relations] == [
        ("Criança", "Percebe", "Ausência"),
        ("Adulto", "Tenta", "Proteção"),
    ]
    nodes, relations = graph_extraction_to_llama(extraction)
    assert all(isinstance(node, EntityNode) for node in nodes)
    assert all(isinstance(item, Relation) for item in relations)


def test_gcp_persists_each_extraction_under_the_source_lineage() -> None:
    persistence, storage = _gcp_persistence()
    extractor = RecordingExtractor()
    run = PlanningOrchestrator(persistence).execute(_request("um.", "dois.", "três."))
    result = GraphExtractionOrchestrator(persistence, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    assert result.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    names = list(storage.bucket("test-bucket").blobs)
    extraction_blobs = [name for name in names if "/graph-extractions/" in name]
    assert any(name.endswith("graph-extractions/manifest.json") for name in extraction_blobs)
    assert len([name for name in extraction_blobs if name.endswith(".json")]) == 4
    loaded = persistence.load_graph_extraction_manifest(result.graph_extractions_manifest_uri)
    assert loaded.extracted_count == 3
    assert persistence.load_graph_extraction(loaded.entries[0].extraction_uri).chunk_id == loaded.entries[0].chunk_id


def test_graph_extraction_runtime_rejects_missing_budgets() -> None:
    with pytest.raises(ValueError):
        LlamaIndexChunkGraphExtractor(GraphExtractionRuntime(llm=object(), model="x", context_window=0, max_tokens=128))
    with pytest.raises(ValueError):
        build_graph_extraction_runtime(VertexRuntimeSettings(project="test-project", context_window=0))
    with pytest.raises(ValueError):
        build_graph_extraction_runtime(VertexRuntimeSettings(project="test-project", graph_max_tokens=0))
