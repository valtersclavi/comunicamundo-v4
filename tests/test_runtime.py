import inspect

from llama_index.llms.google_genai import GoogleGenAI

from comunicamundo_v4.hefesto.runtime import (
    GEMINI_CONTEXT_WINDOW,
    GEMINI_MAX_OUTPUT_TOKENS,
    RESEARCH_MAX_OUTPUT_TOKENS,
    VertexRuntimeSettings,
    build_vertex_runtime,
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
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_vertex_runtime_skips_model_metadata_fetch(monkeypatch) -> None:
    CapturedLLM.calls = []
    monkeypatch.setattr("comunicamundo_v4.hefesto.runtime.GoogleGenAI", CapturedLLM)
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.runtime.GoogleGenAIEmbedding", CapturedEmbedding
    )

    runtime = build_vertex_runtime(VertexRuntimeSettings(project="comunicamundo-v4"))

    assert runtime.planning_workflow is not None
    assert len(CapturedLLM.calls) == 7
    for kwargs in CapturedLLM.calls:
        assert kwargs["max_tokens"] is not None
        assert kwargs["context_window"] == GEMINI_CONTEXT_WINDOW
        assert kwargs["vertexai_config"] == {
            "project": "comunicamundo-v4",
            "location": "us-central1",
        }

    research_call = next(
        call for call in CapturedLLM.calls if call["model"] == "gemini-2.5-flash"
        and call["max_tokens"] == RESEARCH_MAX_OUTPUT_TOKENS
    )
    assert research_call["max_tokens"] == RESEARCH_MAX_OUTPUT_TOKENS
    planning_call = next(
        call for call in CapturedLLM.calls if call["model"] == "gemini-2.5-pro"
    )
    assert planning_call["max_tokens"] == GEMINI_MAX_OUTPUT_TOKENS
