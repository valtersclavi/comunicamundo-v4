from comunicamundo_v4.hefesto.prompts import EDITORIAL_PROJECT_PROMPT


def test_project_prompt_keeps_outline_internal() -> None:
    rendered = EDITORIAL_PROJECT_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        graph_context="conteúdo",
    )

    assert "LISTA DE ASSUNTOS" in rendered
    assert "Não produza outline" in rendered
    assert "não mencione grafo" in rendered

