from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from llama_index.core.embeddings import MockEmbedding

from comunicamundo_v4.hefesto.axis_proposal import (
    AxisProposalOrchestrator,
    UnderstandingMaterial,
    count_sentences,
    graph_ready_identity_hash,
    validate_axis_statement,
)
from comunicamundo_v4.hefesto.contracts import (
    EDITORIAL_UNDERSTANDING_CONTRACT_VERSION,
    EDITORIAL_UNDERSTANDING_PROMPT_VERSION,
    GRAPH_EXTRACTION_CONTRACT_VERSION,
    GRAPH_EXTRACTOR_VERSION,
    NARRATIVE_AXIS_CONTRACT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    EditorialBrief,
    EditorialUnderstandingProposal,
    GraphEntity,
    GraphExtraction,
    GraphExtractionUnit,
    GraphRelation,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    NormalizedSource,
    PlanningRequest,
    PlanningRunState,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.graph_assembly import GraphAssemblyOrchestrator
from comunicamundo_v4.hefesto.graph_extraction import GraphExtractionOrchestrator
from comunicamundo_v4.hefesto.persistence import FirestoreEditorialRepository, GcsArtifactStore, GcpEditorialPersistence, GcpPersistenceSettings
from comunicamundo_v4.hefesto.planning import PlanningOrchestrator
from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime, GraphIndexingRuntime
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence

AXIS_PY = Path("src/comunicamundo_v4/hefesto/axis_proposal.py").read_text()
WORKER_PY = Path("src/comunicamundo_v4/hefesto/worker.py").read_text()
FAKE_MODEL = "fake-graph-llm"
FAKE_EMBED = "fake-embed"
FAKE_UNDERSTANDING = "fake-understanding"
FAKE_AXIS = "fake-axis"


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


class RecordingSynthesizer:
    def __init__(self) -> None:
        self.materials: list[UnderstandingMaterial] = []

    def synthesize(self, material: UnderstandingMaterial) -> EditorialUnderstandingProposal:
        self.materials.append(material)
        return EditorialUnderstandingProposal(
            central_ideas=["A criança percebe a ausência mesmo quando o adulto tenta proteger."],
            relations_and_tensions=["A relação adulto-criança atravessa a perda em conjunto."],
            nuances=["A honestidade precisa caber na idade da criança."],
            source_limits=["As fontes não cobrem todas as faixas etárias."],
            editorial_opportunities=["Tornar a presença compartilhada praticável no cotidiano."],
            synthesis="As fontes sustentam que a criança percebe a ausência e que o adulto pode transformar proteção em presença compartilhada.",
        )


class RecordingProposer:
    def __init__(self, statement: str | None = None) -> None:
        self.understandings = []
        self.briefs = []
        self.statement = statement or (
            "A criança e o adulto atravessam a perda em relação, e a honestidade adequada à idade pode transformar proteção em presença compartilhada."
        )

    def propose(self, *, brief: EditorialBrief, understanding) -> NarrativeAxisProposal:
        self.briefs.append(brief)
        self.understandings.append(understanding)
        return NarrativeAxisProposal(statement=self.statement)


class FailingSynthesizer:
    def synthesize(self, material: UnderstandingMaterial) -> EditorialUnderstandingProposal:
        raise RuntimeError("understanding failed")


class FailingProposer:
    def propose(self, *, brief: EditorialBrief, understanding) -> NarrativeAxisProposal:
        raise RuntimeError("axis failed")


class RecordingEvidenceReviewer:
    def __init__(self) -> None:
        self.candidates: list[str] = []
        self.understandings = []

    def review(self, *, candidate: str, understanding) -> NarrativeAxisEvidenceReview:
        self.candidates.append(candidate)
        self.understandings.append(understanding)
        return NarrativeAxisEvidenceReview(
            candidate_statement=candidate,
            revision_required=False,
            unsupported_upgrades=[],
            final_statement=candidate,
        )


class FailingReviewer:
    def review(self, *, candidate: str, understanding) -> NarrativeAxisEvidenceReview:
        raise RuntimeError("review failed")


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


def _indexing_runtime(embed: MockEmbedding | None = None) -> GraphIndexingRuntime:
    embed_model = embed if embed is not None else RecordingEmbedding()
    return GraphIndexingRuntime(
        embed_model=embed_model,
        model=FAKE_EMBED,
        package_version="test-embed-1",
        config={"model_name": FAKE_EMBED, "location": "us-central1", "embed_kg_nodes": True},
    )


def _axis_runtime(embed: MockEmbedding | None = None) -> AxisProposalRuntime:
    return AxisProposalRuntime(
        embed_model=embed if embed is not None else RecordingEmbedding(),
        understanding_llm=object(),
        understanding_model=FAKE_UNDERSTANDING,
        understanding_max_tokens=4_096,
        axis_llm=object(),
        axis_model=FAKE_AXIS,
        axis_max_tokens=1_200,
        context_window=1_048_576,
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


def _prepare_graph_ready(store: MemoryPlanningPersistence, *texts: str, brief: EditorialBrief | None = None, project_id=None, sources=None):
    request = _request(*texts, brief=brief, project_id=project_id, sources=sources)
    run = PlanningOrchestrator(store).execute(request)
    extractor = RecordingExtractor()
    lineage = GraphExtractionOrchestrator(store, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    lineage = GraphAssemblyOrchestrator(store, _indexing_runtime()).execute(lineage.source_lineage_id)
    run = store.get_run(run.planning_run_id)
    return run, lineage, extractor, request


def _propose(store, run, *, synthesizer=None, proposer=None, reviewer=None, runtime=None):
    synth = synthesizer if synthesizer is not None else RecordingSynthesizer()
    prop = proposer if proposer is not None else RecordingProposer()
    rev = reviewer if reviewer is not None else RecordingEvidenceReviewer()
    result = AxisProposalOrchestrator(store, runtime or _axis_runtime(), synth, prop, rev).execute(run.planning_run_id)
    return result, synth, prop


def test_axis_module_does_not_reextract_or_create_editorial_project() -> None:
    forbidden = (
        "SimpleLLMPathExtractor",
        "from_documents",
        "build_vertex_runtime",
        "build_graph_extraction_runtime",
        "LlamaIndexChunkGraphExtractor",
        "GraphExtractionOrchestrator",
        "GraphAssemblyOrchestrator",
        "build_project",
        "EditorialProject(",
        ".approve(",
        "save_project",
    )
    for token in forbidden:
        assert token not in AXIS_PY
    assert "propose-axis" in WORKER_PY
    assert "--planning-run-id" in WORKER_PY
    assert "build_axis_proposal_runtime" in WORKER_PY
    assert "LlamaIndexAxisEvidenceReviewer" in AXIS_PY
    assert "LlamaIndexAxisEvidenceReviewer" in WORKER_PY
    assert "build_vertex_runtime" in WORKER_PY


def test_axis_statement_must_be_one_or_two_sentences() -> None:
    assert count_sentences("") == 0
    assert count_sentences("Uma direção intelectual específica.") == 1
    assert count_sentences("Primeira frase. Segunda frase.") == 2
    assert count_sentences("Uma. Duas. Três.") == 3
    assert validate_axis_statement("  Uma direção.  ") == "Uma direção."
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_axis_statement("   ")
    with pytest.raises(ValueError, match="1–2 sentences"):
        validate_axis_statement("Uma. Duas. Três.")


def test_understanding_uses_persisted_graph_and_persisted_chunks() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, request = _prepare_graph_ready(
        store,
        "A criança percebe a ausência mesmo quando o adulto tenta proteger.",
        "A família atravessa a perda em relação.",
    )
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    result, synth, prop = _propose(store, run)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert len(synth.materials) == 1
    material = synth.materials[0]
    assert material.brief.theme == request.brief.theme
    assert "Entidade-0" in material.entities
    assert "Fonte" in material.entities
    assert any("APARECE_EM" in relation for relation in material.relations)
    chunk_texts = {chunk.text for chunk in material.chunks}
    assert any("criança percebe a ausência" in text for text in chunk_texts)
    assert any("família atravessa a perda" in text for text in chunk_texts)
    understanding = result.editorial_understanding
    assert understanding.graph_entity_count >= 2
    assert understanding.graph_relation_count >= 1
    assert understanding.chunk_ids
    assert understanding.graph_artifact_id == lineage.graph_artifact_id
    assert understanding.graph_manifest_hash == graph_ready_identity_hash(store.load_graph_ready_manifest(lineage.graph_manifest_uri))
    assert understanding.contract_version == EDITORIAL_UNDERSTANDING_CONTRACT_VERSION
    assert understanding.prompt_version == EDITORIAL_UNDERSTANDING_PROMPT_VERSION
    assert understanding.model == FAKE_UNDERSTANDING
    assert prop.understandings[0].understanding_id == understanding.understanding_id
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert len(extractor.calls) == 2
    lineage_after = store.get_source_lineage(lineage.source_lineage_id)
    assert lineage_after.state is SourceLineageState.GRAPH_READY
    assert lineage_after.graph_artifact_id == lineage.graph_artifact_id


def test_understanding_is_persisted_before_axis_and_axis_failure_reuses_it() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "primeiro bloco.", "segundo bloco.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    synth = RecordingSynthesizer()
    with pytest.raises(RuntimeError, match="axis failed"):
        AxisProposalOrchestrator(store, _axis_runtime(), synth, FailingProposer(), RecordingEvidenceReviewer()).execute(run.planning_run_id)
    updated = store.get_run(run.planning_run_id)
    assert updated.state is PlanningRunState.UNDERSTANDING_READY
    assert updated.state is not PlanningRunState.AXIS_PROPOSED
    assert updated.artifacts.understanding_uri is not None
    assert updated.artifacts.narrative_axis_id is None
    stored = store.load_understanding(updated.artifacts.understanding_uri)
    assert stored.synthesis
    assert stored.planning_run_id == run.planning_run_id
    assert len(synth.materials) == 1
    assert store.understanding_writes == 1
    assert store.axis_writes == 0
    lineage_after = store.get_source_lineage(lineage.source_lineage_id)
    assert lineage_after.state is SourceLineageState.GRAPH_READY
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert extractor.calls

    proposer = RecordingProposer()
    result = AxisProposalOrchestrator(store, _axis_runtime(), synth, proposer, RecordingEvidenceReviewer()).execute(run.planning_run_id)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert len(synth.materials) == 1
    assert store.understanding_writes == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert result.editorial_understanding.understanding_id == stored.understanding_id
    assert proposer.understandings[0].understanding_id == stored.understanding_id


def test_understanding_failure_keeps_graph_ready_and_stays_below_understanding_ready() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "um.", "dois.")
    extract_writes = store.extraction_writes
    with pytest.raises(RuntimeError, match="understanding failed"):
        AxisProposalOrchestrator(store, _axis_runtime(), FailingSynthesizer(), RecordingProposer(), RecordingEvidenceReviewer()).execute(run.planning_run_id)
    updated = store.get_run(run.planning_run_id)
    assert updated.state is PlanningRunState.GRAPH_READY
    assert updated.state is not PlanningRunState.UNDERSTANDING_READY
    assert updated.artifacts.understanding_uri is None
    assert updated.error == "understanding failed"
    lineage_after = store.get_source_lineage(lineage.source_lineage_id)
    assert lineage_after.state is SourceLineageState.GRAPH_READY
    assert store.extraction_writes == extract_writes
    assert store.understanding_writes == 0
    assert store.axis_writes == 0
    assert extractor.calls


def test_compatible_axis_proposed_rerun_makes_zero_model_calls() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "um.", "dois.", "três.")
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    reviewer = RecordingEvidenceReviewer()
    first, synth, prop = _propose(store, run, reviewer=reviewer)
    understanding_writes = store.understanding_writes
    axis_writes = store.axis_writes
    second, synth2, prop2 = _propose(store, first.planning_run, synthesizer=synth, proposer=prop, reviewer=reviewer)
    assert first.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert second.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert second.planning_run.planning_run_id == first.planning_run.planning_run_id
    assert second.narrative_axis.narrative_axis_id == first.narrative_axis.narrative_axis_id
    assert second.proposed_axis == first.proposed_axis
    assert len(synth.materials) == 1
    assert len(prop.understandings) == 1
    assert len(reviewer.candidates) == 1
    assert len(synth2.materials) == 1
    assert len(prop2.understandings) == 1
    assert store.understanding_writes == understanding_writes
    assert store.axis_writes == axis_writes
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls


def test_different_brief_reuses_graph_ready_and_creates_separate_editorial_run() -> None:
    store = MemoryPlanningPersistence()
    project_id = uuid4()
    sources = [_source("fonte estável um."), _source("fonte estável dois.")]
    first_run, lineage, extractor, _first_request = _prepare_graph_ready(store, sources=sources, project_id=project_id)
    first, synth, prop = _propose(store, first_run)
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    second_run = PlanningOrchestrator(store).execute(_request(sources=sources, project_id=project_id, brief=_brief(theme="Outro recorte")))
    assert second_run.planning_run_id != first_run.planning_run_id
    assert second_run.source_lineage_id == first_run.source_lineage_id
    assert second_run.state is PlanningRunState.SOURCE_CHUNKS_READY
    second, synth2, prop2 = _propose(store, second_run)
    assert second.planning_run.planning_run_id == second_run.planning_run_id
    assert second.planning_run.source_lineage_id == lineage.source_lineage_id
    assert second.editorial_understanding.understanding_id != first.editorial_understanding.understanding_id
    assert second.editorial_understanding.brief_fingerprint != first.editorial_understanding.brief_fingerprint
    assert second.editorial_understanding.graph_artifact_id == first.editorial_understanding.graph_artifact_id
    assert second.narrative_axis.narrative_axis_id != first.narrative_axis.narrative_axis_id
    assert len(synth2.materials) == 1
    assert len(prop2.understandings) == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes + 1
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert len(extractor.calls) == 2
    assert store.get_run(first.planning_run.planning_run_id).state is PlanningRunState.AXIS_PROPOSED


def test_axis_is_one_or_two_sentences_visible_and_never_auto_approved() -> None:
    store = MemoryPlanningPersistence()
    run, _lineage, _extractor, _request = _prepare_graph_ready(store, "um.", "dois.")
    two = RecordingProposer("Primeira frase específica sobre a perda. Segunda frase que fecha a tensão editorial.")
    result, _synth, _prop = _propose(store, run, proposer=two)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.stopped_at == "axis_proposed"
    assert result.human_approval_required is True
    assert result.editorial_project_created is False
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert result.proposed_axis == result.narrative_axis.statement
    assert count_sentences(result.proposed_axis) == 2
    persisted = store.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert persisted.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert persisted.contract_version == NARRATIVE_AXIS_CONTRACT_VERSION
    assert persisted.prompt_version == NARRATIVE_AXIS_PROMPT_VERSION
    assert persisted.model == FAKE_AXIS
    stored_axis = store.get_axis(result.narrative_axis.narrative_axis_id)
    assert stored_axis.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert stored_axis.editorial_project_id is None


def test_invalid_axis_statement_preserves_understanding() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, _extractor, _request = _prepare_graph_ready(store, "um.", "dois.")
    synth = RecordingSynthesizer()
    with pytest.raises(ValueError, match="1–2 sentences"):
        AxisProposalOrchestrator(
            store,
            _axis_runtime(),
            synth,
            RecordingProposer("Uma. Duas. Três frases indevidas."),
            RecordingEvidenceReviewer(),
        ).execute(run.planning_run_id)
    updated = store.get_run(run.planning_run_id)
    assert updated.state is PlanningRunState.UNDERSTANDING_READY
    assert updated.artifacts.understanding_uri is not None
    assert store.understanding_writes == 1
    assert store.get_source_lineage(lineage.source_lineage_id).state is SourceLineageState.GRAPH_READY


def test_incompatible_understanding_model_regenerates_without_source_lineage_work() -> None:
    store = MemoryPlanningPersistence()
    run, lineage, extractor, _request = _prepare_graph_ready(store, "um.", "dois.")
    first, synth, prop = _propose(store, run)
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    changed = AxisProposalRuntime(
        embed_model=RecordingEmbedding(),
        understanding_llm=object(),
        understanding_model="other-understanding",
        understanding_max_tokens=4_096,
        axis_llm=object(),
        axis_model=FAKE_AXIS,
        axis_max_tokens=1_200,
        context_window=1_048_576,
    )
    second, synth2, _prop2 = _propose(store, first.planning_run, runtime=changed)
    assert second.editorial_understanding.understanding_id != first.editorial_understanding.understanding_id
    assert second.editorial_understanding.model == "other-understanding"
    assert second.narrative_axis.narrative_axis_id != first.narrative_axis.narrative_axis_id
    assert len(synth2.materials) == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.get_source_lineage(lineage.source_lineage_id).graph_artifact_id == lineage.graph_artifact_id
    assert extractor.calls


def test_gcp_persists_understanding_and_axis_under_the_planning_run() -> None:
    persistence, storage, firestore = _gcp_persistence()
    extractor = RecordingExtractor()
    run = PlanningOrchestrator(persistence).execute(_request("A criança percebe a ausência.", "O adulto acompanha."))
    extracted = GraphExtractionOrchestrator(persistence, extractor, model=FAKE_MODEL).execute(run.source_lineage_id)
    GraphAssemblyOrchestrator(persistence, _indexing_runtime()).execute(extracted.source_lineage_id)
    result, _synth, _prop = _propose(persistence, run)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    names = list(storage.bucket("test-bucket").blobs)
    assert any(name.endswith("understanding.json") for name in names)
    assert any(name.endswith("axis.json") for name in names)
    assert any("/planning/" in name and name.endswith("understanding.json") for name in names)
    loaded = persistence.load_understanding(result.planning_run.artifacts.understanding_uri)
    assert loaded.understanding_id == result.editorial_understanding.understanding_id
    axis = persistence.load_narrative_axis(result.planning_run.artifacts.narrative_axis_uri)
    assert axis.narrative_axis.statement == result.proposed_axis
    assert axis.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    stored_axis = persistence.get_axis(result.narrative_axis.narrative_axis_id)
    assert stored_axis is not None
    assert stored_axis.editorial_project_id is None
    project_docs = [key for key in firestore._store if key[0] == "hefesto_v4_rebuild_projects"]
    assert project_docs == []
    extraction_names = [name for name in names if "/graph-extractions/" in name]
    assert extraction_names
