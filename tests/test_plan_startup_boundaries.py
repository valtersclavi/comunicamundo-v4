"""Atomic unpaid plan-startup boundaries versus the last successful graph path.

These tests prove constructor/library behavior. They do not call Vertex, GCS,
or Firestore, and they do not rebuild or persist a PropertyGraphIndex.
"""

from __future__ import annotations

import inspect

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.persistence import GcpEditorialPersistence
from comunicamundo_v4.hefesto.runtime import (
    RuntimeProfile,
    VertexRuntimeSettings,
    build_vertex_runtime,
)
from comunicamundo_v4.hefesto.worker import _run
from comunicamundo_v4.hefesto.workflow import EditorialPlanningWorkflow


class _FakeModels:
    def __init__(self) -> None:
        self.get_calls: list[dict] = []

    def get(self, model=None, **kwargs):
        self.get_calls.append({"model": model, **kwargs})
        raise AssertionError(f"models.get must not be called during construction: {model}")


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.models = _FakeModels()
        type(self).instances.append(self)


def _patch_genai_client(monkeypatch) -> None:
    _FakeClient.instances = []
    monkeypatch.setattr("google.genai.Client", _FakeClient)


def test_graph_build_still_uses_the_proven_property_graph_call() -> None:
    source = inspect.getsource(ProjectGraph.build)
    assert "PropertyGraphIndex.from_documents(" in source
    assert "embed_kg_nodes=True" in source
    assert "use_async=True" in source
    assert "show_progress=False" in source
    assert "GraphRAG" not in source
    workflow_source = inspect.getsource(EditorialPlanningWorkflow.build_graph)
    assert "ProjectGraph.build" in workflow_source
    assert "context_for_editorial_project" not in workflow_source


def test_embedding_constructor_does_not_fetch_model_metadata() -> None:
    source = inspect.getsource(GoogleGenAIEmbedding.__init__)
    assert "google.genai.Client(" in source
    assert "models.get" not in source


def test_google_genai_still_fetches_metadata_when_a_limit_is_missing() -> None:
    source = inspect.getsource(GoogleGenAI.__init__)
    assert "if max_tokens is None or context_window is None" in source
    assert "client.models.get(model=model)" in source


def test_worker_validates_the_planning_request_before_runtime() -> None:
    source = inspect.getsource(_run)
    validate_at = source.index("PlanningJobRequest.model_validate")
    runtime_at = source.index("_build_application")
    assert validate_at < runtime_at


def test_plan_constructors_do_not_call_models_get(monkeypatch) -> None:
    _patch_genai_client(monkeypatch)
    runtime = build_vertex_runtime(
        VertexRuntimeSettings(project="comunicamundo-v4", location="us-central1"),
        profile=RuntimeProfile.PLAN,
    )

    assert runtime.planning_workflow is not None
    assert runtime.production_workflow is None
    assert runtime.editorial_planner is None
    assert len(_FakeClient.instances) == 3
    assert all(client.models.get_calls == [] for client in _FakeClient.instances)
    assert {client.kwargs.get("vertexai") for client in _FakeClient.instances} == {True}
    assert {client.kwargs.get("project") for client in _FakeClient.instances} == {
        "comunicamundo-v4"
    }


def test_embedding_construction_alone_does_not_call_models_get(monkeypatch) -> None:
    _patch_genai_client(monkeypatch)
    embedding = GoogleGenAIEmbedding(
        model_name="gemini-embedding-001",
        vertexai_config={"project": "comunicamundo-v4", "location": "us-central1"},
    )
    assert embedding.model_name == "gemini-embedding-001"
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].models.get_calls == []


def test_missing_context_window_still_triggers_models_get(monkeypatch) -> None:
    _patch_genai_client(monkeypatch)
    try:
        GoogleGenAI(
            model="gemini-2.5-flash",
            temperature=0.1,
            max_tokens=2048,
            vertexai_config={"project": "comunicamundo-v4", "location": "us-central1"},
        )
    except AssertionError as error:
        assert "models.get must not be called" in str(error)
    else:
        raise AssertionError("GoogleGenAI skipped models.get without context_window")
    assert _FakeClient.instances[0].models.get_calls == [{"model": "gemini-2.5-flash"}]


def test_persistence_settings_do_not_construct_vertex_clients(monkeypatch) -> None:
    constructed: dict[str, int] = {"storage": 0, "firestore": 0, "genai": 0}

    class FakeStorage:
        def __init__(self, *args, **kwargs) -> None:
            constructed["storage"] += 1

        def bucket(self, name: str):
            return object()

    class FakeFirestore:
        def __init__(self, *args, **kwargs) -> None:
            constructed["firestore"] += 1

    def forbidden_genai(*args, **kwargs):
        constructed["genai"] += 1
        raise AssertionError("persistence must not construct google.genai.Client")

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.persistence.storage.Client", FakeStorage
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.persistence.firestore.Client", FakeFirestore
    )
    monkeypatch.setattr("google.genai.Client", forbidden_genai)

    from comunicamundo_v4.hefesto.persistence import GcpPersistenceSettings

    persistence = GcpEditorialPersistence.from_settings(
        GcpPersistenceSettings(project="comunicamundo-v4", bucket="unused")
    )
    assert persistence.artifacts is not None
    assert persistence.repository is not None
    assert constructed == {"storage": 1, "firestore": 1, "genai": 0}
