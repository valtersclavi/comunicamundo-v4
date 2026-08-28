import base64
import json
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.contracts import (
    GRAPH_EXTRACTION_CONTRACT_VERSION,
    GRAPH_EXTRACTOR_VERSION,
    AxisProposalResult,
    GraphEntity,
    GraphExtraction,
    GraphRelation,
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime, GraphExtractionRuntime, GraphIndexingRuntime
from comunicamundo_v4.hefesto.worker import _feedback, _parser, main
from fakes import MemoryPlanningPersistence
from llama_index.core.embeddings import MockEmbedding


def test_worker_exposes_both_approval_boundaries() -> None:
    parser = _parser()
    axis_id = str(uuid4())
    project_id = str(uuid4())
    assert parser.parse_args(["approve-axis", "--narrative-axis-id", axis_id]).command == "approve-axis"
    assert parser.parse_args(["approve-project", "--editorial-project-id", project_id]).command == "approve-project"
    assert parser.parse_args(["plan", "--request", "gs://bucket/request.json"]).command == "plan"
    parsed = parser.parse_args(["extract-graph", "--source-lineage-id", axis_id])
    assert parsed.command == "extract-graph"
    assert str(parsed.source_lineage_id) == axis_id
    indexed = parser.parse_args(["index-graph", "--source-lineage-id", axis_id])
    assert indexed.command == "index-graph"
    assert str(indexed.source_lineage_id) == axis_id
    proposed = parser.parse_args(["propose-axis", "--planning-run-id", axis_id])
    assert proposed.command == "propose-axis"
    assert str(proposed.planning_run_id) == axis_id


def test_feedback_is_transport_encoded_but_natural_language() -> None:
    text = "Inclua que o adulto também está sofrendo."
    encoded = base64.urlsafe_b64encode(text.encode()).decode()
    assert _feedback(encoded) == text
    with pytest.raises(ValueError):
        _feedback("")


def _write_request(path, *, extra=None, sources=None, brief=None) -> None:
    payload = {
        "project_id": str(uuid4()),
        "brief": brief or {"theme": "Luto infantil", "approach": "sensível", "duration_minutes": 8},
        "sources": [{"source_id": str(uuid4()), "text": "A criança percebe a ausência.", "language": "pt-BR"}] if sources is None else sources,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_invalid_plan_request_stops_before_runtime_and_persistence(monkeypatch, tmp_path) -> None:
    sys.modules.pop("comunicamundo_v4.hefesto.runtime", None)
    sys.modules.pop("comunicamundo_v4.hefesto.application", None)
    calls: list[str] = []
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("HEFESTO_GCS_BUCKET", raising=False)
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._application", lambda: calls.append("application") or (_ for _ in ()).throw(AssertionError("runtime")))
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: calls.append("persistence") or (_ for _ in ()).throw(AssertionError("persistence")))
    path = tmp_path / "bad.json"
    _write_request(path, extra={"recipe": "not allowed"})
    with pytest.raises(ValidationError):
        main(["plan", "--request", str(path)])
    assert calls == []
    assert "comunicamundo_v4.hefesto.runtime" not in sys.modules
    assert "comunicamundo_v4.hefesto.application" not in sys.modules


def test_empty_sources_stop_before_runtime(monkeypatch, tmp_path) -> None:
    sys.modules.pop("comunicamundo_v4.hefesto.runtime", None)
    calls: list[str] = []
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._application", lambda: calls.append("application") or (_ for _ in ()).throw(AssertionError("runtime")))
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: calls.append("persistence") or (_ for _ in ()).throw(AssertionError("persistence")))
    path = tmp_path / "empty.json"
    _write_request(path, sources=[])
    with pytest.raises(ValidationError):
        main(["plan", "--request", str(path)])
    assert calls == []
    assert "comunicamundo_v4.hefesto.runtime" not in sys.modules


def test_plan_command_reaches_source_chunks_ready_without_building_runtime(monkeypatch, tmp_path, capsys) -> None:
    sys.modules.pop("comunicamundo_v4.hefesto.runtime", None)
    sys.modules.pop("comunicamundo_v4.hefesto.application", None)
    store = MemoryPlanningPersistence()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._application", lambda: (_ for _ in ()).throw(AssertionError("Vertex runtime must not be built for plan")))
    path = tmp_path / "request.json"
    _write_request(path)
    main(["plan", "--request", str(path)])
    run = PlanningRun.model_validate_json(capsys.readouterr().out)
    assert run.state is PlanningRunState.SOURCE_CHUNKS_READY
    assert run.chunk_count == 1
    assert store.manifest_writes == 1
    assert "comunicamundo_v4.hefesto.runtime" not in sys.modules
    main(["plan", "--request", str(path)])
    rerun = PlanningRun.model_validate_json(capsys.readouterr().out)
    assert rerun.planning_run_id == run.planning_run_id
    assert rerun.source_lineage_id == run.source_lineage_id
    assert store.manifest_writes == 1
    assert "comunicamundo_v4.hefesto.runtime" not in sys.modules
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["brief"]["theme"] = "Outro recorte"
    changed_path = tmp_path / "changed-brief.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    main(["plan", "--request", str(changed_path)])
    new_brief = PlanningRun.model_validate_json(capsys.readouterr().out)
    assert new_brief.planning_run_id != run.planning_run_id
    assert new_brief.source_lineage_id == run.source_lineage_id
    assert store.manifest_writes == 1
    assert store.chunk_writes == 1
    assert "comunicamundo_v4.hefesto.runtime" not in sys.modules


def test_extract_graph_command_uses_only_graph_extraction_runtime(monkeypatch, tmp_path, capsys) -> None:
    store = MemoryPlanningPersistence()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("full Vertex runtime must not be built for extract-graph")),
    )
    path = tmp_path / "request.json"
    _write_request(
        path,
        sources=[
            {"source_id": str(uuid4()), "text": "primeiro.", "language": "pt-BR"},
            {"source_id": str(uuid4()), "text": "segundo.", "language": "pt-BR"},
            {"source_id": str(uuid4()), "text": "terceiro.", "language": "pt-BR"},
        ],
    )
    main(["plan", "--request", str(path)])
    run = PlanningRun.model_validate_json(capsys.readouterr().out)
    calls: list[str] = []

    class FakeExtractor:
        def __init__(self, runtime) -> None:
            assert runtime.model == "fake-graph-llm"
            assert runtime.context_window == 1_048_576
            assert runtime.max_tokens == 2_048

        def extract(self, unit):
            calls.append(unit.chunk_id)
            return GraphExtraction(
                contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
                extractor_version=GRAPH_EXTRACTOR_VERSION,
                model="fake-graph-llm",
                source_lineage_id=unit.source_lineage_id,
                source_id=unit.source_id,
                chunk_id=unit.chunk_id,
                chunk_sha256=unit.content_sha256,
                entities=[GraphEntity(name="Criança")],
                relations=[GraphRelation(source="Criança", relation="PERCEBE", target="Ausência")],
            )

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: GraphExtractionRuntime(llm=object(), model="fake-graph-llm", context_window=1_048_576, max_tokens=2_048),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.graph_extraction.LlamaIndexChunkGraphExtractor", FakeExtractor)
    main(["extract-graph", "--source-lineage-id", str(run.source_lineage_id)])
    lineage = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert lineage.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert lineage.extracted_count == 3
    assert store.extraction_writes == 3
    assert len(calls) == 3
    main(["extract-graph", "--source-lineage-id", str(run.source_lineage_id)])
    rerun = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert rerun.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    assert store.extraction_writes == 3
    assert len(calls) == 3


class _RecordingEmbed(MockEmbedding):
    def __init__(self) -> None:
        super().__init__(embed_dim=8, model_name="fake-embed")
        object.__setattr__(self, "_recorded", [])

    @property
    def texts(self) -> list[str]:
        return self._recorded

    def get_text_embedding_batch(self, texts, **kwargs):
        self._recorded.extend(list(texts))
        return super().get_text_embedding_batch(texts, **kwargs)


def test_index_graph_command_uses_only_embedding_runtime(monkeypatch, tmp_path, capsys) -> None:
    store = MemoryPlanningPersistence()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("full Vertex runtime must not be built for index-graph")),
    )
    path = tmp_path / "request.json"
    _write_request(
        path,
        sources=[
            {"source_id": str(uuid4()), "text": "primeiro.", "language": "pt-BR"},
            {"source_id": str(uuid4()), "text": "segundo.", "language": "pt-BR"},
            {"source_id": str(uuid4()), "text": "terceiro.", "language": "pt-BR"},
        ],
    )
    main(["plan", "--request", str(path)])
    run = PlanningRun.model_validate_json(capsys.readouterr().out)

    class FakeExtractor:
        def __init__(self, runtime) -> None:
            assert runtime.model == "fake-graph-llm"

        def extract(self, unit):
            return GraphExtraction(
                contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
                extractor_version=GRAPH_EXTRACTOR_VERSION,
                model="fake-graph-llm",
                source_lineage_id=unit.source_lineage_id,
                source_id=unit.source_id,
                chunk_id=unit.chunk_id,
                chunk_sha256=unit.content_sha256,
                entities=[GraphEntity(name="Criança"), GraphEntity(name="Ausência")],
                relations=[GraphRelation(source="Criança", relation="PERCEBE", target="Ausência")],
            )

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: GraphExtractionRuntime(llm=object(), model="fake-graph-llm", context_window=1_048_576, max_tokens=2_048),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.graph_extraction.LlamaIndexChunkGraphExtractor", FakeExtractor)
    main(["extract-graph", "--source-lineage-id", str(run.source_lineage_id)])
    extracted = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert extracted.state is SourceLineageState.GRAPH_EXTRACTIONS_READY
    extract_writes = store.extraction_writes

    embed = _RecordingEmbed()
    indexing_calls: list[str] = []

    def indexing_runtime():
        indexing_calls.append("indexing")
        return GraphIndexingRuntime(
            embed_model=embed,
            model="fake-embed",
            package_version="test-embed-1",
            config={"model_name": "fake-embed", "location": "us-central1", "embed_kg_nodes": True},
        )

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph extraction runtime must not be built for index-graph")),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._graph_indexing_runtime", indexing_runtime)
    main(["index-graph", "--source-lineage-id", str(run.source_lineage_id)])
    lineage = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert lineage.state is SourceLineageState.GRAPH_READY
    assert lineage.graph_artifact_id is not None
    assert store.extraction_writes == extract_writes
    assert embed.texts
    first_texts = list(embed.texts)
    assert indexing_calls == ["indexing"]
    main(["index-graph", "--source-lineage-id", str(run.source_lineage_id)])
    rerun = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert rerun.state is SourceLineageState.GRAPH_READY
    assert rerun.graph_artifact_id == lineage.graph_artifact_id
    assert store.extraction_writes == extract_writes
    assert embed.texts == first_texts
    assert indexing_calls == ["indexing", "indexing"]


def test_propose_axis_command_uses_only_axis_proposal_runtime(monkeypatch, tmp_path, capsys) -> None:
    store = MemoryPlanningPersistence()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("full Vertex runtime must not be built for propose-axis")),
    )
    path = tmp_path / "request.json"
    _write_request(
        path,
        sources=[
            {"source_id": str(uuid4()), "text": "primeiro.", "language": "pt-BR"},
            {"source_id": str(uuid4()), "text": "segundo.", "language": "pt-BR"},
        ],
    )
    main(["plan", "--request", str(path)])
    run = PlanningRun.model_validate_json(capsys.readouterr().out)

    class FakeExtractor:
        def __init__(self, runtime) -> None:
            assert runtime.model == "fake-graph-llm"

        def extract(self, unit):
            return GraphExtraction(
                contract_version=GRAPH_EXTRACTION_CONTRACT_VERSION,
                extractor_version=GRAPH_EXTRACTOR_VERSION,
                model="fake-graph-llm",
                source_lineage_id=unit.source_lineage_id,
                source_id=unit.source_id,
                chunk_id=unit.chunk_id,
                chunk_sha256=unit.content_sha256,
                entities=[GraphEntity(name="Criança"), GraphEntity(name="Ausência")],
                relations=[GraphRelation(source="Criança", relation="PERCEBE", target="Ausência")],
            )

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: GraphExtractionRuntime(llm=object(), model="fake-graph-llm", context_window=1_048_576, max_tokens=2_048),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.graph_extraction.LlamaIndexChunkGraphExtractor", FakeExtractor)
    main(["extract-graph", "--source-lineage-id", str(run.source_lineage_id)])
    capsys.readouterr()
    embed = _RecordingEmbed()
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_indexing_runtime",
        lambda: GraphIndexingRuntime(
            embed_model=embed,
            model="fake-embed",
            package_version="test-embed-1",
            config={"model_name": "fake-embed", "location": "us-central1", "embed_kg_nodes": True},
        ),
    )
    main(["index-graph", "--source-lineage-id", str(run.source_lineage_id)])
    lineage = SourceLineage.model_validate_json(capsys.readouterr().out)
    assert lineage.state is SourceLineageState.GRAPH_READY
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes

    from comunicamundo_v4.hefesto.contracts import (
        EditorialUnderstandingProposal,
        NarrativeAxisEvidenceReview,
        NarrativeAxisProposal,
    )
    from comunicamundo_v4.hefesto.axis_proposal import UnderstandingMaterial

    class FakeSynthesizer:
        def __init__(self, runtime) -> None:
            assert runtime.understanding_model == "fake-understanding"
            assert runtime.understanding_max_tokens == 4_096
            assert runtime.axis_max_tokens == 1_200
            self.calls = 0

        def synthesize(self, material: UnderstandingMaterial):
            self.calls += 1
            assert material.entities
            assert material.chunks
            return EditorialUnderstandingProposal(
                central_ideas=["Ideia central persistida."],
                relations_and_tensions=["Tensão relevante."],
                synthesis="Compreensão editorial persistida a partir do grafo e dos chunks.",
            )

    class FakeProposer:
        def __init__(self, runtime) -> None:
            assert runtime.axis_model == "fake-axis"
            self.calls = 0

        def propose(self, *, brief, understanding):
            self.calls += 1
            assert understanding.synthesis
            return NarrativeAxisProposal(
                statement="A perda infantil se atravessa em relação, e a honestidade adequada à idade transforma proteção em presença."
            )

    class FakeReviewer:
        def __init__(self, runtime) -> None:
            assert runtime.axis_model == "fake-axis"
            self.calls = 0

        def review(self, *, candidate, understanding):
            self.calls += 1
            assert candidate
            assert understanding.synthesis
            return NarrativeAxisEvidenceReview(
                candidate_statement=candidate,
                revision_required=False,
                unsupported_upgrades=[],
                final_statement=candidate,
            )

    synth_holder: list[FakeSynthesizer] = []
    prop_holder: list[FakeProposer] = []
    review_holder: list[FakeReviewer] = []

    def make_synth(runtime):
        synth = FakeSynthesizer(runtime)
        synth_holder.append(synth)
        return synth

    def make_prop(runtime):
        prop = FakeProposer(runtime)
        prop_holder.append(prop)
        return prop

    def make_reviewer(runtime):
        reviewer = FakeReviewer(runtime)
        review_holder.append(reviewer)
        return reviewer

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph extraction runtime must not be built for propose-axis")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_indexing_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph indexing runtime must not be built for propose-axis")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._axis_proposal_runtime",
        lambda: AxisProposalRuntime(
            embed_model=_RecordingEmbed(),
            understanding_llm=object(),
            understanding_model="fake-understanding",
            understanding_max_tokens=4_096,
            axis_llm=object(),
            axis_model="fake-axis",
            axis_max_tokens=1_200,
            context_window=1_048_576,
        ),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.axis_proposal.LlamaIndexUnderstandingSynthesizer", make_synth)
    monkeypatch.setattr("comunicamundo_v4.hefesto.axis_proposal.LlamaIndexAxisProposer", make_prop)
    monkeypatch.setattr("comunicamundo_v4.hefesto.axis_proposal.LlamaIndexAxisEvidenceReviewer", make_reviewer)
    main(["propose-axis", "--planning-run-id", str(run.planning_run_id)])
    result = AxisProposalResult.model_validate_json(capsys.readouterr().out)
    assert result.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert result.human_approval_required is True
    assert result.editorial_project_created is False
    assert result.narrative_axis.status.value == "proposed"
    assert result.proposed_axis
    assert synth_holder[0].calls == 1
    assert prop_holder[0].calls == 1
    assert review_holder[0].calls == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    main(["propose-axis", "--planning-run-id", str(run.planning_run_id)])
    rerun = AxisProposalResult.model_validate_json(capsys.readouterr().out)
    assert rerun.planning_run.state is PlanningRunState.AXIS_PROPOSED
    assert rerun.narrative_axis.narrative_axis_id == result.narrative_axis.narrative_axis_id
    assert synth_holder[0].calls == 1
    assert prop_holder[0].calls == 1
    assert review_holder[0].calls == 1
    assert synth_holder[1].calls == 0
    assert prop_holder[1].calls == 0
    assert review_holder[1].calls == 0
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes


