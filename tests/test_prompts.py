from comunicamundo_v4.hefesto.prompts import (
    EDITORIAL_PROJECT_PROMPT,
    NARRATIVE_AXIS_PROMPT,
)


def test_project_prompt_keeps_outline_internal() -> None:
    rendered = EDITORIAL_PROJECT_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        graph_context="conteúdo",
        narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
    )

    assert "LISTA DE ASSUNTOS" in rendered
    assert "Não produza outline" in rendered
    assert "não mencione grafo" in rendered
    assert "Eixo Narrativo aprovado" in rendered


def test_axis_prompt_asks_for_a_compact_editorial_direction() -> None:
    rendered = NARRATIVE_AXIS_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        graph_context="conteúdo",
    )

    assert "1 ou 2 frases" in rendered
    assert "outline" in rendered
    assert "não mencione grafo" in rendered

