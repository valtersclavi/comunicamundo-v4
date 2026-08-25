"""Lock the known-good pre-Narrative-Axis plan path. Do not rewrite the graph.

Baseline: 7393829260c98ad8c1a04c87978cc4ad95430d6d (last successful
pre-Narrative-Axis plan architecture). Current main after PR #14 still uses
that graph call. PR #18's sync-extractor rewrite is not an incident fix.

These tests are unpaid: they inspect source and constructors only.
"""

from __future__ import annotations

import inspect

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.worker import PlanningJobRequest, _run
from comunicamundo_v4.hefesto.workflow import EditorialPlanningWorkflow


def test_graph_build_matches_known_good_property_graph_call() -> None:
    source = inspect.getsource(ProjectGraph.build)
    assert "PropertyGraphIndex.from_documents(" in source
    assert "embed_kg_nodes=True" in source
    assert "use_async=True" in source
    assert "show_progress=False" in source
    assert "kg_extractors" not in source
    assert "use_async=False" not in source
    assert "SyncLLMPathExtractor" not in source
    assert "GraphRAG" not in source


def test_workflow_still_builds_the_graph_in_a_worker_thread() -> None:
    source = inspect.getsource(EditorialPlanningWorkflow.build_graph)
    assert "asyncio.to_thread" in source
    assert "ProjectGraph.build" in source
    assert "use_async=False" not in source
    assert "context_for_editorial_project" not in source


def test_current_plan_validates_the_request_before_runtime_construction() -> None:
    """Post-#14 order. Pre-#14 (tpp2s/tdkmc) built Vertex first and never validated."""

    source = inspect.getsource(_run)
    validate_at = source.index("PlanningJobRequest.model_validate")
    runtime_at = source.index("_build_application")
    assert validate_at < runtime_at


def test_planning_request_contract_is_still_the_baseline_strict_object() -> None:
    fields = set(PlanningJobRequest.model_fields)
    assert fields == {"project_id", "brief", "sources"}
    assert PlanningJobRequest.model_config.get("extra") == "forbid"


def test_embedding_constructor_does_not_call_models_get() -> None:
    source = inspect.getsource(GoogleGenAIEmbedding.__init__)
    assert "google.genai.Client(" in source
    assert "models.get" not in source


def test_google_genai_still_fetches_metadata_when_either_limit_is_missing() -> None:
    source = inspect.getsource(GoogleGenAI.__init__)
    assert "if max_tokens is None or context_window is None" in source
    assert "client.models.get(model=model)" in source
