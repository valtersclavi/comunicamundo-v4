from comunicamundo_v4.hefesto import runtime


def test_vertex_runtime_passes_explicit_context_and_stage_token_budgets(monkeypatch) -> None:
    llm_calls: list[dict] = []

    class FakeGoogleGenAI:
        def __init__(self, **kwargs):
            llm_calls.append(kwargs)

    class FakeEmbedding:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(runtime, "GoogleGenAI", FakeGoogleGenAI)
    monkeypatch.setattr(runtime, "GoogleGenAIEmbedding", FakeEmbedding)

    settings = runtime.VertexRuntimeSettings(project="test-project")
    built = runtime.build_vertex_runtime(settings)

    assert built.graph_llm is not None
    assert len(llm_calls) == 8

    expected = [
        (settings.graph_model, settings.graph_max_tokens),
        (settings.axis_model, settings.axis_max_tokens),
        (settings.project_model, settings.project_max_tokens),
        (settings.research_model, settings.research_max_tokens),
        (settings.architecture_model, settings.architecture_max_tokens),
        (settings.writing_model, settings.writing_max_tokens),
        (settings.critic_model, settings.critic_max_tokens),
        (settings.editing_model, settings.editing_max_tokens),
    ]

    assert [(call["model"], call["max_tokens"]) for call in llm_calls] == expected
    assert all(call["context_window"] == runtime.GEMINI_2_5_CONTEXT_WINDOW for call in llm_calls)
    assert all(call["max_tokens"] is not None for call in llm_calls)
    assert all(call["max_tokens"] < 65_536 for call in llm_calls)
    assert len({call["max_tokens"] for call in llm_calls}) > 1
