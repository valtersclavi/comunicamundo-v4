# Arquitetura do produto Hefesto

Status: canônica.

## Percurso do usuário

`login da equipe → briefing + fontes → acompanhamento → Projeto Editorial com Eixo Narrativo → aprovação humana → texto final`

A aplicação web e a API são entregues pelo mesmo Cloud Run Service. O navegador envia fontes em partes para a API, URLs públicas também são aceitas e o Cloud Run Job executa o processamento demorado. PostgreSQL é a fonte canônica de estado, estrutura, artefatos e métricas; Cloud Storage preserva arquivos e recibos.

## Processamento editorial

1. A ingestão valida cada formato aceito, preserva o original e normaliza parágrafos completos, tabelas, figuras e proveniência.
2. Parágrafos e frases são gravados no PostgreSQL antes da primeira chamada de modelo.
3. Uma varredura em lotes orientada pelo briefing devolve somente flags compactas: relevante, central, incerto e contexto adjacente necessário.
4. Os parágrafos selecionados seguem como texto original para a compreensão editorial.
5. A estrutura de ideias, perguntas e assuntos parte apenas do briefing ou do pré-PE aprovado. O material selecionado é encaminhado a esses assuntos, sem determinar uma nova agenda.
6. Cada lote medido de trechos originais recebe uma leitura editorial direta. Essa chamada registra entendimento, nuances e evidências por referências. Não há condensação preliminar, resumo intermediário nem chamada para resumir resumos; os resultados dos lotes são preservados sem recompressão pelo modelo.
7. Eixo Narrativo e Projeto Editorial são concebidos juntos e apresentados em uma única aprovação humana.
8. A compreensão produz notas editoriais e referências estáveis aos originais: source_id, paragraph_id e passage_id. O modelo indica posições locais no lote; o programa resolve a identidade real da fonte. Citações devem existir literalmente no trecho referenciado. Notas editoriais não substituem documentos.
9. Depois da aprovação, redação, revisão de conteúdo e correção recebem o estado editorial e os parágrafos originais recuperados pelas referências, sem reabrir o escopo aprovado. Referência ausente causa erro explícito; não há fallback silencioso para resumos. PEs antigos sem referências finas recuperam os originais das fontes já indicadas, sem inventar endereços retrospectivos.
10. Um editor compara a redação com corpus, Eixo e projeto e devolve correções localizadas. O mesmo redator altera somente os pontos pedidos; se não houver defeito, a redação segue intacta.
11. Um redator de estilo recebe somente o texto editorialmente fechado e a especificação do produto. Ele tem liberdade total de forma e nenhuma liberdade de conteúdo.
12. Antes da edição narrativa final, uma verificação compara conteúdo fechado e versão estilizada. Divergências voltam como correções pontuais sobre a versão estilizada.
13. O editor de narrativa lê o texto completo validado e entrega a versão final. Pode fundir e reordenar passagens, condensar argumentos repetidos e reescrever abertura, transições e fechamento. Preserva ideias distintas, fatos, nuances e contradições; a arquitetura inicial serve à redação, sem engessar essa edição. Nenhuma revisão posterior recompõe blocos ou repetições. O artefato é persistido e a retomada não repete etapas concluídas.

Não existem fichas editoriais por parágrafo. O parágrafo é unidade de estrutura e endereço, não uma chamada de inteligência. Nenhum agrupamento usa somente contagem de parágrafos: cada prompt final é medido pelo tokenizer do modelo, com orçamento separado para seleção, compreensão e saída. Parágrafos grandes são divididos preferencialmente entre frases completas; se uma frase exceder o orçamento, ela é subdividida sem perda de caracteres, mantendo a referência ao original.

## Contrato editorial

O briefing contém tema, intenção, público, produto, idioma e duração. Ele orienta todas as decisões sem alterar o que as fontes dizem.

O Projeto Editorial inclui título de trabalho, promessa, Eixo Narrativo e sua justificativa, assuntos sustentados pelas fontes dimensionados para a duração, sugestões opcionais provenientes do modelo e oportunidades que exigem fontes adicionais. A identidade mínima das fontes é preservada durante a compreensão para que convergências, complementos, contradições, contextos e limites possam ser reconhecidos.

A duração orienta a extensão e a proporção editorial conforme o produto. O projeto distribui o tempo completo entre seus assuntos e essa proporção vira uma meta aproximada de palavras por seção, nunca uma faixa mecânica de aceitação. A entrega final é julgada pela compatibilidade substantiva entre o conteúdo editorialmente fechado e o texto estilizado.

## Garantias operacionais

- A mesma solicitação possui identidade determinística e não abre deliberadamente outra execução paga.
- Flags e compreensões parciais concluídas são persistidas antes da unidade seguinte e reutilizadas após falha.
- Chamadas de geração têm timeout; falhas são persistidas e exibidas com sua causa real.
- Toda chamada paga registra tokens, latência, estado, erro e custo tarifado.
- Toda etapa registra duração, estado, chamadas, tokens, custo, erro real e artefatos produzidos.
- A interface oferece uma única aprovação para o Projeto Editorial completo.
- Cada artefato intermediário da produção é persistido. A retomada continua da etapa incompleta sem repetir redação, revisão ou estilização já concluída.
- A mesma entrada atende qualquer tema, fonte, briefing, produto, idioma e duração, sem variante de teste.


## Frozen editorial contract — 2026-09-30

The brief governs both source selection and editorial comprehension. Extract
only evidence, qualifications, context and contradictions needed by its ideas
and questions. Source length or a well-developed source theme does not authorize
another agenda. No topic-specific exceptions or rules designed to fit a test.

Source enrichment is an additive operation on a frozen proposed Editorial
Project. The chosen opportunity scopes research and extraction. Only the newly
acquired sources are selected and read directly; the general scaffold/routing builder
is not called. The contribution is assessed against the brief, opportunity and
frozen PE. Pertinence does not require prior mention in the original brief: the
selected opportunity authorizes new perspectives, examples, contrasts and
dimensions that add color and diversity without replacing the focus. No pertinent addition means the same PE remains unchanged. Otherwise
append the new knowledge unit, attach it to an existing subject or append only
the selected opportunity as a subject, and add the time it needs. Preserve the
previous title, promise, axis, subject content, knowledge units, qualifications
and forbidden inferences. Do not reread old sources to discover additional topics.
Acquisition identity, contribution outcome and enrichment-specific usage are
recorded in the source_enrichment stage and the source is displayed in the UI.
Acquisition records survive failed incorporation; the UI distinguishes failure
from a completed assessment that found no addition, while preserving the old PE.

Writing and narrative editing use the approved PE's current duration, including
additions, rather than the original briefing duration. Duration remains guidance,
not a hard word-count rejection gate. Narrative editing may merge repetitions
and reorder passages while preserving distinct approved content and focus.

These are product invariants. Changes to these editorial rules require explicit
user authorization; audits and test failures do not authorize redesigns.


User remoque is distinct from additive source enrichment. The user's instruction
may revise the PE's axis, scope, subjects, ordering, depth and duration. Preserve
what the instruction does not change. Plan the requested revision from the
current PE and corpus; revisit stored sources only where the user asks for it
or the requested change needs evidence absent from the corpus. Route and read original passages directly
only for those revised subjects. No automatic full rebuild,
no compulsory source rereading and no new external search from remoque. Prior
source-coverage restrictions must not block an explicitly authorized revisit.
The revised PE returns for user approval and invalidates previous text artifacts.

## Referências e retomada — restauração de 30/09/2026

- A seleção persiste flags e identidades dos trechos, vinculadas ao briefing vigente. Flags de outra segmentação ou escopo não são reutilizadas por coincidência de número.
- Os lotes respeitam o orçamento medido. Parágrafos grandes são divididos preferencialmente entre frases completas, com subdivisão sem perda quando uma frase sozinha exceder o orçamento; não são substituídos por resumos nem truncados para caber.
- Leituras diretas concluídas são persistidas em `subject_readings`, com chave do prompt, escopo e identidades dos originais. A retomada reaproveita resultados idênticos; remoque com outro escopo não reutiliza uma leitura inadequada.
- A tabela antiga de condensações permanece apenas por compatibilidade de armazenamento. Nenhum caminho editorial ativo lê essas condensações como material documental.
- Enriquecimento usa somente novas fontes; remoque só revisita originais conforme a revisão autorizada; contribuições pequenas do LLM permanecem explicitamente não documentais.
- Ingestão, aquisição, formatos e o editor de narrativa não foram reformulados nesta restauração.
- A migração 1006 cria apenas a tabela de leituras referenciadas, sem reescrever PEs ou fontes existentes.

## Regra obrigatória de tamanho e tarefas atômicas

Comprimento de conteúdo não autoriza truncamento, resumo substitutivo ou rejeição
por quantidade de caracteres. Citações não têm teto de 280 caracteres: preservam
o texto literal e sua referência ao original, inclusive espaços significativos.

Triagem, encaminhamento por assunto e compreensão operam em lotes. Além de medir
a entrada, tratam `MAX_TOKENS` de saída dividindo o trabalho, nunca repetindo o
mesmo pedido grande. Se o lote tem um único trecho, subdividem sua unidade de
processamento, preferindo fronteiras de frases, depois palavras, sem alterar o
parágrafo persistido. Os resultados mantêm a referência ao parágrafo original.
Uma resposta cortada pelo provedor não é aceita como resultado parcial válido.

A montagem do corpus/PE concatena os resultados validados no código; não existe
chamada de LLM para condensar ou regenerar o conjunto. Leituras concluídas e suas
referências são persistidas e reutilizadas. A remoção do antigo limite de citação
não invalida leituras completas já salvas sob o mesmo briefing e material.

Essa regra deve orientar também qualquer alteração futura da produção textual:
dividir tarefas com preservação do contexto e do contrato narrativo, sem
reintroduzir limites editoriais arbitrários para contornar limites do provedor.
