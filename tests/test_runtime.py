import inspect

import pytest
from llama_index.llms.google_genai import GoogleGenAI

from comunicamundo_v4.hefesto.runtime import (
    ARCHITECTURE_MAX_OUTPUT_TOKENS,
    AXIS_MAX_OUTPUT_TOKENS,
    CRITIC_MAX_OUTPUT_TOKENS,
    EDITING_MAX_OUTPUT_TOKENS,
    GEMINI_CONTEXT_WINDOW,
    GRAPH_MAX_OUTPUT_TOKENS,
    PROJECT_MAX_OUTPUT_TOKENS,
    PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS,
    RESEARCH_MAX_OUTPUT_TOKENS,
    WRITING_MAX_OUTPUT_TOKENS,
    RuntimeProfile,
    VertexRuntimeSettings,
    build_vertex_runtime,
    profile_for_command,
)


def test_google_genai_fetches_model_meta_unless_both_limits_are_set() -> None:
    source = inspect.getsource(GoogleGenAI.__init__)
    assert "if max_tokens is None or context_window is None" in source
    assert "client.models.get(model=model)" in source


class CapturedLLM:
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        type(self).calls.append(kwargs)


class CapturedEmbedding:
    constructed = 0

    def __init__(self, **kwargs) -> None:
        type(self).constructed += 1
        self.kwargs = kwargs


def _patch_clients(monkeypatch) -> None:
    CapturedLLM.calls = []
    CapturedEmbedding.constructed = 0
    monkeypatch.setattr("comunicamundo_v4.hefesto.runtime.GoogleGenAI", CapturedLLM)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.runtime.GoogleGenAIEmbedding", CapturedEmbedding
    )


def _assert_stage_limits(calls: list[dict]) -> None:
    assert calls
    for kwargs in calls:
        assert kwargs["max_tokens"] is not None
        assert kwargs["max_tokens"] < PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS
        assert kwargs["max_tokens"] != PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS
        assert kwargs["context_window"] == GEMINI_CONTEXT_WINDOW


def test_plan_does_not_instantiate_production_llms(monkeypatch) -> None:
    _patch_clients(monkeypatch)
    runtime = build_vertex_runtime(
        VertexRuntimeSettings(project="comunicamundo-v4"),
        profile=RuntimeProfile.PLAN,
    )

    assert runtime.planning_workflow is not None
    assert runtime.axis_planner is not None
    assert runtime.editorial_planner is None
    assert runtime.production_workflow is None
    assert len(CapturedLLM.calls) == 2
    assert CapturedEmbedding.constructed == 1
    _assert_stage_limits(CapturedLLM.calls)

    models = {call["model"] for call in CapturedLLM.calls}
    assert models == {"gemini-2.5-flash", "gemini-2.5-pro"}
    graph = next(call for call in CapturedLLM.calls if call["temperature"] == 0.1)
    axis = next(call for call in CapturedLLM.calls if call["temperature"] == 0.4)
    assert graph["max_tokens"] == GRAPH_MAX_OUTPUT_TOKENS
    assert axis["max_tokens"] == AXIS_MAX_OUTPUT_TOKENS


def test_no_stage_defaults_to_published_model_maximum(monkeypatch) -> None:
    _patch_clients(monkeypatch)
    settings = VertexRuntimeSettings(project="comunicamundo-v4")
    for profile in RuntimeProfile:
        CapturedLLM.calls = []
        CapturedEmbedding.constructed = 0
        build_vertex_runtime(settings, profile=profile)
        if profile is RuntimeProfile.APPROVE:
            assert CapturedLLM.calls == []
            continue
        _assert_stage_limits(CapturedLLM.calls)

    assert settings.graph_max_tokens == GRAPH_MAX_OUTPUT_TOKENS
    assert settings.axis_max_tokens == AXIS_MAX_OUTPUT_TOKENS
    assert settings.project_max_tokens == PROJECT_MAX_OUTPUT_TOKENS
    assert settings.research_max_tokens == RESEARCH_MAX_OUTPUT_TOKENS
    assert settings.architecture_max_tokens == ARCHITECTURE_MAX_OUTPUT_TOKENS
    assert settings.writing_max_tokens == WRITING_MAX_OUTPUT_TOKENS
    assert settings.critic_max_tokens == CRITIC_MAX_OUTPUT_TOKENS
    assert settings.editing_max_tokens == EDITING_MAX_OUTPUT_TOKENS
    for cap in (
        settings.graph_max_tokens,
        settings.axis_max_tokens,
        settings.project_max_tokens,
        settings.research_max_tokens,
        settings.architecture_max_tokens,
        settings.writing_max_tokens,
        settings.critic_max_tokens,
        settings.editing_max_tokens,
    ):
        assert 0 < cap < PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS


def test_produce_uses_stage_specific_caps_not_model_maximum(monkeypatch) -> None:
    _patch_clients(monkeypatch)
    runtime = build_vertex_runtime(
        VertexRuntimeSettings(project="comunicamundo-v4"),
        profile=RuntimeProfile.PRODUCE,
    )

    assert runtime.planning_workflow is None
    assert runtime.production_workflow is not None
    assert len(CapturedLLM.calls) == 6
    _assert_stage_limits(CapturedLLM.calls)
    by_tokens = {(call["model"], call["temperature"], call["max_tokens"]) for call in CapturedLLM.calls}
    assert (
        "gemini-2.5-flash",
        0.1,
        GRAPH_MAX_OUTPUT_TOKENS,
    ) in by_tokens
    assert (
        "gemini-2.5-flash",
        0.2,
        RESEARCH_MAX_OUTPUT_TOKENS,
    ) in by_tokens
    assert (
        "gemini-2.5-pro",
        0.5,
        ARCHITECTURE_MAX_OUTPUT_TOKENS,
    ) in by_tokens
    assert (
        "gemini-2.5-pro",
        0.7,
        WRITING_MAX_OUTPUT_TOKENS,
    ) in by_tokens
    assert (
        "gemini-2.5-flash",
        0.2,
        CRITIC_MAX_OUTPUT_TOKENS,
    ) in by_tokens
    assert (
        "gemini-2.5-pro",
        0.5,
        EDITING_MAX_OUTPUT_TOKENS,
    ) in by_tokens


def test_approve_constructs_no_vertex_clients(monkeypatch) -> None:
    _patch_clients(monkeypatch)
    runtime = build_vertex_runtime(
        VertexRuntimeSettings(project="comunicamundo-v4"),
        profile=RuntimeProfile.APPROVE,
    )
    assert runtime.production_workflow is None
    assert runtime.planning_workflow is None
    assert CapturedLLM.calls == []
    assert CapturedEmbedding.constructed == 0


def test_non_produce_profiles_omit_production_llms(monkeypatch) -> None:
    _patch_clients(monkeypatch)
    settings = VertexRuntimeSettings(project="comunicamundo-v4")
    for profile in (
        RuntimeProfile.PLAN,
        RuntimeProfile.AXIS,
        RuntimeProfile.PROJECT,
        RuntimeProfile.APPROVE,
    ):
        CapturedLLM.calls = []
        runtime = build_vertex_runtime(settings, profile=profile)
        assert runtime.production_workflow is None
        assert all(call["max_tokens"] != PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS for call in CapturedLLM.calls)

    CapturedLLM.calls = []
    project_runtime = build_vertex_runtime(settings, profile=RuntimeProfile.PROJECT)
    assert project_runtime.editorial_planner is not None
    assert project_runtime.planning_workflow is None
    project_llm = next(call for call in CapturedLLM.calls if call["temperature"] == 0.4)
    assert project_llm["max_tokens"] == PROJECT_MAX_OUTPUT_TOKENS


def test_command_profiles_keep_plan_off_production() -> None:
    assert profile_for_command("plan") is RuntimeProfile.PLAN
    assert profile_for_command("revise-axis") is RuntimeProfile.AXIS
    assert profile_for_command("approve-axis") is RuntimeProfile.PROJECT
    assert profile_for_command("produce") is RuntimeProfile.PRODUCE
    assert profile_for_command("approve") is RuntimeProfile.APPROVE


def test_stage_cap_rejects_published_model_maximum() -> None:
    with pytest.raises(ValueError, match="published model maximum"):
        build_vertex_runtime(
            VertexRuntimeSettings(
                project="comunicamundo-v4",
                graph_max_tokens=PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS,
            ),
            profile=RuntimeProfile.PLAN,
        )
