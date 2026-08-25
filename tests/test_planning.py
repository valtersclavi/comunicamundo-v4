from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.contracts import (
    ChunkStatus,
    EditorialBrief,
    NormalizedSource,
    PlanningRequest,
    PlanningRunState,
)
from comunicamundo_v4.hefesto.persistence import (
    FirestoreEditorialRepository,
    GcsArtifactStore,
    GcpEditorialPersistence,
    GcpPersistenceSettings,
)
from comunicamundo_v4.hefesto.planning import (
    CHUNK_SIZE_CHARS,
    PlanningOrchestrator,
    fingerprint_brief,
    fingerprint_sources,
    prepare_source_chunks,
    split_source_text,
)
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence

PLANNING_PY = Path("src/comunicamundo_v4/hefesto/planning.py").read_text()
PERSISTENCE_PY = Path("src/comunicamundo_v4/hefesto/persistence.py").read_text()
CONTRACTS_PY = Path("src/comunicamundo_v4/hefesto/contracts.py").read_text()


def _brief(**overrides) -> EditorialBrief:
    payload = {"theme": "Luto infantil", "approach": "sensível e prático", "duration_minutes": 8, **overrides}
    return EditorialBrief.model_validate(payload)


def _source(text: str, *, source_id=None, title: str | None = "Fonte") -> NormalizedSource:
    return NormalizedSource(source_id=source_id or uuid4(), text=text, language="pt-BR", title=title)


def _request(*texts: str, project_id=None, brief: EditorialBrief | None = None, sources: list[NormalizedSource] | None = None) -> PlanningRequest:
    return PlanningRequest(
        project_id=project_id or uuid4(),
        brief=brief or _brief(),
        sources=sources or [_source(text) for text in texts],
    )


def _gcp_persistence() -> tuple[GcpEditorialPersistence, FakeStorageClient, FakeFirestore]:
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
            planning_lineages_collection=settings.planning_lineages_collection,
        ),
    )
    return persistence, storage, firestore


def test_canonical_planning_states_are_stable() -> None:
    assert [state.value for state in PlanningRunState] == [
        "request_validated",
        "source_chunks_ready",
        "graph_extractions_ready",
        "graph_ready",
        "understanding_ready",
        "axis_proposed",
    ]


def test_planning_modules_do_not_construct_or_import_model_clients() -> None:
    forbidden = ("GoogleGenAI", "llama_index", "build_vertex_runtime", "GoogleGenAIEmbedding", "HefestoRuntime")
    for token in forbidden:
        assert token not in PLANNING_PY
        assert token not in PERSISTENCE_PY
        assert token not in CONTRACTS_PY


def test_invalid_planning_request_is_rejected_before_any_store() -> None:
    store = MemoryPlanningPersistence()
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({"project_id": str(uuid4()), "brief": _brief().model_dump(), "sources": []})
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({"project_id": str(uuid4())})
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate(
            {
                "project_id": str(uuid4()),
                "brief": _brief().model_dump(),
                "sources": [_source("texto").model_dump(mode="json")],
                "recipe": "not allowed",
            }
        )
    assert store.runs == {}
    assert store.request_writes == 0
    assert store.manifest_writes == 0


def test_source_chunks_are_stable_for_the_same_input() -> None:
    source_id = uuid4()
    text = "Parágrafo um sobre a criança.\n\n" + ("A família atravessa a perda em relação. " * 200)
    first = prepare_source_chunks([_source(text, source_id=source_id)])
    second = prepare_source_chunks([_source(text, source_id=source_id)])
    assert len(first) >= 2
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.content_sha256 for chunk in first] == [chunk.content_sha256 for chunk in second]
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert first[0].source_id == source_id
    assert first[0].status is ChunkStatus.PREPARED
    assert all(chunk.extraction_ref is None for chunk in first)


def test_split_source_text_is_deterministic_and_covers_the_input() -> None:
    text = ("Bloco de memória familiar. " * 200) + "\n\n" + ("Outro bloco clínico. " * 200)
    once = split_source_text(text)
    twice = split_source_text(text)
    assert once == twice
    assert len(once) >= 2
    assert all(1 <= len(chunk) <= CHUNK_SIZE_CHARS for chunk in once)
    assert text.startswith(once[0])
    assert text.endswith(once[-1])
    assert sum(len(chunk) for chunk in once) > len(text)


def test_source_fingerprint_changes_when_source_content_changes() -> None:
    source_id = uuid4()
    original = [_source("texto estável", source_id=source_id)]
    changed = [_source("texto estável mudou", source_id=source_id)]
    assert fingerprint_sources(original) == fingerprint_sources(original)
    assert fingerprint_sources(original) != fingerprint_sources(changed)
    assert fingerprint_brief(_brief()) != fingerprint_brief(_brief(theme="Outro tema"))


def test_plan_reaches_source_chunks_ready_without_model_runtime() -> None:
    store = MemoryPlanningPersistence()
    request = _request("A criança percebe a ausência mesmo quando o adulto tenta proteger.")
    run = PlanningOrchestrator(store).execute(request)
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert run.error is None
    assert run.chunk_count == 1
    assert run.artifacts.request_uri is not None
    assert run.artifacts.chunk_manifest_uri is not None
    assert run.artifacts.narrative_axis_id is None
    manifest = store.load_chunk_manifest(run.artifacts.chunk_manifest_uri)
    assert manifest.source_fingerprint == run.source_fingerprint
    chunk = store.load_chunk(manifest.chunks[0].chunk_uri)
    assert chunk.text.startswith("A criança percebe")
    assert store.manifest_writes == 1


def test_rerun_reuses_existing_chunks_and_planning_run() -> None:
    store = MemoryPlanningPersistence()
    source = _source("A honestidade adequada à idade transforma proteção em presença.")
    request = _request(sources=[source], project_id=uuid4())
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(request)
    second = orchestrator.execute(request)
    assert first.planning_run_id == second.planning_run_id
    assert first.source_fingerprint == second.source_fingerprint
    assert first.artifacts.chunk_manifest_uri == second.artifacts.chunk_manifest_uri
    assert store.request_writes == 1
    assert store.manifest_writes == 1
    assert store.chunk_writes == 1
    first_manifest = store.load_chunk_manifest(first.artifacts.chunk_manifest_uri)
    second_manifest = store.load_chunk_manifest(second.artifacts.chunk_manifest_uri)
    assert [entry.chunk_id for entry in first_manifest.chunks] == [entry.chunk_id for entry in second_manifest.chunks]


def test_resume_from_request_validated_prepares_chunks_without_new_run() -> None:
    store = MemoryPlanningPersistence()
    request = _request("Material suficiente para preparar chunks na retomada.")
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator._ensure_request_validated(request)
    assert first.state is PlanningRunState.REQUEST_VALIDATED
    assert store.manifest_writes == 0
    resumed = orchestrator.execute(request)
    assert resumed.planning_run_id == first.planning_run_id
    assert resumed.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert store.manifest_writes == 1


def test_source_change_starts_a_new_planning_lineage() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    source_id = uuid4()
    first_request = _request(sources=[_source("versão um", source_id=source_id)], project_id=project_id)
    second_request = _request(sources=[_source("versão dois", source_id=source_id)], project_id=project_id)
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(first_request)
    second = orchestrator.execute(second_request)
    assert first.planning_run_id != second.planning_run_id
    assert first.source_fingerprint != second.source_fingerprint
    assert store.find_run(project_id, first.source_fingerprint).planning_run_id == first.planning_run_id
    assert store.find_run(project_id, second.source_fingerprint).planning_run_id == second.planning_run_id


def test_gcp_persistence_writes_run_and_chunk_manifest() -> None:
    persistence, storage, _firestore = _gcp_persistence()
    request = _request("Persistir o manifesto de chunks no bucket do rebuild.")
    run = PlanningOrchestrator(persistence).execute(request)
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    loaded = persistence.get_run(run.planning_run_id)
    assert loaded is not None
    assert loaded.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert persistence.find_run(request.project_id, run.source_fingerprint).planning_run_id == run.planning_run_id
    manifest = persistence.load_chunk_manifest(run.artifacts.chunk_manifest_uri)
    chunk = persistence.load_chunk(manifest.chunks[0].chunk_uri)
    assert chunk.content_sha256 == manifest.chunks[0].content_sha256
    bucket = storage.bucket("test-bucket")
    assert any(name.endswith("chunks/manifest.json") for name in bucket.blobs)
    reused = PlanningOrchestrator(persistence).execute(request)
    assert reused.planning_run_id == run.planning_run_id


def test_entrega_1_does_not_create_editorial_project_or_axis() -> None:
    run = PlanningOrchestrator(MemoryPlanningPersistence()).execute(_request("Somente preparação de fontes."))
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert run.artifacts.narrative_axis_id is None
    assert run.artifacts.graph_artifact_id is None
    assert run.artifacts.understanding_uri is None
    assert run.artifacts.graph_extractions_uri is None
