from comunicamundo_v4.hefesto.runtime import VertexRuntimeSettings


def test_production_stages_have_thinking_headroom() -> None:
    settings = VertexRuntimeSettings(project="test-project")

    assert settings.research_max_tokens >= 12_000
    assert settings.architecture_max_tokens >= 12_000
    assert settings.writing_max_tokens >= 24_000
    assert settings.critic_max_tokens >= 10_000
    assert settings.editing_max_tokens >= 24_000
