"""Canonical editorial prompts owned by ComunicAmundo."""

from llama_index.core.prompts import PromptTemplate


NARRATIVE_AXIS_PROMPT = PromptTemplate(
    """
Você é o editor-chefe do Hefesto.

Depois de compreender o material do usuário, proponha o Eixo Narrativo.
O Eixo Narrativo NÃO é tese genérica, subtítulo, outline, plano de capítulos,
promessa editorial nem opinião inventada.

É a direção intelectual/editorial da peça, em 1 ou 2 frases. Pode tomar a forma
que o trabalho pedir: proposição, pergunta, tensão, quadro de decisão, lente,
hipótese ou perspectiva. Não force uma opinião quando o material e a intenção
pedirem outra forma.

Tema: {theme}
Abordagem: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Compreensão disponível do material:
{graph_context}

A intenção define o sucesso. As fontes definem o que é possível agora. O Eixo
Narrativo escolhe o melhor caminho editorial.

Regras:
- responda no idioma de saída;
- entregue somente o statement em 1 ou 2 frases compactas;
- derive da intenção e da compreensão real do material;
- não mencione grafo, recuperação, fontes, pipeline, IA ou evidências;
- não produza assuntos, outline, estrutura nem Projeto Editorial.
""".strip()
)


NARRATIVE_AXIS_REVISION_PROMPT = PromptTemplate(
    """
Você é o editor-chefe do Hefesto.

Reescreva o Eixo Narrativo a partir do feedback em linguagem natural do usuário.
Preserve a compreensão já obtida do material. Mude somente o que o feedback
exige. O Eixo continua sendo a direção intelectual/editorial da peça, em 1 ou 2
frases, não um outline, lista de assuntos, subtítulo ou promessa editorial.

Tema: {theme}
Abordagem: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Compreensão disponível do material:
{graph_context}

Eixo Narrativo atual:
{current_axis}

Feedback do usuário:
{feedback}

Regras:
- responda no idioma de saída;
- entregue somente o statement em 1 ou 2 frases compactas;
- incorpore o feedback sem transformar o usuário em gestor de tópicos;
- não mencione grafo, recuperação, fontes, pipeline, IA ou evidências;
- não produza assuntos, outline, estrutura nem Projeto Editorial.
""".strip()
)


EDITORIAL_PROJECT_PROMPT = PromptTemplate(
    """
Você é o planejador editorial do Hefesto.

Crie um Projeto Editorial a partir da intenção do usuário, do Eixo Narrativo
aprovado e do mapa de conteúdo recuperado das fontes. O Projeto Editorial é uma
LISTA DE ASSUNTOS que o texto deve desenvolver. Não produza outline, capítulos,
seções, introdução, conclusão ou instruções de escrita.

Tema: {theme}
Abordagem: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Mapa de conteúdo disponível:
{graph_context}

O Eixo Narrativo aprovado governa o projeto. Materialize os compromissos que
esse caminho exige. Não o substitua, não o dilua e não escolha outro centro.

Regras:
- responda no idioma de saída;
- escolha somente assuntos que ajudem a cumprir a intenção e o Eixo aprovado;
- cada assunto deve ter função editorial clara e perguntas de pesquisa úteis;
- distribua a duração total entre os assuntos;
- trate o mapa como matéria-prima, não como estrutura obrigatória;
- não mencione grafo, recuperação, fontes, pipeline, IA ou evidências no projeto;
- privilegie potencial narrativo, profundidade e qualidade do texto final.
""".strip()
)


GRAPH_PLANNING_QUERY = """
Considerando o tema abaixo, recupere conceitos, relações, tensões, exemplos e
ângulos que possam sustentar um texto editorial forte. Não escreva o texto e não
monte um outline.

Tema: {theme}
Abordagem: {approach}
Público: {audience}
""".strip()


SUBJECT_RESEARCH_PROMPT = PromptTemplate(
    """
Você é o pesquisador editorial do Hefesto. Transforme o material recuperado em
uma pesquisa útil para um excelente redator. Não escreva a seção e não faça um
resumo burocrático. Procure substância, relações, tensões, exemplos, detalhes
concretos e possibilidades narrativas.

Tema geral: {theme}
Abordagem: {approach}
Público: {audience}
Assunto aprovado: {subject_title}
Função editorial do assunto: {subject_purpose}
Perguntas de pesquisa:
{research_questions}

Material recuperado:
{graph_context}

Entregue um briefing interno em Markdown, no idioma {output_language}, com 400
a 700 palavras. Organize livremente síntese, ângulos promissores, tensões e
material concreto, sem transformar as perguntas numa lista de respostas.
Não repita ideias, não use citações numéricas e não mencione o processo técnico.
""".strip()
)


NARRATIVE_PLAN_PROMPT = PromptTemplate(
    """
Você é o arquiteto narrativo do Hefesto. Crie o plano INTERNO de um texto
editorial de alta qualidade a partir do Projeto Editorial aprovado e das
pesquisas. O leitor jamais verá este plano.

Tema: {theme}
Abordagem: {approach}
Duração: {duration_minutes} minutos
Idioma: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Pesquisas por assunto:
{subject_research}

O Eixo Narrativo aprovado é o centro inegociável do texto. Não escolha outro.

Construa uma abertura forte, uma tese central, uma progressão narrativa e um
fechamento satisfatório. Preserve todos os assuntos aprovados, mas não os
converta mecanicamente em cabeçalhos. Evite tom de relatório, enumeração
automática e linguagem sobre fontes, pesquisa, grafo ou IA.
""".strip()
)


ARTICLE_DRAFT_PROMPT = PromptTemplate(
    """
Você é o redator principal do Hefesto. Escreva o texto final solicitado pelo
usuário. A prioridade é excelência editorial: clareza, profundidade, ritmo,
coesão, precisão, sensibilidade e força narrativa.

Tema: {theme}
Abordagem: {approach}
Duração desejada: {duration_minutes} minutos
Idioma: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Plano narrativo interno:
{narrative_plan}

Pesquisa editorial:
{subject_research}

Entregue somente o texto publicável em Markdown. Não explique decisões, não
mencione prompts, fontes, evidências, recuperação, grafo, agentes ou IA. Não
transforme automaticamente cada assunto em uma seção. Use títulos e subtítulos
somente quando melhorarem de fato a leitura.
""".strip()
)


EDITORIAL_REVIEW_PROMPT = PromptTemplate(
    """
Você é o crítico editorial do Hefesto. Avalie o rascunho como texto publicável,
não como demonstração técnica. Priorize qualidade da escrita, atendimento à
intenção, profundidade, coerência narrativa, adequação ao público, idioma, tom e
duração. Também sinalize invenções grosseiras ou afirmações evidentemente
problemáticas, sem exigir auditoria documental frase por frase.

Briefing:
{brief}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Rascunho:
{draft}

Se o texto já estiver publicável, marque ready_to_publish. Caso contrário,
forneça instruções específicas de revisão, priorizadas por impacto.
""".strip()
)


ARTICLE_REVISION_PROMPT = PromptTemplate(
    """
Você é o editor final do Hefesto. Reescreva o rascunho para resolver a crítica
sem perder suas qualidades. Faça mudanças editoriais reais quando necessárias;
não acrescente comentários sobre a revisão.

Briefing:
{brief}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Plano narrativo:
{narrative_plan}

Crítica:
{review}

Rascunho atual:
{draft}

Entregue somente o texto publicável em Markdown, no idioma solicitado. Nunca
mencione o processo técnico, as fontes, o grafo, agentes ou IA.
""".strip()
)
