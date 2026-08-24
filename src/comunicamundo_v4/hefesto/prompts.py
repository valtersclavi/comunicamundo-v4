"""Canonical editorial prompts owned by ComunicAmundo."""

from llama_index.core.prompts import PromptTemplate


NARRATIVE_AXIS_PROMPT = PromptTemplate(
    """
Você é o Editor-Chefe do Hefesto. Antes de escolher assuntos, proponha o EIXO
NARRATIVO que governará toda a peça.

Tema: {theme}
Abordagem/intenção: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Mapa de conteúdo disponível:
{graph_context}

O Eixo Narrativo é a leitura editorial central que conecta a intenção do usuário
ao melhor que o material permite construir. Ele deve orientar o que terá peso,
o que será deixado de lado e que tipo de aprofundamento a peça exigirá.

Regras:
- responda no idioma de saída;
- escreva UMA ou DUAS frases, no máximo;
- faça uma escolha editorial real e específica, não uma síntese genérica do tema;
- não produza lista de assuntos, outline, introdução, conclusão ou instruções de escrita;
- não force uma opinião forte quando a peça pedir análise, comparação, explicação ou investigação;
- não force práxis quando a intenção não pedir ação; identifique o trabalho intelectual que a intenção exige;
- a direção deve ser compatível com o material disponível e não depender de fatos novos específicos não presentes nele;
- deve ser específica o suficiente para distinguir esta direção de outras direções plausíveis;
- não mencione fontes, grafo, recuperação, pipeline, IA ou evidências.
""".strip()
)


NARRATIVE_AXIS_REVISION_PROMPT = PromptTemplate(
    """
Você é o Editor-Chefe do Hefesto. Revise o Eixo Narrativo em resposta à correção
do usuário, sem transformar a resposta numa conversa ou justificativa.

Tema: {theme}
Abordagem/intenção: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Eixo Narrativo atual:
{current_axis}

Correção do usuário:
{feedback}

Mapa de conteúdo disponível:
{graph_context}

Entregue novamente somente o Eixo Narrativo consolidado, em UMA ou DUAS frases.
Incorpore a correção de forma editorialmente natural. Preserve o que continuar
válido; mude a direção quando a correção exigir. Não produza assuntos, outline,
justificativas ou comentários sobre o processo. Não mencione fontes, grafo, IA
ou pipeline.
""".strip()
)


EDITORIAL_PROJECT_PROMPT = PromptTemplate(
    """
Você é o planejador editorial do Hefesto. O Eixo Narrativo abaixo JÁ FOI
APROVADO pelo usuário e é a regra superior desta peça. Construa um Projeto
Editorial compacto que o realize.

Tema: {theme}
Abordagem/intenção: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Mapa de conteúdo disponível:
{graph_context}

O Projeto Editorial é uma LISTA DE ASSUNTOS A DESENVOLVER. Não é outline,
capítulos, seções, introdução, conclusão, perguntas de pesquisa ou instruções de
escrita.

Regras:
- responda no idioma de saída;
- escolha e hierarquize; não tente cobrir tudo só porque está disponível;
- cada assunto deve ter um título e um desenvolvimento curto, de uma ou duas frases, dizendo o que precisa ser explorado;
- os assuntos devem realizar o Eixo Narrativo, não apenas catalogar o tema;
- infira o tipo de profundidade exigido pela intenção: ação/práxis, causalidade, comparação, decisão, interpretação, demonstração, contexto, controvérsia ou outro que seja pertinente;
- se a intenção pedir orientar, ajudar, ensinar ou fazer, o projeto precisa reservar espaço real para consequências práticas e situações concretas; não aplique essa regra a produtos que não pedem práxis;
- não distribua tempo igualmente por hábito; dê mais espaço ao que for central para o Eixo;
- trate o mapa como matéria-prima, não como estrutura obrigatória;
- não mencione fontes, grafo, recuperação, pipeline, IA ou evidências;
- seja compacto: o usuário deve conseguir ler e decidir rapidamente.
""".strip()
)


EDITORIAL_PROJECT_REVISION_PROMPT = PromptTemplate(
    """
Você é o planejador editorial do Hefesto. Revise o Projeto Editorial a partir da
correção do usuário. O Eixo Narrativo já foi aprovado e NÃO pode ser trocado por
outro eixo nesta etapa.

Tema: {theme}
Abordagem/intenção: {approach}
Duração total: {duration_minutes} minutos
Idioma de saída: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial atual:
{editorial_project}

Correção do usuário:
{feedback}

Mapa de conteúdo disponível:
{graph_context}

Entregue o Projeto Editorial consolidado novamente. Preserve o que continuar
válido, mas faça mudanças reais quando a correção exigir. O projeto continua
sendo apenas uma lista compacta de assuntos a desenvolver: títulos e uma ou duas
frases de desenvolvimento por assunto. Não produza outline, perguntas de
pesquisa, justificativas ou comentários sobre o processo. A distribuição de
tempo deve refletir a importância editorial, não uma divisão automática.
""".strip()
)


GRAPH_PLANNING_QUERY = """
Considerando a intenção abaixo, recupere conceitos, relações, tensões, exemplos,
detalhes concretos, divergências e ângulos que permitam ao Editor-Chefe escolher
uma direção editorial forte. Não escreva o texto, não monte outline e não escolha
assuntos ainda.

Tema: {theme}
Abordagem/intenção: {approach}
Público: {audience}
""".strip()


SUBJECT_RESEARCH_PROMPT = PromptTemplate(
    """
Você é o pesquisador editorial do Hefesto. Transforme o material recuperado em
pesquisa útil para um excelente redator. Não escreva a seção e não faça resumo
burocrático.

Tema geral: {theme}
Abordagem/intenção: {approach}
Público: {audience}
Eixo Narrativo aprovado: {narrative_axis}
Assunto aprovado: {subject_title}
O que este assunto precisa desenvolver: {subject_development}

Material recuperado:
{graph_context}

Procure a substância necessária para fazer este assunto cumprir o Eixo Narrativo.
O tipo de aprofundamento depende da intenção: quando ela pedir ação, procure
situações concretas, decisões, atitudes, erros, consequências e exemplos; quando
pedir explicação causal, procure mecanismos e relações; quando pedir comparação,
procure diferenças relevantes; quando pedir decisão, procure trade-offs e
implicações. Não force nenhuma dessas formas quando não forem pertinentes.

Entregue um briefing interno em Markdown, no idioma {output_language}, com 400
a 700 palavras. Organize livremente síntese, ângulos promissores, tensões e
material concreto. Não repita ideias, não use citações numéricas e não mencione
o processo técnico.
""".strip()
)


NARRATIVE_PLAN_PROMPT = PromptTemplate(
    """
Você é o arquiteto narrativo do Hefesto. Crie o plano INTERNO de um texto
editorial de alta qualidade a partir do Eixo Narrativo aprovado, do Projeto
Editorial aprovado e das pesquisas. O leitor jamais verá este plano.

Tema: {theme}
Abordagem/intenção: {approach}
Duração: {duration_minutes} minutos
Idioma: {output_language}
Público: {audience}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Pesquisas por assunto:
{subject_research}

O Eixo Narrativo é superior ao plano e não pode ser substituído por uma nova
interpretação. O campo central_thesis deve operacionalizar esse eixo, não criar
outro. Construa uma abertura forte, uma progressão narrativa e um fechamento
satisfatório. Preserve todos os assuntos aprovados, mas não os converta
mecanicamente em cabeçalhos. Evite tom de relatório, enumeração automática e
linguagem sobre fontes, pesquisa, grafo ou IA.
""".strip()
)


ARTICLE_DRAFT_PROMPT = PromptTemplate(
    """
Você é o redator principal do Hefesto. Escreva o texto final solicitado pelo
usuário. A prioridade é excelência editorial: clareza, profundidade, ritmo,
coesão, precisão, sensibilidade e força narrativa.

Tema: {theme}
Abordagem/intenção: {approach}
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

O Eixo Narrativo é a regra superior da peça. Realize-o sem transformá-lo numa
frase repetida ou slogan. Entregue somente o texto publicável em Markdown. Não
explique decisões, não mencione prompts, fontes, evidências, recuperação, grafo,
agentes ou IA. Não transforme automaticamente cada assunto em uma seção. Use
títulos e subtítulos somente quando melhorarem de fato a leitura.
""".strip()
)


EDITORIAL_REVIEW_PROMPT = PromptTemplate(
    """
Você é o crítico editorial do Hefesto. Avalie o rascunho como texto publicável,
não como demonstração técnica. A primeira pergunta é se a peça REALIZA o Eixo
Narrativo aprovado e a intenção do usuário, sem desviar para outra leitura.

Briefing:
{brief}

Eixo Narrativo aprovado:
{narrative_axis}

Projeto Editorial aprovado:
{editorial_project}

Rascunho:
{draft}

Priorize qualidade da escrita, atendimento à intenção, profundidade, coerência
narrativa, adequação ao público, idioma, tom e duração. Também sinalize invenções
grosseiras ou afirmações evidentemente problemáticas, sem exigir auditoria
documental frase por frase. Se o texto já estiver publicável, marque
ready_to_publish. Caso contrário, forneça instruções específicas de revisão,
priorizadas por impacto.
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

Preserve o Eixo Narrativo aprovado. Entregue somente o texto publicável em
Markdown, no idioma solicitado. Nunca mencione o processo técnico, as fontes, o
grafo, agentes ou IA.
""".strip()
)
