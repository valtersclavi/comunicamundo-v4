# HEFESTO V8 — CONTRATO CANÔNICO DE IMPLEMENTAÇÃO

Autoridade funcional: instrução integral do usuário de 2026-10-08, contrato editorial substitutivo. Este contrato prevalece sobre regras editoriais incompatíveis do plano histórico, AGENTS.md e código anterior. Repositório valtersclavi/comunicamundo-v4; desenvolvimento em hefesto-v8. A implementação editorial anterior não foi aceita. Uma entrega integrada, testes técnicos sem modelos pagos, commit/promoção/deploy/smokes; avaliação intelectual pelo usuário. Não A/B, faturamento, casos especiais, funcionalidades oportunistas ou novas aprovações.

## I. Identidade e concepção intelectual
Hefesto é um sistema genérico de concepção, planejamento e produção de obras intelectuais, para quaisquer assuntos e formatos viáveis. O briefing encomenda; o LLM concebe com conhecimento geral confiável, raciocínio e fontes; as fontes ampliam, aprofundam, qualificam, confrontam e fundamentam. O software administra contexto/estado/decisões/integridade/execução/entrega. Fontes não são uma lista fechada de afirmações autorizadas. A obra é construção intelectual original, não reorganização documental.

A primeira operação editorial concebe a obra integralmente, de forma integrada:
1. Interpretar assunto/recorte, finalidade, público, formato/linguagem, perspectiva, extensão, requisitos e profundidade; determinar o que o público deve compreender, experimentar ou explicar. Exposição, narração, investigação, argumentação, interpretação, comparação e ensino podem se combinar conforme decisão intelectual do LLM.
2. Considerar conceitos, fatos, processos, acontecimentos, argumentos, interpretações, perguntas, hipóteses, relações, contradições, consequências e perspectivas provenientes do conhecimento do modelo, ideias explícitas/implícitas das fontes, inferências/raciocínio e tensões. Não preencher categorias obrigatórias. Selecionar pela finalidade; menção breve pode justificar desenvolvimento amplo e discussão longa pode ser pouco útil.
3. Desenvolver intelectualmente ideias, não apenas identificá-las. Escolher livremente explicar significado/contexto/mecanismos/causas/consequências; distinguir conceitos; estabelecer relações; confrontar interpretações/argumentos; qualificar afirmações; explorar implicações/contradições/conexões; construir analogias/exemplos/perguntas; desenvolver progressivamente interpretação. Não transformar essas possibilidades em matriz. Profundidade vem do desenvolvimento, não da quantidade de tópicos.
4. Conceber progressão intelectual/narrativa: início, pré-requisitos, introdução/aprofundamento, ordem de relações, transições, tensões e conclusão. Qualquer organização adequada à encomenda; não ordem das fontes nem seções fixas.
5. Dimensionar a obra inteira desde a concepção: profundidade, movimentos e desenvolvimento aproximado compatíveis com extensão/duração. Não preenchimento posterior.
6. Materializar uma concepção explícita, persistível, executável pelo escritor. Documento de trabalho editorial produzido expressamente; nunca transcrição de thinking privado.

## II. Conhecimento, fontes e responsabilidade factual
Utilizar conjuntamente conhecimento geral confiável, informação documental, raciocínio/interpretação e evidências específicas quando necessárias. Não exigir frase equivalente no corpus para cada afirmação; ausência nas fontes não indica erro. Conhecimento estabelecido pode ser usado diretamente, sem pesquisa compulsória por fato comum. Ideias explícitas podem ser desenvolvidas; ideias implícitas e construções próprias (interpretações, argumentos, analogias, relações, explicações) são legítimas. Expressar certeza proporcional à fundamentação.

Evidência particular é necessária conforme natureza/relevância/finalidade/público para dados quantitativos específicos, acontecimentos/declarações atribuídos, descobertas recentes, causalidades incertas, controvérsias, alegações extraordinárias e informações cuja precisão é essencial. Fundamentar, qualificar ou enriquecer quando indispensável. Fontes podem ensinar, aprofundar, inspirar, fornecer fatos/exemplos, fundamentar, contradizer, corrigir e nuançar; não determinam a estrutura intelectual. Distinguir fato estabelecido, informação documental, inferência, interpretação, hipótese e exemplo ilustrativo. Nunca inventar estudos, dados, depoimentos reais, acontecimentos ou bibliografia. Responsabilidade intelectual, não correspondência literal documental.

## III. Artefatos
### Planejamento Interno da Obra
Persistir concepção completa suficiente para o escritor executar sem reconstruí-la: missão, ideia central, ideias selecionadas/desenvolvimento previsto, relações/tensões, progressão narrativa, estratégias explicativas, profundidade/extensão, conclusão e contribuições documentais pertinentes. Representação acomoda gêneros diversos, sem sequência fixa. Preservar identificação, versão, vínculo com briefing/manifesto, conteúdo integral, revisões e aprovação. Recuperável após interrupção. Nunca aparecer na UI.

### Projeto Editorial
Checkpoint humano derivado fielmente do planejamento completo, vinculado inequivocamente à sua versão. Exatamente três campos visíveis:
- Ideia central: uma ou duas frases.
- Desenvolvimento narrativo: texto conciso com condução, relações importantes e avanço da compreensão.
- Conclusão pretendida: ponto intelectual/interpretativo/comunicativo de chegada.
Não inventar planejamento depois de apresentar PE.

## IV. Fluxo
Primeiro PE: briefing + corpus → concepção integral → planejamento completo → persistência → síntese fiel em três campos → persistência vinculada → UI. Corpus integral em uma janela; estratégia V8 de no máximo dois blocos quando necessário, com compreensão integrada na operação final.

Remoque: planejamento + PE vigentes + instrução + contexto → LLM compreende intenção/alcance, identifica decisões afetadas, preserva válidas, altera afetadas e consequências de coerência, verifica briefing/extensão, produz planejamento revisado completo → nova versão persistida → novo PE. Pode ser localizado ou estrutural. Manifestar mudança nos campos pertinentes, sem quarto campo. Quando já totalmente contemplado, informar objetivamente, sem simular modificação. Pesquisar via Hermes somente lacuna indispensável de conhecimento/evidência específica, não ausência de equivalência literal. Incorporar e reconsiderar planejamento pertinente. Preservar versões/retomada.

Aprovação única: usuário remoca ou aprova. Aprovar identifica PE/planejamento vigentes, persiste aprovação, agenda/inicia produção automaticamente, mostra progresso e entrega após verificação. Idempotência contra solicitações repetidas. Nenhuma segunda autorização editorial.

## V. Escrita
Escritor recebe briefing completo, planejamento integral aprovado, PE aprovado, corpus/contexto e público/formato/idioma/extensão. Executa concepção: desenvolve profundidade, conexões/transições, conhecimento e contribuições documentais, explicações/argumentos/descrições/exemplos, coerência e conclusão. Não enumeração nem um parágrafo por tópico. Transmitir meta quantitativa correspondente à encomenda; extensão dimensionada desde o princípio. Uma geração contínua quando cabe no output real. Persistir obra antes da verificação; falha posterior não repete escrita válida.

## VI. Verificação proporcional e correção
Recebe briefing, planejamento aprovado, PE, obra e contexto documental pertinente. Avalia cumprimento, extensão, coerência, progressão, inteligibilidade, repetições prejudiciais, contradições relevantes, fatos especificamente problemáticos e omissões do planejamento. Considera conhecimento confiável, certeza, natureza e contexto; não policiar todas as frases nem exigir corpus para conhecimento geral/interpretação/inferência. Problemas relevantes/acionáveis apenas: tipo, trecho/localização, explicação, correção necessária. Sem problemas, concluir.

Correções localizadas para problemas locais, revisão proporcional para múltiplas partes, preservando todas as partes válidas. Extensão insuficiente exige desenvolvimento intelectual ausente do planejamento e expansão coerente, nunca enchimento. Não exigir support.claim como substring literal de todo texto. Preservar integridade de referências sem inventário de citações. Persistir correções/checkpoints. Fundamentar/qualificar/enriquecer fatos específicos indispensáveis conforme necessidade real.

## VII. Infraestrutura preservada
Reutilizar ingestão/normalização, originais/endereços, corpus, Hermes, PostgreSQL/Neon, Cloud Storage, explicit caching, perfis/capacidade cognitiva, telemetria, recuperação, operações, autenticação e Cloud Run. Adaptar domínio/engine/provider/runtime/UI somente pelo contrato. Artefatos integram estado existente; schema aditivo e projetos anteriores preservados. Sem procedimentos por tema e sem faturamento.

## VIII–XI. Execução, testes e entrega
Ler estado/plano; mapear substituições; menor sequência técnica integrada; implementar planejamento/persistência, três campos, concepção, remoque/manifestação, aprovação/produção automática, escritor integral, verificação proporcional e integração/retomada/UI/telemetria. Testar sem modelo pago, corrigir falhas técnicas, commit único funcional coerente, promoção/deploy e smoke. Não CI/deploy a cada pequeno ajuste. Resolver impedimentos reais sem redefinir produto.

Fixtures sintéticas/neutras diferentes comprovam generalidade: finalidades/organizações distintas; desenvolvimento e extensão na concepção; PE derivado/vinculado; escritor recebe todo planejamento; conhecimento/ideias implícitas/inferências/interpretações/contradições legítimos sem correspondência frase a frase; fundamentação específica possível; remoque localizado preserva decisões e estrutural reorganiza, mudança no PE, versões recuperáveis; aprovação única e idempotente; obra persistida antes de verificar; problemas factuais reais detectáveis; correções preservam validade; falhas retomam sem repetir trabalho pago. Regressão de múltiplas fontes, explicit cache, checkpoints, operações e armazenamento.

Entrega aceita tecnicamente somente com o fluxo completo acima, todos os testes técnicos sem geração paga, registro do SHA funcional/plano, imagem/revisão implantadas e smokes. CI verde comprova engenharia, não qualidade intelectual; validação editorial pertence ao usuário.
