from comunicamundo_v4.hefesto.prompts import EDITORIAL_PROJECT_PROMPT, NARRATIVE_AXIS_PROMPT


def test_axis_prompt_is_prior_to_subject_selection_and_short() -> None:
    rendered = NARRATIVE_AXIS_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        graph_context="conteúdo",
    )
    assert "UMA ou DUAS frases" in rendered
    assert "não escolha assuntos ainda" in rendered or "não produza lista de assuntos" in rendered
    assert "não force práxis" in rendered


def test_project_prompt_keeps_outline_and_research_questions_internal() -> None:
    rendered = EDITORIAL_PROJECT_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        narrative_axis="Eixo aprovado",
        graph_context="conteúdo",
    )
    assert "LISTA DE ASSUNTOS" in rendered
    assert "perguntas de pesquisa" in rendered
    assert "não tente cobrir tudo" in rendered
    assert "não aplique essa regra a produtos que não pedem práxis" in rendered
