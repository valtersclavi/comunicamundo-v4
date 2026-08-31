import asyncio
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
    EditorialProjectProposalResult,
    GraphEntity,
    GraphExtraction,
    GraphRelation,
    PlanningRun,
    PlanningRunState,
    SourceLineage,
    SourceLineageState,
)
from comunicamundo_v4.hefesto.runtime import AxisProposalRuntime, EditorialProjectRuntime, GraphExtractionRuntime, GraphIndexingRuntime
from comunicamundo_v4.hefesto.worker import _feedback, _parser, main
from fakes import MemoryPlanningPersistence
from llama_index.core.embeddings import MockEmbedding


def test_worker_exposes_both_approval_boundaries() -> None:
    parser = _parser()
    axis_id = str(uuid4())
    project_id = str(uuid4())
    assert parser.parse_args(["approve-project", "--editorial-project-id", project_id]).command == "approve-project"
    assert parser.parse_args(["plan", "--request", "gs://bucket/request.json"]).command == "plan"
    digested = parser.parse_args(
        ["digest-v2", "--request", "gs://bucket/request.json", "--tokens-per-minute", "1000000"]
    )
    assert digested.command == "digest-v2"
    assert digested.tokens_per_minute == 1_000_000
    resumed_v2 = parser.parse_args(
        ["resume-digest-v2", "--execution-run-id", axis_id, "--tokens-per-minute", "1000000"]
    )
    assert resumed_v2.command == "resume-digest-v2"
    assert resumed_v2.max_concurrency == 4
    proposed_v2 = parser.parse_args(
        ["propose-axis-v2", "--execution-run-id", axis_id, "--tokens-per-minute", "1000000"]
    )
    assert proposed_v2.command == "propose-axis-v2"
    approved_axis_v2 = parser.parse_args(
        ["approve-axis-v2", "--execution-run-id", axis_id, "--tokens-per-minute", "1000000"]
    )
    assert approved_axis_v2.command == "approve-axis-v2"
    approved_project_v2 = parser.parse_args(
        [
            "approve-project-v2",
            "--execution-run-id",
            axis_id,
            "--without-tips",
            "--tokens-per-minute",
            "1000000",
        ]
    )
    assert approved_project_v2.command == "approve-project-v2"
    assert approved_project_v2.without_tips is True
    produced_v2 = parser.parse_args(
        ["produce-v2", "--execution-run-id", axis_id, "--tokens-per-minute", "1000000"]
    )
    assert produced_v2.command == "produce-v2"
    parsed = parser.parse_args(["extract-graph", "--source-lineage-id", axis_id])
    assert parsed.command == "extract-graph"
    assert str(parsed.source_lineage_id) == axis_id
    indexed = parser.parse_args(["index-graph", "--source-lineage-id", axis_id])
    assert indexed.command == "index-graph"
    assert str(indexed.source_lineage_id) == axis_id
    proposed = parser.parse_args(["propose-axis", "--planning-run-id", axis_id])
    assert proposed.command == "propose-axis"
    assert str(proposed.planning_run_id) == axis_id
    approved = parser.parse_args(["approve-axis", "--planning-run-id", axis_id])
    assert approved.command == "approve-axis"
    assert str(approved.planning_run_id) == axis_id
    assert approved.narrative_axis_id is None
    with_axis = parser.parse_args(["approve-axis", "--planning-run-id", axis_id, "--narrative-axis-id", project_id])
    assert str(with_axis.narrative_axis_id) == project_id
    reviewed = parser.parse_args(
        ["review-axis-feedback", "--planning-run-id", axis_id, "--feedback-b64", "Zg"]
    )
    assert reviewed.command == "review-axis-feedback"
    assert str(reviewed.planning_run_id) == axis_id
    resolved = parser.parse_args(
        ["resolve-axis-feedback", "--planning-run-id", axis_id, "--choice", "decline"]
    )
    assert resolved.command == "resolve-axis-feedback"
    assert resolved.choice == "decline"
    subparsers = [action for action in parser._actions if getattr(action, "choices", None)]
    commands = set(subparsers[0].choices)
    assert "revise-axis" not in commands
    assert "approve-axis" in commands
    assert "review-axis-feedback" in commands
    assert "resolve-axis-feedback" in commands
    with pytest.raises(SystemExit):
        parser.parse_args(["revise-axis", "--narrative-axis-id", axis_id, "--feedback-b64", "Zg"])
    with pytest.raises(SystemExit):
        parser.parse_args(["approve-axis", "--narrative-axis-id", axis_id])
    with pytest.raises(SystemExit):
        parser.parse_args(["digest-v2", "--request", "gs://bucket/request.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["approve-project-v2", "--execution-run-id", axis_id, "--tokens-per-minute", "1000000"]
        )


def test_canonical_cli_cannot_rewrite_proposed_axis_without_feedback_decision() -> None:
    parser = _parser()
    subparsers = [action for action in parser._actions if getattr(action, "choices", None)]
    commands = set(subparsers[0].choices)
    assert "revise-axis" not in commands
    assert "approve-axis" in commands
    approve_sub = subparsers[0].choices["approve-axis"]
    dests = {getattr(arg, "dest", None) for arg in approve_sub._actions}
    assert "planning_run_id" in dests
    assert "feedback_b64" not in dests
    assert {"review-axis-feedback", "resolve-axis-feedback"} <= commands
    axis_feedback_commands = [
        name
        for name, sub in subparsers[0].choices.items()
        if "axis" in name
        and any(getattr(arg, "dest", None) == "feedback_b64" for arg in sub._actions)
    ]
    assert axis_feedback_commands == ["review-axis-feedback"]
    with pytest.raises(SystemExit):
        parser.parse_args(["revise-axis", "--narrative-axis-id", str(uuid4()), "--feedback-b64", "Zg"])


def test_feedback_is_transport_encoded_but_natural_language() -> None:
    text = "Inclua que o adulto também está sofrendo."
    encoded = base64.urlsafe_b64encode(text.encode()).decode()
    assert _feedback(encoded) == text
    with pytest.raises(ValueError):
        _feedback("")


def test_extract_graph_entrypoint_runs_sync_adapter_without_active_event_loop(monkeypatch, capsys) -> None:
    source_lineage_id = uuid4()
    calls = []

    class Result:
        def model_dump_json(self) -> str:
            return json.dumps({"state": "ok"})

    def run_without_active_loop(value):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("synchronous LlamaIndex adapter ran inside the worker event loop")
        calls.append(value)
        return Result()

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.run_extract_graph_command",
        run_without_active_loop,
    )
    main(["extract-graph", "--source-lineage-id", str(source_lineage_id)])

    assert calls == [source_lineage_id]
    assert json.loads(capsys.readouterr().out) == {"state": "ok"}


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


def test_digest_v2_command_uses_async_opt_in_path_without_loading_v1_application(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("V1 application must not be built")),
    )
    calls = []

    class Result:
        def model_dump_json(self):
            return json.dumps({"state": "understanding_ready"})

    async def run(location, *, tokens_per_minute, max_concurrency, per_project_concurrency):
        calls.append((location, tokens_per_minute, max_concurrency, per_project_concurrency))
        return Result()

    monkeypatch.setattr("comunicamundo_v4.hefesto.worker.run_digest_v2_command", run)
    path = tmp_path / "request.json"
    _write_request(path)

    main(
        [
            "digest-v2",
            "--request",
            str(path),
            "--tokens-per-minute",
            "2000000",
            "--max-concurrency",
            "3",
            "--per-project-concurrency",
            "2",
        ]
    )

    assert calls == [(str(path), 2_000_000, 3, 2)]
    assert json.loads(capsys.readouterr().out) == {"state": "understanding_ready"}


def test_v2_editorial_commands_use_async_opt_in_path_and_explicit_zero_tips(monkeypatch, capsys) -> None:
    execution_run_id = uuid4()
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("V1 application must not be built")),
    )
    calls = []

    class Result:
        def model_dump_json(self):
            return json.dumps({"state": "ok"})

    async def propose(value, **capacity):
        calls.append(("propose", value, capacity))
        return Result()

    async def approve_axis(value, **capacity):
        calls.append(("axis", value, capacity))
        return Result()

    async def approve_project(value, **capacity):
        calls.append(("project-no-tips", value, capacity))
        return Result()

    async def produce(value, **capacity):
        calls.append(("produce", value, capacity))
        return Result()

    monkeypatch.setattr("comunicamundo_v4.hefesto.worker.run_propose_axis_v2_command", propose)
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker.run_approve_axis_v2_command", approve_axis)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.run_approve_project_v2_without_tips_command",
        approve_project,
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker.run_produce_v2_command", produce)
    capacity = ["--tokens-per-minute", "2000000"]

    main(["propose-axis-v2", "--execution-run-id", str(execution_run_id), *capacity])
    assert json.loads(capsys.readouterr().out) == {"state": "ok"}
    main(["approve-axis-v2", "--execution-run-id", str(execution_run_id), *capacity])
    assert json.loads(capsys.readouterr().out) == {"state": "ok"}
    main(
        [
            "approve-project-v2",
            "--execution-run-id",
            str(execution_run_id),
            "--without-tips",
            *capacity,
        ]
    )
    assert json.loads(capsys.readouterr().out) == {"state": "ok"}
    main(["produce-v2", "--execution-run-id", str(execution_run_id), *capacity])
    assert json.loads(capsys.readouterr().out) == {"state": "ok"}
    assert [item[0] for item in calls] == ["propose", "axis", "project-no-tips", "produce"]
    assert all(item[1] == execution_run_id for item in calls)
    assert all(item[2]["tokens_per_minute"] == 2_000_000 for item in calls)


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


def test_approve_axis_command_uses_only_project_runtime(monkeypatch, capsys) -> None:
    from comunicamundo_v4.hefesto.contracts import EditorialProjectProposal, EditorialSubjectProposal
    from test_axis_feedback import _ready

    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    extract_writes = store.extraction_writes
    graph_writes = store.graph_writes
    understanding_writes = store.understanding_writes
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("comunicamundo_v4.hefesto.worker._planning_persistence", lambda: store)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._application",
        lambda: (_ for _ in ()).throw(AssertionError("full Vertex runtime must not be built for approve-axis")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_extraction_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph extraction runtime must not be built for approve-axis")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._graph_indexing_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("graph indexing runtime must not be built for approve-axis")),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._axis_proposal_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("axis proposal runtime must not be built for approve-axis")),
    )

    class FakeBuilder:
        def __init__(self, runtime) -> None:
            assert runtime.project_model == "fake-project"
            assert runtime.project_max_tokens == 5_000
            self.calls = 0

        def build(self, **kwargs):
            self.calls += 1
            assert kwargs["understanding"].synthesis
            assert kwargs["axis"].status.value == "approved"
            return EditorialProjectProposal(
                working_title="Título compacto",
                editorial_promise="Promessa editorial.",
                core_subjects=[
                    EditorialSubjectProposal(
                        title="Assunto",
                        development="Desenvolvimento operacional.",
                    )
                ],
            )

    builder_holder: list[FakeBuilder] = []

    def make_builder(runtime):
        item = FakeBuilder(runtime)
        builder_holder.append(item)
        return item

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker._editorial_project_runtime",
        lambda: EditorialProjectRuntime(
            project_llm=object(),
            project_model="fake-project",
            project_max_tokens=5_000,
            context_window=1_048_576,
        ),
    )
    monkeypatch.setattr("comunicamundo_v4.hefesto.editorial_project.LlamaIndexEditorialProjectBuilder", make_builder)
    main(["approve-axis", "--planning-run-id", str(proposed.planning_run.planning_run_id)])
    result = EditorialProjectProposalResult.model_validate_json(capsys.readouterr().out)
    assert result.planning_run.state is PlanningRunState.PROJECT_PROPOSED
    assert result.human_approval_required is True
    assert result.article_created is False
    assert result.editorial_project.status.value == "proposed"
    assert result.narrative_axis.status.value == "approved"
    assert builder_holder[0].calls == 1
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
    main(["approve-axis", "--planning-run-id", str(proposed.planning_run.planning_run_id)])
    rerun = EditorialProjectProposalResult.model_validate_json(capsys.readouterr().out)
    assert rerun.editorial_project.editorial_project_id == result.editorial_project.editorial_project_id
    assert builder_holder[0].calls == 1
    assert builder_holder[1].calls == 0
    assert store.extraction_writes == extract_writes
    assert store.graph_writes == graph_writes
    assert store.understanding_writes == understanding_writes
