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
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SourceLineageState,
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
    fingerprint_request,
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
            planning_intents_collection=settings.planning_intents_collection,
            source_lineages_collection=settings.source_lineages_collection,
            source_lineage_keys_collection=settings.source_lineage_keys_collection,
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
    assert [state.value for state in SourceLineageState] == [
        "created",
        "chunks_ready",
        "graph_extractions_in_progress",
        "graph_extractions_ready",
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
    assert store.lineages == {}
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
    same_sources = fingerprint_sources(original)
    project_id = uuid4()
    assert fingerprint_request(project_id=project_id, source_fingerprint=same_sources, brief_fingerprint=fingerprint_brief(_brief())) != fingerprint_request(
        project_id=project_id,
        source_fingerprint=same_sources,
        brief_fingerprint=fingerprint_brief(_brief(theme="Outro tema")),
    )


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
    lineage = store.get_source_lineage(run.source_lineage_id)
    assert lineage is not None
    assert lineage.state is SourceLineageState.CHUNKS_READY
    assert lineage.chunk_manifest_uri == run.artifacts.chunk_manifest_uri
    manifest = store.load_chunk_manifest(run.artifacts.chunk_manifest_uri)
    assert manifest.source_lineage_id == run.source_lineage_id
    assert manifest.source_fingerprint == run.source_fingerprint
    chunk = store.load_chunk(manifest.chunks[0].chunk_uri)
    assert chunk.text.startswith("A criança percebe")
    assert store.manifest_writes == 1


def test_same_sources_same_brief_reuses_run_and_chunks() -> None:
    store = MemoryPlanningPersistence()
    source = _source("A honestidade adequada à idade transforma proteção em presença.")
    request = _request(sources=[source], project_id=uuid4())
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(request)
    second = orchestrator.execute(request)
    assert first.planning_run_id == second.planning_run_id
    assert first.source_lineage_id == second.source_lineage_id
    assert first.request_fingerprint == second.request_fingerprint
    assert first.artifacts.chunk_manifest_uri == second.artifacts.chunk_manifest_uri
    assert store.request_writes == 1
    assert store.manifest_writes == 1
    assert store.chunk_writes == 1
    assert len(store.lineages) == 1
    assert len(store.runs) == 1


def test_same_sources_changed_brief_creates_new_run_and_reuses_chunks() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    source = _source("Material estável das fontes normalizadas.")
    first_request = _request(sources=[source], project_id=project_id, brief=_brief())
    second_request = _request(sources=[source], project_id=project_id, brief=_brief(theme="Outro recorte editorial"))
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(first_request)
    second = orchestrator.execute(second_request)
    assert first.planning_run_id != second.planning_run_id
    assert first.source_lineage_id == second.source_lineage_id
    assert first.source_fingerprint == second.source_fingerprint
    assert first.brief_fingerprint != second.brief_fingerprint
    assert first.request_fingerprint != second.request_fingerprint
    assert first.artifacts.chunk_manifest_uri == second.artifacts.chunk_manifest_uri
    assert store.manifest_writes == 1
    assert store.chunk_writes == 1
    assert store.request_writes == 2
    assert len(store.lineages) == 1
    assert len(store.runs) == 2
    lineage = store.get_source_lineage(first.source_lineage_id)
    assert lineage is not None
    assert lineage.chunk_manifest_uri == first.artifacts.chunk_manifest_uri


def test_changed_brief_does_not_return_a_later_stage_run() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    source = _source("As mesmas fontes não autorizam reutilizar o Eixo de outro briefing.")
    first_request = _request(sources=[source], project_id=project_id, brief=_brief())
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(first_request)
    advanced = first.model_copy(update={"state": PlanningRunState.AXIS_PROPOSED})
    store.save_run(advanced)
    same_brief = orchestrator.execute(first_request)
    assert same_brief.planning_run_id == first.planning_run_id
    assert same_brief.state is PlanningRunState.AXIS_PROPOSED
    second = orchestrator.execute(_request(sources=[source], project_id=project_id, brief=_brief(approach="outro rumo")))
    assert second.planning_run_id != first.planning_run_id
    assert second.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert second.source_lineage_id == first.source_lineage_id
    assert store.get_run(first.planning_run_id).state is PlanningRunState.AXIS_PROPOSED
    assert store.manifest_writes == 1
    assert store.chunk_writes == 1


def test_resume_from_request_validated_prepares_chunks_without_new_run() -> None:
    store = MemoryPlanningPersistence()
    request = _request("Material suficiente para preparar chunks na retomada.")
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator._ensure_request_validated(request)
    assert first.state is PlanningRunState.REQUEST_VALIDATED
    assert store.manifest_writes == 0
    resumed = orchestrator.execute(request)
    assert resumed.planning_run_id == first.planning_run_id
    assert resumed.source_lineage_id == first.source_lineage_id
    assert resumed.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert store.manifest_writes == 1


def test_source_change_starts_a_new_source_lineage_and_run() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    source_id = uuid4()
    first_request = _request(sources=[_source("versão um", source_id=source_id)], project_id=project_id)
    second_request = _request(sources=[_source("versão dois", source_id=source_id)], project_id=project_id)
    orchestrator = PlanningOrchestrator(store)
    first = orchestrator.execute(first_request)
    second = orchestrator.execute(second_request)
    assert first.planning_run_id != second.planning_run_id
    assert first.source_lineage_id != second.source_lineage_id
    assert first.source_fingerprint != second.source_fingerprint
    assert first.artifacts.chunk_manifest_uri != second.artifacts.chunk_manifest_uri
    assert store.find_source_lineage(project_id, first.source_fingerprint).source_lineage_id == first.source_lineage_id
    assert store.find_source_lineage(project_id, second.source_fingerprint).source_lineage_id == second.source_lineage_id
    assert store.find_run(project_id, first.request_fingerprint).planning_run_id == first.planning_run_id
    assert store.find_run(project_id, second.request_fingerprint).planning_run_id == second.planning_run_id
    assert store.manifest_writes == 2
    assert len(store.lineages) == 2
    assert len(store.runs) == 2


def test_gcp_persistence_keeps_source_lineage_and_planning_run_distinct() -> None:
    persistence, storage, _firestore = _gcp_persistence()
    project_id = uuid4()
    source = _source("Persistir o manifesto de chunks no bucket do rebuild.")
    request = _request(sources=[source], project_id=project_id)
    run = PlanningOrchestrator(persistence).execute(request)
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    loaded = persistence.get_run(run.planning_run_id)
    assert loaded is not None
    assert loaded.source_lineage_id == run.source_lineage_id
    assert persistence.find_run(request.project_id, run.request_fingerprint).planning_run_id == run.planning_run_id
    lineage = persistence.find_source_lineage(request.project_id, run.source_fingerprint)
    assert lineage.source_lineage_id == run.source_lineage_id
    manifest = persistence.load_chunk_manifest(run.artifacts.chunk_manifest_uri)
    assert manifest.source_lineage_id == run.source_lineage_id
    chunk = persistence.load_chunk(manifest.chunks[0].chunk_uri)
    assert chunk.content_sha256 == manifest.chunks[0].content_sha256
    bucket = storage.bucket("test-bucket")
    chunk_blobs = [name for name in bucket.blobs if "/sources/" in name and "/chunks/" in name]
    assert any(name.endswith("chunks/manifest.json") for name in chunk_blobs)
    reused = PlanningOrchestrator(persistence).execute(request)
    assert reused.planning_run_id == run.planning_run_id
    changed_brief = PlanningOrchestrator(persistence).execute(_request(sources=[source], project_id=project_id, brief=_brief(theme="Novo briefing")))
    assert changed_brief.planning_run_id != run.planning_run_id
    assert changed_brief.source_lineage_id == run.source_lineage_id
    assert changed_brief.artifacts.chunk_manifest_uri == run.artifacts.chunk_manifest_uri
    assert [name for name in bucket.blobs if "/sources/" in name and "/chunks/" in name] == chunk_blobs


def test_entrega_1_does_not_create_editorial_project_or_axis() -> None:
    run = PlanningOrchestrator(MemoryPlanningPersistence()).execute(_request("Somente preparação de fontes."))
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert run.artifacts.narrative_axis_id is None
    assert run.artifacts.graph_artifact_id is None
    assert run.artifacts.understanding_uri is None
    assert run.artifacts.graph_extractions_uri is None
    assert isinstance(run, PlanningRun)
    assert isinstance(SourceLineage(project_id=run.project_id, source_fingerprint=run.source_fingerprint), SourceLineage)
