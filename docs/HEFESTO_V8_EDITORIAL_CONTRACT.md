# HEFESTO V8 — CONTRATO E PLANO DE IMPLEMENTAÇÃO

Decisões do usuário consolidadas em 12/10/2026.
Autoridade editorial única, referenciada pelo README e AGENTS.md.

**Estado: implementação em etapas, com merge após verificação; consultar o registro de sessões.**
Este documento define o comportamento exigido. Não afirma que o código atual já o executa.
Substitui as orientações anteriores deste arquivo que preservavam writing completion,
correções por patches, continuação de resposta cortada ou restrições contrariadas abaixo.
Instruções explícitas posteriores do usuário prevalecem.

## 1. Escopo, sequência e disciplina

- Corrigir e deixar funcionando o Hefesto V8 antes de alterar Hermes.
- Implementar somente as decisões deste documento. Não criar nova arquitetura, novos
  revisores, aprovações, limites editoriais, mecanismos de fragmentação ou melhorias oportunistas.
- A implantação pode acontecer em várias sessões, mas deve convergir para um único
  funcionamento integrado. Não declarar concluído um item só porque seu prompt mudou.
- Em cada retomada, ler este contrato, conferir o código e o estado dos commits.
  Não reconstruir decisões pela memória de uma conversa.
- Toda pendência de interpretação deve ser apresentada ao usuário antes de virar regra.
- Esta rodada autoriza o registro documental. Não confundir esse registro com início da
  implementação, deploy ou autorização para inferências pagas.

Atualização de autorização em 12/10/2026: o usuário autorizou iniciar a implementação,
depois determinou fazer merge ao concluir cada etapa e começar a seguinte. Não deixar
etapas concluídas em PR rascunho nem depender de testes intermediários dele.
Executar os testes técnicos sem inferência paga e os gates de CI antes dos merges.
O workflow existente da main inicia implantação automática após merge de código.

## 2. Limpeza radical e preservação do V8

Remover resíduos claros de arquitetura editorial V4, caminhos mortos e compatibilidades
sem função no V8. Não conservar código duvidoso apenas por receio de removê-lo.
O que veio do V4 e efetivamente pertence ao V8 deve ser preservado como V8.

Preservar ingestão, normalização, segmentação, aquisição, originais e endereçamento das
fontes, uploads, autenticação e armazenamento necessários ao produto. Não refazer
ingestão nem substituir ferramentas no âmbito desta correção editorial.
Identificadores remotos contendo v4 são endereços operacionais; não renomear infraestrutura
ou repositório por estética.

**Verificação:** caminhos editoriais ativos pertencem ao V8; remoções são justificadas por
dependências e uso real; funcionalidades de infraestrutura usadas pelo V8 continuam válidas.

## 3. Proteções e configurações mantidas

- Preservar propriedade dos projetos, controle de acesso e proteção contra execução
  simultânea indevida por workers.
- Preservar vínculo técnico entre aprovação, versão do PE e versão do rascunho.
  Rejeitar aprovação de versão obsoleta é proteção de estado, não julgamento editorial.
- Manter temperatura 1 e a configuração de capacidade cognitiva máxima usada no teste.
  Não alterar modelo, temperatura ou thinking budget por iniciativa desta implementação.
- Manter no máximo dois contextos, com fronteiras naturais quando houver divisão.
  Isso não autoriza dividir a autoria em duas obras nem escrever por assunto.
- Preservar integridade, identificação e proveniência das fontes.

**Verificação:** essas proteções continuam operantes sem introduzir novas restrições de autoria.

## 4. Encomenda, duração e capacidade de saída

- Limite comercial aprovado: encomenda de até **100 minutos (1h40)**.
- Conversão de referência mantida: **150 palavras por minuto**.
- Assim, 100 minutos correspondem a **15.000 palavras de referência**.
- O adicional permitido de 10% leva a **16.500 palavras de texto final**.
  O usuário confirmou expressamente que esse é o significado das 16.500 palavras.
- A obra deve cumprir a extensão encomendada, com liberdade de desenvolvimento até o
  adicional de 10%. Não tratar texto 10% menor como automaticamente aceitável:
  a regra combinada não é uma tolerância simétrica de ±10%.
- Exibir e validar o limite na encomenda, antes de começar a produção intelectual.
  Não aceitar silenciosamente a encomenda e só rejeitá-la depois do PE.
- Remover a proibição de escrita baseada em target_words × 4 comparado à saída máxima.
  Quatro tokens por palavra é uma estimativa, não prova de impossibilidade.
- Fontes grandes são entrada/contexto. Não aplicar a elas o limite de palavras da obra.
- O limite físico de saída do provedor continua real. A implementação deve conferir
  capacidade para texto, envelope e rascunho excedente sem fingir que a remoção da fórmula
  aumenta essa capacidade.
- Se a capacidade efetiva não comportar o contrato, apresentar o conflito antes de
  inventar uma solução: não truncar, reduzir a encomenda, trocar modelo ou fragmentar
  a autoria silenciosamente.

**Exemplos:** 20 min → referência 3.000; final até 3.300; rascunho 3.900.
100 min → referência 15.000; final até 16.500; rascunho 19.500.
Os 30% do rascunho incidem na referência, não no teto final já acrescido de 10%.

**Verificação:** encomenda máxima aceita na entrada não é bloqueada depois pela fórmula
de quatro tokens; extensão é conhecida desde o planejamento e conferida na completude.

## 5. Planejador: matéria intelectual antes do PE

O planejador deve produzir um **rascunho extremamente robusto intelectualmente**,
dimensionado para **130% da extensão de referência**, antes de sintetizar o PE.
O usuário corrigiu explicitamente a orientação anterior e manteve os 30% excedentes
para dar matéria suficiente ao escritor e eliminar a necessidade de writing completion.

O prompt deve exigir desenvolvimento da matéria: explicações, relações entre ideias,
causas, consequências, nuances, contrapontos e exemplos pertinentes.
O planejador não tem obrigação de acabamento narrativo ou estilístico.
Não preencher extensão com repetição, paráfrase redundante ou assunto decorativo.
Não produzir apenas um roteiro de tópicos no lugar do rascunho desenvolvido.

Preservar autoria intelectual: briefing orienta a encomenda; fontes lastreiam fatos;
conhecimento confiável e elaboração intelectual são permitidos.
Não inventar estudos, dados, depoimentos, acontecimentos ou bibliografia.
Uma fonte não ganha peso editorial proporcional ao seu tamanho.

O rascunho é uma unidade intelectual integral. Não criar uma chamada por assunto,
nem substituir o trabalho por relatório de suficiência ou conversa do modelo consigo.
Registrar alvo e extensão observada sem transformar diferença de contagem em um novo
revisor ou loop de complementação não aprovado.

**Verificação:** rascunho integral vem antes do PE, usa o alvo de 130%, é persistido e
entregue integralmente ao escritor. Um alvo no prompt, sozinho, não prova atendimento
do dimensionamento; divergências devem ser explicitadas, não consideradas sucesso por conveniência.

## 6. PE, remoque e aprovação

- PE é uma síntese curta do rascunho efetivamente produzido.
- Preservar os três campos atuais: ideia central, desenvolvimento narrativo e
  conclusão pretendida. Não acrescentar matriz de fontes ou quarto campo.
- Persistir o rascunho antes de produzir a síntese.
- Não criar fiscal de alinhamento/fidelidade entre PE e rascunho.
- PE e rascunho ficam vinculados por versão; a aprovação única aprova esse conjunto.
- Remoque recebe a concepção anterior e a instrução do usuário, atualiza o rascunho
  como um todo e depois o PE correspondente, preservando o que permanece válido.
- Preservar agendamento da produção após aprovação e sua idempotência.
- Uma proteção contra clique/aprovação obsoleta não pode virar restrição editorial.

**Verificação:** aprovação usa a versão correta; remoque não deixa PE e rascunho
descasados; não existe julgamento editorial adicional de fidelidade.

## 7. Escritor: autoria da obra inteira

A prioridade das fontes de trabalho do escritor é, explicitamente:

1. Rascunho.
2. Briefing.
3. Fontes.

O PE aprovado informa o compromisso editorial. Essa prioridade não autoriza inventar
fatos nem ignorar a encomenda.

O escritor deve produzir um excelente texto completo, concebido como narrativa:
relações entre assuntos, progressão, transições e destino intelectual.
Pode selecionar, reorganizar, condensar e reescrever a matéria excedente do rascunho.
Não é obrigado a manter sua ordem, redação, enumerações ou todos os exemplos.

Pode introduzir material intelectualmente bom e pertinente, sem alucinação, respeitando
a encomenda e sua extensão. Ausência literal nas fontes não torna uma elaboração inválida.

A produção deve ocorrer como obra inteira em uma chamada de escrita, dentro da capacidade
do produto. Não gerar parágrafos ou assuntos separadamente para o código montar.
Streaming transporta a resposta de uma chamada; não altera essa unidade de autoria.

Eliminar a etapa/loop separado de **writing completion**.
O escritor já recebe a obrigação de extensão. Deficiência de tamanho pertence à
completude e usa o mecanismo editorial de correção integral, não um apêndice de preenchimento.

**Verificação:** prompt explicita a prioridade; uma chamada escreve o texto integral;
não existe caminho ativo de writing completion ou montagem por blocos editoriais.

## 8. Quatro revisões e seus critérios

As quatro etapas são revisões por modelo, nesta ordem:
completude → consistência → narrativa → estilo.

### 8.1 Completude

Modelo de comparação: **PE aprovado**. Direção: **PE → texto**.
Pergunta: o que prometemos no PE foi efetivamente entregue no texto?
Verificar também a especificação de extensão conhecida desde a encomenda.
Não exigir que tudo no texto tenha sido previamente enumerado no PE.
Não exigir todos os detalhes do rascunho excedente.
Material adicional pertinente, dentro da encomenda, não é defeito por ser adicional.
Tamanho não pode ficar escondido em validação final do JSON.

### 8.2 Consistência

Referências: fontes e conhecimento confiável/bom senso do LLM, incorporado à elaboração
intelectual. O critério central é **contradição**, e não equivalência literal.
Apontar erros factuais, contradições efetivas, invenções e inferências tratadas como fatos.
Não exigir uma passagem documental para cada ideia, analogia, interpretação ou conexão.
Não tratar divergências entre fontes como se todas dissessem a mesma coisa.
Distinguir erro factual de discussão explícita e fundamentada de uma posição.

### 8.3 Narrativa

Aplicar boas práticas do produto solicitado: progressão das ideias, relações,
transições, ritmo, clareza e unidade. Combater a lista de assuntos apenas disfarçada
de prosa. Trata-se de polimento de uma obra que o escritor já deveria conceber bem.

### 8.4 Estilo

Aplicar boas práticas de redação adequadas a público, formato, idioma e tom:
clareza, fluidez, precisão, oralidade quando aplicável e retirada de redundância.
Preservar a matéria intelectual e a narrativa válida.

Narrativa e estilo não são uma segunda autoria independente nem licença para trocar
a tese por preferência do revisor. Não inventar defeitos para justificar uma chamada.
Preservar decisões anteriores válidas; erro real não se torna intocável por ter passado
por uma etapa anterior.

**Verificação:** cada prompt define sua referência e competência; completude mede
promessa/entrega; consistência admite autoria responsável; polimento preserva a obra.

## 9. Diagnóstico, correção integral e verificação

Fluxo obrigatório em cada etapa com defeitos:

1. Uma chamada de diagnóstico lê a obra e entrega uma lista pontual dos defeitos.
2. Uma chamada de correção recebe **o texto inteiro e a lista de defeitos** e devolve
   **o texto inteiro corrigido**, preservando unidade e o que está válido.
3. Uma chamada de verificação lê a correção e verifica a resolução dos defeitos
   apontados e a preservação do que não deveria ser prejudicado.

O revisor aponta a falha; não entrega um catálogo de substituições para o programa aplicar.
Pode citar um trecho para explicar o problema. Isso não é um endereço obrigatório de patch.
Eliminar correções dependentes de p8, p6 ou outros IDs de parágrafos.
Não fazer uma chamada por defeito ou parágrafo. Não montar a obra a partir de remendos.
A verificação não reabre automaticamente toda a pauta como uma nova auditoria irrestrita.

Remover a regra de desistir/reconsiderar automaticamente após duas correções malsucedidas.
Completude e consistência podem precisar de mais de duas chamadas de correção.
Nova correção recebe a versão integral vigente e as pendências concretas.
Narrativa e estilo são polimento; não transformar preferência repetida em ciclo sem fim.

Não inventar outro número fixo de tentativas para substituir o dois.
Se houver falha técnica, aplicar recuperação técnica. Critério novo de encerramento
editorial por falta de progresso exige discussão; não está autorizado neste registro.

**Verificação:** simular mais de duas correções necessárias; verificar texto integral
em todas as chamadas e ausência de aplicação de patches por endereços.

## 10. JSON é transporte

- Validação estrutural não julga extensão, qualidade editorial, fidelidade ao PE
  ou legitimidade das ideias.
- Corrigir automaticamente erro simples de embalagem quando a correção for inequívoca
  e não exigir inventar conteúdo.
- Resposta cortada/incompleta não é reparada juntando sufixos nem completando pontuação:
  refazer integralmente a operação que falhou, preservando as etapas anteriores válidas.
- Não reaproveitar conteúdo defeituoso como resultado concluído.
- Não continuar bytes de uma resposta truncada como mecanismo de autoria.
- Se a causa for limite físico de saída, tratar a causa; repetir a mesma chamada
  impossível indefinidamente não é recuperação.
- Metadados auxiliares de localização não podem destruir um texto válido nem restabelecer
  a correção editorial por endereços. Preservar conteúdo válido e distinguir o problema
  de transporte/metadados de uma falha da obra.

**Verificação:** embalagem reparável, resposta truncada e metadados inválidos têm
tratamentos distintos; nenhum deles cria julgamento editorial no parser.

## 11. Persistência e retomada efetiva

Persistir etapas terminadas e seus resultados completos.
Na falha, manter essas etapas e refazer **do início ao fim a etapa falhada**, usando
o último estado concluído e válido. Depois, seguir o fluxo até a entrega.

Resposta bruta e erro podem permanecer para diagnóstico, mas não são checkpoint de sucesso.
Journal não pode devolver eternamente a mesma tentativa inválida e bloquear toda retomada.
Não repetir planejamento, ingestão, escrita ou revisão já concluídos sem necessidade
decorrente de mudança efetiva da encomenda/versão.

Não basta dizer que os dados estão guardados. A recuperação precisa funcionar pelo
caminho normal do produto, sem intervenção manual no banco.

**Verificação:** interromper uma etapa simulada após anteriores concluídas; retomar;
confirmar nova tentativa integral, preservação dos resultados anteriores e avanço.
Cobrir falha antes/depois da persistência e repetição de acionamento sem produção duplicada.

## 12. Timeout por inatividade observável

Retirar os 240 segundos como regra geral de desistência de uma chamada longa.
Usar a atividade recebida por streaming para distinguir resposta em andamento de silêncio.
Recebimento de novos eventos/trechos renova a observação de atividade.

Distinguir espera inicial de interrupção depois que a resposta começou.
Silêncio de rede não prova inatividade interna do pensamento do modelo.
Não fabricar progresso por heartbeat local e tratá-lo como atividade do LLM.
Streaming continua sendo transporte de uma única geração integral.

Falha técnica deve permitir a retomada descrita acima.
Prazos impostos pelo provedor/infraestrutura precisam ser identificados separadamente;
mudar o temporizador local não garante removê-los.

**Definição técnica aprovada em 12/10/2026:** a geração usa streaming. Qualquer
evento/trecho recebido do provedor (incluindo partes de pensamento, quando
existirem) renova a observação de atividade. Esperar no máximo **60 segundos**
pelo primeiro evento. Depois do primeiro evento, **30 segundos** sem novo evento
contam como inatividade. Não fabricar progresso local. Não manter os 240 segundos
como teto total da chamada no cliente; prazos reais do provedor/infraestrutura
continuam distintos e devem ser tratados como tal.

**Verificação:** chamada com atividade além de 240 segundos não é cancelada por
duração total local; silêncio de 30 segundos após atividade, ausência do primeiro
evento em 60 segundos e erro do provedor são distinguíveis.

## 13. Cache obrigatório NO LLM

O usuário explicitou: cache significa **carregamento/reutilização de contexto no
provedor do LLM**, não armazenamento no banco.

Cobrir materiais reutilizados: **fontes, corpus principal, briefing, rascunho e PE**,
conforme os insumos de cada etapa e as versões vigentes.
Não se limitar ao corpus documental deixando sempre os outros materiais como entrada
integral repetida fora do cache.

O código deve garantir uso do cache, renovar validade quando possível e recuperar
ou recriar o carregamento quando necessário.
Recriar cache não significa regerar briefing, PE ou rascunho.
Após remoque ou outra alteração, usar o contexto correspondente à nova versão.
Não reutilizar cache de uma versão anterior por conveniência.
Não criar fallback silencioso para execução sem cache.

Respeitar o máximo de dois contextos e requisitos reais do provedor.
Cache obrigatório de cada material não exige um cache independente para cada pequeno
documento; a organização deve permitir sua reutilização sem multiplicar contextos.
Indisponibilidade externa pode impedir uma chamada naquele momento, mas não pode deixar
o projeto permanentemente preso a um identificador de cache vencido.

**Verificação:** chamadas fazem referência efetiva ao cache; insumos reutilizados estão
nele; expiração/recriação, versão alterada, falha transitória e reinício são recuperáveis.
Dados salvos no banco, isoladamente, não satisfazem nenhum desses critérios de cache.

## 14. Hermes e o fluxo sem fontes: etapa posterior

Depois de concluir as correções do V8, retomar a falha auditada do fluxo
“não tenho fontes”. Fluxo pretendido: briefing → Hermes → corpus → V8 normal.
Não implementar suas correções junto destas mudanças editoriais.

Ponto de retomada da auditoria: decisão pendente inválida podia ser persistida antes
da validação de sucesso e reaproveitada em toda retomada. Revalidar o código atual e
corrigir a causa posteriormente, sem tratar a tentativa inválida como resultado concluído.
Verificar também alternativas de busca/aquisição diante de falha de um provedor.

## 15. Implantação em sessões: checklist e evidências

Os itens abaixo só podem ser marcados concluídos com as evidências do registro de sessões.
Ordem de trabalho proposta por dependência técnica; não muda a arquitetura aprovada.

- [ ] A. Conferir main, deploy e arquivos ativos; mapear resíduos V4 e testes que preservam
  comportamentos agora rejeitados. Não confundir nome de recurso com legado editorial.
- [ ] B. Corrigir estado, checkpoints e retomada; separar falha de transporte de sucesso;
  retirar reaproveitamento de tentativas inválidas e continuação de resposta cortada.
- [ ] C. Implementar cache no LLM e tratamento de inatividade, preservando configuração.
  Resolver explicitamente as definições técnicas pendentes do item 12.
- [ ] D. Ajustar encomenda, capacidade, planejador, PE, remoque e escritor:
  100 minutos, referência/10%, rascunho 130%, prioridades e autoria integral;
  retirar writing completion e bloqueio por quatro tokens.
- [ ] E. Ajustar os quatro revisores e o ciclo diagnóstico/correção/verificação;
  remover patches endereçados e limite de duas correções.
- [ ] F. Concluir limpeza e validar o fluxo integrado com simulações sem inferência paga;
  registrar o que depende de avaliação editorial real do usuário.
- [ ] G. Após V8 concluído, tratar Hermes/“não tenho fontes” em trabalho próprio.

Em cada sessão registrar aqui: itens alterados, commit, verificações realizadas,
resultado, pendências concretas e próximo passo. Não marcar concluído por intenção.
Não executar testes pagos ou deploy apenas para documentar decisões.

### Mapa inicial de código para retomada

Confirmar nomes e chamadas na revisão efetivamente usada, sem assumir que permaneceram iguais:

- src/comunicamundo_v8/hefesto/v8/editorial.py: instruções de planejamento/escrita/revisão.
- src/comunicamundo_v8/hefesto/v8/engine.py: fluxo, extensão e bloqueio de capacidade.
- src/comunicamundo_v8/hefesto/v8/hierarchy.py: diagnóstico, correção, verificação e patches.
- src/comunicamundo_v8/hefesto/v8/provider.py: chamadas, JSON, journal, timeout e SDK.
- src/comunicamundo_v8/hefesto/v8/context.py: contexto e cache.
- src/comunicamundo_v8/hefesto/v8/contracts.py: estruturas e validações.
- src/comunicamundo_v8/hefesto/v8/repository.py e runtime.py: persistência e execução.
- src/comunicamundo_v8/hefesto/v8/routes.py e web/: entrada, limite e retomada visível.
- tests/test_v8*.py: alinhar testes ao contrato, sem perpetuar comportamento rejeitado.
- src/comunicamundo_v8/hefesto/v8/hermes.py: somente na etapa posterior.

### Registro de sessões

12/10/2026 — decisões consolidadas neste contrato. Somente documentação.
Nenhuma correção funcional desta lista implementada nesta rodada.

12/10/2026 — primeira implementação, branch fix/v8-recovery-contract, baseada na
main 917cdb6646b29b59789ce18ae9eded8d23cd3e73. Escopo: transporte e recuperação
do bloco B. Alteração funcional ainda não implantada em produção.

Implementado e verificado localmente:

- Reparação determinística de vírgula estrutural excedente, preservando pontuação
  dentro de strings e resposta bruta. Nenhuma chamada ao LLM para esse reparo simples.
- Resposta inválida/incompleta recebe nova chamada da operação original integral,
  com os mesmos insumos e cache disponível. Retirados prompts de reparo por modelo,
  geração de sufixos e concatenação de fragmentos.
- Journal de falha não aprisiona a retomada: tentativas rejeitadas ficam como
  diagnóstico; a retomada executa uma tentativa nova. Resposta completa já salva
  antes de uma interrupção continua recuperável sem repetir a chamada.
- Chamada interrompida sem resposta persistida pode ser refeita pelo caminho normal
  de retomada; custo desconhecido permanece registrado, sem confirmação adicional.
- Recibos de respostas e erros são reconciliados após interrupção sem duplicar
  recibos antigos de custo desconhecido.
- Revisão do estado em memória só avança após gravação confirmada no PostgreSQL;
  falha na gravação não contamina a próxima tentativa com revisão inexistente.
- Teste integrado usa a ação resume do Backend e confirma preservação do rascunho,
  PE, escrita e revisão aprovada antes da falha; o fluxo chega a text_ready.

Verificação: suíte local sem inferência paga, 165 testes aprovados e 2 ignorados
por ausência de PostgreSQL isolado. Testes reais de banco e qualidade editorial
não foram executados. Não houve deploy nem mudança em Hermes.

Limites deste bloco, que NÃO devem ser apresentados como conclusão de todo o V8:

- MAX_TOKENS preserva diagnóstico e interrompe a tentativa sem montar fragmentos.
  A retomada refaz a operação integral. O dimensionamento/capacidade para evitar
  essa falha continua no bloco D; não foi resolvido por este commit.
- Repetição idêntica de resposta inválida na mesma execução interrompe o transporte
  sem loop infinito; a retomada permite nova tentativa. Isso não é o limite de
  duas correções editoriais, que ainda precisa ser removido no bloco E.
- Validações editoriais de endereços/extensão ainda existentes em Calls.validate
  serão retiradas com os contratos e a hierarquia no bloco E. A separação completa
  entre transporte e decisões editoriais ainda NÃO está concluída.
- Writing completion, patches por parágrafos, limite de duas correções, prioridades
  dos prompts, restrição de 100 minutos, cache dos materiais editoriais e timeout
  por inatividade ainda estão pendentes. Não confundir reutilização do cache atual
  durante regeneração com atendimento ao novo requisito de cache completo.

Próxima retomada: conferir a branch fix/v8-recovery-contract e sua revisão remota;
prosseguir pelo bloco C (cache dos materiais por versão) sem alterar modelo ou
temperatura. Prazo de inatividade e espera inicial continuam sem número aprovado.
Os blocos D/E devem seguir este contrato integralmente. Hermes permanece posterior.

12/10/2026 — PR #145 incorporado à main em
122106449dcdcf92abafe9467e9d99b4fc16ed70. CI 38312817672 aprovado integralmente:
testes com PostgreSQL isolado, migrações, navegador offline e imagem de produção.
Assim, os dois testes de banco anteriormente ignorados localmente também foram
executados no ambiente de CI. Deploy automático 38313445514 disparado pelo merge.

Foi revisado e incorporado também o PR #144, já existente, com CI 38085620058
aprovado: comando explícito comunicamundo-v8-worker no Cloud Run Job e diagnóstico
de falha de migração. Merge d42dcff20c34db3e1eb88682e6a5e3bb375f67cf.
Esse ajuste elimina a possibilidade de conservar o comando V4 no recurso remoto;
não altera arquitetura editorial ou Hermes. Conferir o resultado do deploy.

12/10/2026 — segunda etapa, branch fix/v8-editorial-cache: cache NO LLM.

- Chamadas editoriais declaram o prefixo reutilizável com os insumos de sua etapa.
  Fontes/corpus, briefing, rascunho e PE passam a participar efetivamente do cache,
  sem cópia desses prefixos na parte dinâmica da mesma chamada.
- Revisões compartilham material estável conforme sua competência; esta mudança
  não altera seus critérios, que continuam pendentes no bloco E.
- Identidade do cache inclui conteúdo exato e versão dos materiais. Remoque e PE
  novos não reutilizam um conjunto anterior por engano.
- Cache perdido ou expirado é reconstruído sem nova geração intelectual. Perda
  entre consulta e renovação é recuperada na mesma preparação.
- Recursos substituídos são retirados antes de criar seus sucessores. Após duas
  leituras, o contexto editorial combinado recebe cache e os anteriores são
  retirados, sem terceiro contexto ativo nem mudança da leitura editorial.
- Prefixos pequenos são agrupados com o restante dos insumos daquela chamada.
  Se o conjunto inteiro não atingir o mínimo real do provedor (2.048 tokens no
  perfil vigente), informar a limitação. Não preencher com material inventado,
  acrescentar fontes irrelevantes ou executar sem cache silenciosamente.
- Verificação local após incorporar #144: 170 testes aprovados; os 2 de PostgreSQL real dependem do CI.
  Inclui versão alterada, perda, expiração, renovação, reconstrução sem regeneração,
  agrupamento e máximo de dois recursos ativos. Sem inferência paga.

Próximos trabalhos: obter aprovação de CI e fazer merge desta etapa; prosseguir
com D/E (encomenda, dimensão, prompts e correção integral). A definição numérica
do timeout por inatividade foi aprovada (60s primeiro evento / 30s silêncio) e
ainda precisa ser implementada em código com streaming; não confundir registro
com implementação.

12/10/2026 — definição aprovada pelo usuário para o item 12: streaming observável;
60 segundos até o primeiro evento (“psiu”); 30 segundos de silêncio após atividade;
qualquer evento do provedor renova os 30s; sem heartbeat local; retirar o teto
total de 240 segundos no cliente. Somente registro documental nesta entrada;
implementação do streaming/inatividade permanece pendente.
