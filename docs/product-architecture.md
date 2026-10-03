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
dimensions that add color and diversity without replacing the focus. The selected
promise is a mandatory primary briefing addendum. If the original does not fulfill
it, preserve the PE and promise and continue searching within the existing four
rounds. Acquisition alone is not completion. On successful fulfillment,
append the new knowledge unit, attach it to an existing subject or append only
the selected opportunity as a subject, expose the additional arguments in its
depth and add strictly positive time. Preserve the
previous title, promise, axis, subject content, knowledge units, qualifications
and forbidden inferences. Do not reread old sources to discover additional topics.
Acquisition identity, contribution outcome and enrichment-specific usage are
recorded in the source_enrichment stage and the source is displayed in the UI.
Acquisition records survive failed incorporation; the UI distinguishes failure
from a source rejected for not fulfilling the promise, while preserving the old PE.

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
por quantidade de caracteres. Os originais permanecem completos no banco,
inclusive espaços significativos. Evidências retornam análise e referências,
sem exigir cópia literal dos trechos pelo LLM.

Triagem, encaminhamento por assunto e compreensão operam em lotes. Além de medir
a entrada, tratam `MAX_TOKENS` de saída dividindo o trabalho, nunca repetindo o
mesmo pedido grande. Se o lote tem um único trecho, subdividem sua unidade de
processamento, preferindo fronteiras de frases, depois palavras, sem alterar o
parágrafo persistido. Os resultados mantêm a referência ao parágrafo original.
Uma resposta cortada pelo provedor não é aceita como resultado parcial válido.

A montagem do corpus/PE concatena os resultados validados no código; não existe
chamada de LLM para condensar ou regenerar o conjunto. Leituras concluídas e suas
referências são persistidas e reutilizadas. O contrato de leitura sem recitação
tem identidade própria de cache. PEs antigos continuam legíveis; cópias literais
legadas são descartadas ao carregar as evidências, preservando análise e referências.

A mesma regra é aplicada na produção textual, preservando contexto, referências
e o contrato narrativo. Todas as chamadas editoriais usam o mecanismo comum,
além da subdivisão semântica já existente na triagem e compreensão.


## Processamento comum de entradas e respostas em partes — 02/10/2026

`atomic.py` é utilizado pelo Hefesto e pelo editor de fontes. Cobre pré-PE,
busca/julgamento de candidatos, PE, sugestões, enriquecimento, remoque,
arquitetura narrativa, redação, revisão de conteúdo, correção, estilo,
verificações e edição narrativa final. A regra de desenvolvimento está também
em `AGENTS.md`, para não ser novamente implementada apenas em algumas etapas.

Chamadas pequenas conservam a tarefa e o schema originais. A entrada é medida
incluindo instruções e schema. Uma entrada maior que o orçamento, uma extensão
prevista de saída que não caiba, ou uma resposta interrompida por limite aciona
leitura/escrita em partes. O orçamento é da operação, não um limite do artefato.

O pedido completo permanece um objeto original endereçável. As partes entregam
janelas literais, com posições, sem condensações. O modelo pode reler qualquer
janela do pedido e do resultado em construção; a conclusão exige que todo o
pedido tenha sido disponibilizado. As operações escrevem ou corrigem partes de
UM artefato, sem gerar um novo título, introdução ou conclusão para cada parte.
O editor continua podendo corrigir, fundir e reordenar conteúdo conforme a
instrução original. O sistema só retorna o resultado após validar o JSON
completo contra o schema editorial original.

A migração 1007 cria `atomic_model_tasks`. Cada operação concluída grava o
resultado em construção, a cobertura das janelas e o estado de continuação.
A chave inclui tarefa, instruções, pedido completo e schema; escopos diferentes
não reutilizam progresso inadequado. A retomada não reinicia as partes já
escritas. Respostas cortadas não são incorporadas como operações válidas.

Cada chamada física, inclusive tentativas que atingiram o limite, mantém tokens,
latência, custo e estado. Na retomada, resultados completos em cache não fazem
novas chamadas. Cálculos de métricas distinguem o artefato lógico das operações
físicas usadas para produzi-lo. Repetição de uma operação sem progresso é erro
de execução do modelo, não um limite de tamanho do conteúdo.

Foram retirados os tetos editoriais de quantidade de fontes, queries e candidatos,
o limite textual do remoque, o maxlength do tema na interface e a rejeição de
metadados de briefing acima de 1 MB. Também foram removidos cortes silenciosos
do entendimento combinado e das inferências proibidas. A leitura e normalização
de arquivos, bem como as regras de aquisição e o limite aprovado de rodadas de
busca, permanecem fora desta alteração.

A validação local usa provedores simulados: demonstra preservação, divisão,
montagem, contabilização e retomada. Qualidade editorial, adesão do Gemini ao
protocolo de partes e consumo real devem ser avaliados no teste do usuário.

## Contribuição externa como promessa editorial — 03/10/2026

A oportunidade escolhida pelo usuário passa a ser adendo obrigatório e prioritário
na busca, julgamento de candidatas e compreensão da fonte adquirida. O briefing
original e o PE congelado contextualizam a proposta, sem excluir evidências novas
por não estarem previstos originalmente. Outros achados pertinentes aos assuntos
existentes são bem-vindos, mas não substituem o tipo de contribuição prometido.

A aquisição técnica não encerra a busca de enriquecimento. Depois da leitura dos
originais, a avaliação verifica se a fonte efetivamente cumpre a proposta e agrega
argumentos. Só então marca a cobertura. Se não cumprir, retira do conjunto editorial ativo apenas
os dados anexados por essa tentativa, preserva o PE e a proposta e avalia outra
candidata ou a rodada seguinte. Mantém as quatro rodadas já previstas; ao esgotar
as opções, registra falha de cumprimento, sem reformular ou apagar a promessa.
Erros técnicos continuam sendo informados como erros, sem rejeitar arbitrariamente
uma fonte nem disparar novas buscas para mascará-los.

Toda incorporação de fonte externa aumenta a duração, com tempo adicional estimado
pelo conteúdo. Quando aprofunda um assunto existente, mantém seu título e papel,
acrescenta a contribuição e seus argumentos à profundidade visível no PE e aumenta
seu tempo. Quando requer assunto próprio, acrescenta assunto, argumentos e tempo.
As unidades anteriores do corpus permanecem intactas. A regra difere da contribuição
pontual do LLM, que não precisa aumentar a duração.

Os registros preservam a identidade de cada fonte tentada, sua aceitação/rejeição
e a justificativa. A interface distingue fontes adquiridas porém rejeitadas das
incorporadas. A contabilização registra as chamadas de busca e julgamento à medida
que ocorrem, inclusive se nenhuma fonte cumprir a promessa. Nenhum parser, formato
ou regra de desmontagem/ingestão foi alterado.


### Recuperação de saída e contribuição externa (2026-10-03)

O gateway preserva o texto de respostas JSON malformadas como trabalho em
progresso, nunca como artefato validado. O processador atômico repara esse objeto
ou regenera apenas um comando malformado, sem apagar fragmentos aplicados. Falhas
consecutivas reduzem a operação. Se o provedor não conseguir emitir nem operações
mínimas, a execução pausa com seu checkpoint, sem rejeitar a fonte. Chamadas
concluídas são reutilizadas; correções e reverificações editoriais deliberadamente
novas podem executar novamente, mesmo com o mesmo texto.

A contribuição possui checkpoint por PE congelado e oportunidade em
`atomic_model_tasks`: plano, consultas, candidatos, julgamentos, conteúdo adquirido
e bytes originais, estrutura e leituras concluídas. Uma falha técnica na avaliação
não repete aquisição, ingestão ou compreensão. Fontes inadequadas são excluídas
da leitura editorial ativa por identidade, mantendo originais e leituras no banco.
Os leitores e a normalização da ingestão permanecem inalterados.

Falha técnica aparece no recibo e na interface como contribuição pausada, com
retomada da mesma oportunidade. Esgotar as quatro rodadas autorizadas aparece como
promessa não cumprida, preservando PE e material. A retomada também reconhece o PE
já incorporado caso a publicação do recibo falhe, para não agregar duas vezes.

Verificação local: JSON inválido antes e durante a montagem, preservação do prefixo,
contabilidade das tentativas, cache de operação concluída, interrupção após a
compreensão e retomada somente da avaliação, isolamento editorial de fontes
guardadas e encaminhamento da retomada pela API. Nenhuma chamada paga nos testes.
