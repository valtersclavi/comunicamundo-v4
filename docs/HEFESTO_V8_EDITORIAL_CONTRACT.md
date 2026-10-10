# HEFESTO V8 — CONTRATO CANÔNICO DE IMPLEMENTAÇÃO

Autoridade editorial única do Hefesto V8. Descreve o comportamento atual, preservado na aposentadoria do V4. Ajustes editoriais exigem instrução explícita do usuário.

## 1. Matéria intelectual antes do PE

O briefing encomenda uma obra intelectual. Fontes aprofundam, qualificam, contradizem e fundamentam; não determinam a ordem narrativa nem uma distribuição proporcional do texto. Conhecimento geral confiável, interpretação, raciocínio e exemplos ilustrativos são admitidos, distinguidos de informação documental. Nunca inventar estudos, dados, acontecimentos, depoimentos ou bibliografia.

A operação principal de planejamento recebe briefing, fontes disponíveis e extensão contratada. O contrato existente converte duração em palavras a 150 palavras/minuto. O alvo do rascunho é inicialmente 130% da extensão contratada, arredondado para cima: 20 minutos → 3.000 palavras finais → aproximadamente 3.900 palavras de rascunho.

O planejador produz diretamente o rascunho intelectual desenvolvido, usando o núcleo de instruções fornecido pelo usuário em `v8/editorial.py:DRAFT`: começar pela matéria central, aprofundar explicações por causas/mecanismos/consequências, relacionar ideias, explorar implicações, incorporar exemplos pertinentes, nuances e contrapontos. O desenvolvimento progressivo acontece dentro dessa tarefa. Não pedir acabamento estilístico nem relatórios sobre o processo. Proibidos repetição, paráfrases redundantes, exemplos decorativos, digressões e assuntos artificiais para aumentar volume.

O princípio `AUTHORSHIP`, compartilhado pelo planejador e escritor, estabelece o briefing como direção da obra e as fontes como sustentação do conhecimento e limites factuais. O autor concebe a linha narrativa e pode formular relações, analogias e interpretações originais, distinguindo-as de fatos estabelecidos. Não reproduzir obrigatoriamente a sequência, classificações ou importância relativa dos documentos. Não inventar fatos ou evidências.

Uma chamada principal `intellectual-draft`, não chamadas por assunto nem ciclos de escrever/avaliar/aprofundar. A resposta contém apenas `body`, com o próprio rascunho integral. O envelope JSON reutiliza transporte e recuperação existentes; não acrescenta análise de suficiência.

## 2. Persistência e Projeto Editorial

O rascunho ocupa `InternalPlanning.content`, no estado existente, identificado como `content_kind=intellectual_draft`. Preservar identificação, versão, briefing, manifesto, vínculo anterior, remoque e aprovação. `draft_target_words` e `measured_words` registram alvo e contagem determinística. A contagem não aprova/reprova o rascunho nem dispara chamadas adicionais.

Persistir integralmente antes da síntese `editorial-project`. O PE descreve o conteúdo efetivamente desenvolvido, exclusivamente em três campos:

- `central_idea`: uma ou duas frases.
- `narrative_development`: progressão concisa e relações intelectuais importantes.
- `intended_conclusion`: destino intelectual/comunicativo.

O PE permanece vinculado à versão exata do rascunho e é o checkpoint apresentado ao usuário. O rascunho interno não vira um quarto campo nem aparece na UI.

## 3. Remoque e aprovação

Remoque recebe texto anterior completo, PE, briefing, fontes/contexto e instrução persistida. A mesma operação principal atualiza a matéria intelectual afetada e suas consequências, preservando o que continua válido, e entrega o rascunho inteiro. Persistir nova versão antes do novo PE. Se o texto retornar exatamente igual ao rascunho vigente, preservar versão/PE e informar que não houve alteração; o resultado bruto continua no journal.

A aprovação única do PE aprova também o rascunho vinculado e mantém o agendamento automático e idempotente da produção. Nenhuma nova aprovação editorial ou agente de fiscalização.

Planejamentos históricos permanecem legíveis com `content_kind=abstract_plan`, sem fingir que são rascunhos. Um remoque os transforma no novo contrato. Checkpoints e journals anteriores ficam preservados. Ao retomar planejamento antigo interrompido, reutilizar seu material disponível na tarefa de rascunho, sem reabrir avaliações antigas.

## 4. Escritor e qualidade

O escritor recebe rascunho integral, encomenda original, PE aprovado, fontes/contexto e instruções editoriais de público, formato, idioma e tom. O rascunho excedente é matéria-prima: liberdade explícita para selecionar, reorganizar, condensar e reescrever, sem preservar literalmente redação, ordem dos parágrafos ou todos os detalhes ilustrativos. A meta final continua sendo a encomenda original, não os 130%.

Dentro da concepção aprovada, o escritor também pode ampliar relações e melhorar explicações. Essa liberdade permanece nas instruções da complementação de extensão, sem alterar seu mecanismo, metas ou tolerância. Classificações e enumerações do rascunho não são obrigatórias.

Preservar a produção e recuperação existentes, incluindo o tratamento da extensão da obra final dentro da escrita. Esse tratamento não chama novamente o planejador. A contagem do rascunho é somente telemetria.

A esteira hierárquica posterior permanece: completude → coerência/consistência → narrativa → estilo. A completude respeita a seleção e condensação autorizadas do material excedente; não exige incorporar todos os detalhes do rascunho. Permanecem diagnósticos, correções, confirmações, reconsideração autônoma e preservação das aprovações anteriores.

O contrato `CONSISTENCY` vale no diagnóstico, correção, confirmação e reconsideração: protege coerência e responsabilidade factual, sem exigir passagem documental equivalente para cada argumento ou conexão, nem fidelidade à organização das fontes. Originalidade não constitui defeito. Contradições efetivas, erros factuais, inferências apresentadas como fatos e conclusões injustificadas continuam verificáveis; a consulta documental existente atende somente dúvida factual específica e relevante. A narrativa avalia qualidade efetiva, não conformidade documental; estilo preserva pensamento e ordem narrativa consolidada. Todas as etapas aprovam diretamente quando não há defeito concreto dentro de sua competência.

## 5. Capacidade, recuperação e observabilidade

`Calls.structured` solicita a capacidade máxima de saída do perfil verificado do modelo, com preflight do contexto real. Não criar teto de conteúdo a partir de uma estimativa arbitrária de tokens por palavra. Se ocorrer interrupção técnica de saída, usar a continuação já existente com bytes preservados. JSON malformado aciona reparo técnico do transporte, não replanejamento editorial.

Persistir resposta bruta antes de parsing/validação; recuperar resultados completos do journal sem repetir chamadas. Timeout mantém checkpoints e progresso. A limpeza do V4 não altera modelo, limites cognitivos, timeout, preço, caching ou comportamento editorial.

Cada chamada mantém etapa, tokens, latência, custo e motivo de término. A medição do rascunho é um evento de artefato e não aumenta o contador de chamadas físicas.

## 6. Verificação

Testar sem inferência real: uma chamada principal seguida de síntese do PE; meta 130%; ausência de avaliação de suficiência e de regeneração por contagem; persistência antes do PE; resposta bruta recuperável; retomada após falhas; remoque do texto; escritor recebendo rascunho completo e liberdade editorial; vínculos/versionamento; fontes, caching e quatro estágios de qualidade. Qualidade literária e consumo real dependem de teste pago posterior autorizado, não de fixtures.
