from comunicamundo_v4.hefesto import runtime


class FakeGoogleGenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeEmbedding:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_vertex_runtime_passes_explicit_context_and_stage_token_budgets(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "GoogleGenAI", FakeGoogleGenAI)
    monkeypatch.setattr(runtime, "GoogleGenAIEmbedding", FakeEmbedding)

    built = runtime.build_vertex_runtime(runtime.VertexRuntimeSettings(project="test-project"))

    expected_by_stage = {
        "graph": built.graph_llm,
        "axis": built.director._axis_llm,
        "project": built.director._project_llm,
        "research": built.production._research_llm,
        "architecture": built.production._architecture_llm,
        "writing": built.production._writing_llm,
        "critic": built.production._critic_llm,
        "editing": built.production._editing_llm,
    }
    expected_max_tokens = {
        "graph": 2048,
        "axis": 1200,
        "project": 5000,
        "research": 4096,
        "architecture": 5000,
        "writing": 10000,
        "critic": 3500,
        "editing": 10000,
    }

    assert set(expected_by_stage) == set(expected_max_tokens)
    assert len(expected_by_stage) == 8

    constructors = list(expected_by_stage.values())
    assert all(isinstance(llm, FakeGoogleGenAI) for llm in constructors)
    assert len({id(llm) for llm in constructors}) == 8

    for stage, llm in expected_by_stage.items():
        assert llm.kwargs["context_window"] == 1_048_576
        assert llm.kwargs["max_tokens"] == expected_max_tokens[stage]
        assert llm.kwargs["max_tokens"] is not None
        assert llm.kwargs["max_tokens"] != 65_536

    assert expected_max_tokens["graph"] == 2048
    unique_caps = {llm.kwargs["max_tokens"] for llm in constructors}
    assert unique_caps == {2048, 1200, 5000, 4096, 10000, 3500}
    assert 65_536 not in unique_caps
