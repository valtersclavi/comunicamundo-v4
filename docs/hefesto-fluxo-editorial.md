# Hefesto V4 — fluxo editorial vigente

**Decisão de 26/09/2026:** a arquitetura experimental da matriz editorial foi retirada. O planejamento e a produção de texto usam novamente a esteira editorial anterior: seleção de trechos, compreensão por assunto quando necessária, Projeto Editorial e redação com revisão. A ingestão e a desmontagem de arquivos continuam congeladas.

O experimento da matriz não concluiu o Projeto Editorial do Teste B após cerca de duas horas e 1.296 chamadas ao modelo (aproximadamente 2,51 milhões de tokens). Melhorias futuras devem aperfeiçoar a esteira existente e medir qualidade, tempo e consumo antes de outra mudança arquitetural. Quando o material ultrapassar a janela do modelo, dividir o trabalho sem truncar ideias, tensões ou contradições; não reduzir outros assuntos para acomodar uma fonte nova.

## Percursos e intervenções

| Situação | Comportamento |
| --- | --- |
| Fontes fornecidas | Ingestão existente, seleção, compreensão e primeiro Projeto Editorial. Este primeiro projeto oferece contribuições pequenas do LLM e oportunidades de novas fontes. |
| Zero fontes | Pré-projeto gerado pelo LLM, aprovação do usuário, busca e aquisição de fontes, ingestão e Projeto Editorial definitivo. As duas listas de sugestões ficam vazias nessa segunda apresentação. |
| Remoque | O campo permanece sob o pré-projeto e sob todas as versões do Projeto Editorial. O pedido do usuário provoca revisão e nova apresentação para aprovação. |
| Contribuição do LLM | Ideia pequena inserida no assunto pertinente, sem alegar que veio de fonte documental. A versão revista não gera outra lista automática. |
| Enriquecimento por fonte | A proposta escolhida vira adendo prioritário ao briefing. Busca, aquisição e compreensão precisam cumprir essa promessa. Incorporação amplia argumentos/assuntos e duração, preservando o PE anterior. Fontes que não cumprem a proposta são rejeitadas e a busca continua nas quatro rodadas previstas. |

O pré-projeto aprovado orienta a busca e o projeto definitivo. Assuntos sem suporte nas fontes adquiridas não devem ser apresentados como sustentados documentalmente. A ingestão original, seus formatos, extração, normalização e estrutura persistida não fazem parte das revisões editoriais.


## Contrato vigente das referências — 30/09/2026

O briefing ou pré-PE aprovado define as perguntas antes da compreensão das fontes.
A triagem devolve flags e referências; a compreensão lê diretamente os trechos
originais pertinentes, em lotes, e mantém suas referências no entendimento
editorial. Não há etapa de condensação ou resumo intermediário substituindo o
material original. Na redação e revisão de conteúdo, os originais referenciados
são recuperados do PostgreSQL para sustentar o desenvolvimento autorizado.

Enriquecimento congela o PE anterior e acrescenta apenas a contribuição pertinente
da nova fonte, inclusive perspectivas novas autorizadas pela oportunidade.
Remoque pode modificar o PE conforme a vontade do usuário e revisitar fontes
quando necessário. Uma incorporação que falhe preserva o PE e mantém visível o
registro da aquisição. Nenhum erro autoriza mudar esses contratos.


## Objetos longos — regra global

Todas as etapas editoriais, incluindo redação e revisões, processam objetos
longos em partes. As operações mantêm os originais e o artefato em construção
endereçáveis; o tamanho de uma chamada não limita o tamanho total da entrega.
`MAX_TOKENS` provoca subdivisão/continuação, com persistência do progresso,
sem truncamento nem resumo substitutivo. O resultado só é apresentado como
completo após montagem e validação. Isso não altera o contrato do PE congelado,
a autorização de remoque nem a liberdade narrativa final.

### Evidências sem recitação — 03/10/2026

A compreensão devolve a contribuição analítica e os índices dos trechos lidos.
O programa resolve e valida suas referências aos parágrafos originais. Não pede
cópia literal nem interrompe a compreensão por diferença textual em uma cópia.
Redação, revisão e correção continuam recebendo os parágrafos originais recuperados
pelas referências, com o processamento por tamanho já existente. A ingestão e a
análise por parágrafo permanecem preservadas. PEs antigos são carregados mantendo
análise e referências, descartando apenas o campo legado de recitação.


### Recuperação sem descarte — 03/10/2026

Resposta ou comando com JSON malformado é reparado na operação que falhou,
conservando a saída válida já construída. Aquisição, estruturação e compreensão
da contribuição externa ficam persistidas para retomar a avaliação pendente.
Uma falha real do provedor aparece como contribuição pausada, com botão de
retomada; esgotamento da busca aparece como promessa não cumprida. Fontes que
não cumprem a promessa ficam fora do PE ativo, com originais e leituras guardados.


### Busca responsável pela contribuição prometida — 03/10/2026

Esta decisão substitui o limite de quatro rodadas para enriquecimento autorizado. O fluxo de zero fontes permanece separado. Antes de publicar uma sugestão externa, Hefesto localiza uma fonte real e verifica seu título/resumo; a pista fica persistida e é reutilizada quando a contribuição é escolhida. Uma proposta sem pista é substituída antes de ser oferecida.

A aquisição é seguida por leitura rápida, em janelas sucessivas, que termina assim que identifica indício concreto da perspectiva prometida. Isso acontece antes da ingestão e da digestão editorial. Uma triagem positiva não equivale à confirmação final. Quando apenas bytes binários estão disponíveis, o título/resumo já triado orienta o uso do leitor existente; não se rejeita material ainda não lido.

Fontes inadequadas realimentam o planejador com o motivo da rejeição. A próxima consulta pode mudar idioma, conceitos, gênero documental e provedor, sem exigir PDF ou DOI. Não há encerramento por quatro tentativas. Busca, triagem, aquisição e trabalho editorial ficam em checkpoints. Falhas do provedor aparecem como falhas técnicas, nunca como ausência de fontes. Uma incapacidade técnica de gerar consulta nova pausa para retomada; não retira a promessa.

Preservados: leitores e normalização da ingestão, PE congelado, regras de incorporação, referências originais e aumento de duração da contribuição aceita.
