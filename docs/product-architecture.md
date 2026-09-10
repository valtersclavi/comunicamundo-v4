# Arquitetura do produto Hefesto

Status: canônica.

## Percurso do usuário

`login da equipe → briefing + fontes → acompanhamento → Projeto Editorial com Eixo Narrativo → aprovação humana → texto final`

A aplicação web e a API são entregues pelo mesmo Cloud Run Service. O navegador envia PDFs em partes para a API, URLs públicas também são aceitas e o Cloud Run Job executa o processamento demorado. PostgreSQL é a fonte canônica de estado, estrutura, artefatos e métricas; Cloud Storage preserva arquivos e recibos.

## Processamento editorial

1. A ingestão valida cada PDF, preserva o original e normaliza parágrafos completos.
2. Parágrafos e frases são gravados no PostgreSQL antes da primeira chamada de modelo.
3. Uma varredura em lotes orientada pelo briefing devolve somente flags compactas: relevante, central, incerto e contexto adjacente necessário.
4. Os parágrafos selecionados seguem como texto original para a compreensão editorial.
5. Se o material selecionado couber no orçamento medido de tokens, uma única chamada produz o Projeto Editorial completo.
6. Se não couber, partes limitadas por tokens são compreendidas e persistidas; consolidações adicionais só existem quando outro limite real exigir.
7. Eixo Narrativo e Projeto Editorial são concebidos juntos e apresentados em uma única aprovação humana.
8. Depois da aprovação, o texto final é escrito a partir do projeto vinculante e do mesmo material selecionado das fontes.

Não existem fichas editoriais por parágrafo. O parágrafo é unidade de estrutura e endereço, não uma chamada de inteligência. Nenhum agrupamento usa somente contagem de parágrafos: cada prompt final é medido pelo tokenizer do modelo, com orçamento separado para seleção, compreensão e saída. Um parágrafo excepcionalmente grande só pode ser dividido entre frases completas.

## Contrato editorial

O briefing contém tema, intenção, público, produto, idioma e duração. Ele orienta todas as decisões sem alterar o que as fontes dizem.

O Projeto Editorial inclui título de trabalho, promessa, Eixo Narrativo e sua justificativa, assuntos sustentados pelas fontes dimensionados para a duração, sugestões opcionais provenientes do modelo e oportunidades que exigem fontes adicionais. A identidade mínima das fontes é preservada durante a compreensão para que convergências, complementos, contradições, contextos e limites possam ser reconhecidos.

## Garantias operacionais

- A mesma solicitação possui identidade determinística e não abre deliberadamente outra execução paga.
- Flags e compreensões parciais concluídas são persistidas antes da unidade seguinte e reutilizadas após falha.
- Chamadas de geração têm timeout; falhas são persistidas e exibidas com sua causa real.
- Toda chamada paga registra tokens, latência, estado, erro e custo tarifado.
- Toda etapa registra duração, estado e artefatos produzidos.
- A interface oferece uma única aprovação para o Projeto Editorial completo.
- A geração do texto final é persistida e idempotente; retomá-la depois de uma falha não repete um texto já concluído.
- A mesma entrada atende qualquer tema, fonte, briefing, produto, idioma e duração, sem variante de teste.
