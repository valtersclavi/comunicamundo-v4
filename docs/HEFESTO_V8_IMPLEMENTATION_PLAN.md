# HEFESTO V8 — PLANO MESTRE DE IMPLEMENTAÇÃO

## Autoridade funcional vigente (2026-10-08)
O [CONTRATO EDITORIAL CANÔNICO](HEFESTO_V8_EDITORIAL_CONTRACT.md) substitui integralmente regras editoriais conflitantes deste histórico: concepção completa persistida, PE de três campos, aprovação única com produção automática, conhecimento geral legítimo e verificação proporcional. O histórico abaixo é memória de implementação, não autorização para restaurar comportamentos recusados. AGENTS.md histórico é reaproveitado apenas onde compatível com o contrato explícito do usuário.

## CURRENT STATUS
Last completed task: correção dimensionamento/hierarquia/reconsideração autônoma implementada e commitada; CI integrado SUCCESS com 228 testes sem skip, PostgreSQL 17, migrations, browser e imagem/startup/rotas.
Last commit: 1c8c38f8d8be7340d74e15747b17ef13aa8575b4 — commit funcional desta correção; documental anterior ao código 6b2566a18435a931a39b97928ef74a0ef11e336a.
Current task: [~] promoção concluída; deploy do commit funcional em execução pelo workflow Hefesto production 37869705877.
Next task: aguardar build/push do digest, migrations/check, deploy API/worker, startup/HTTP smoke; confirmar revisão/tráfego/imagem e registrar recibo. Nenhuma chamada editorial paga.
Known blocker: nenhum. Limitações locais de PostgreSQL/Docker/Chromium supridas pelo CI; testes reais passaram.
Paid validation authorized: no
Deploy status: IN PROGRESS — https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37869705877; SHA 1c8c38f8d8be7340d74e15747b17ef13aa8575b4. Campos Deployment commit/image abaixo ainda identificam a versão anterior até confirmação do novo deploy.
Product URL: https://hefesto-v4-api-wk5wcgnlua-uc.a.run.app/v8
Deployment commit: 4dd602ab4e3e06a982da84b78cf3c813b15ba920
Deployment image: us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/hefesto-v4-rebuild@sha256:468ba5664c896fe6092ec16361fe9ffa86f9fcf53711b3d0fdf75fc75fda9365
Deployment run: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37826667585 — SUCCESS em 2026-10-08 19:11 UTC; job 113481179395.
CI da entrega canônica: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37825641321 — SUCCESS; 216 testes sem skip, PostgreSQL 17 real, migrations, navegador, build/dependências e imagem/startup/rotas. Explicit caching e thinking máximo preservados.
IAM smoke: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37664761799 — SUCCESS; runtime testIamPermissions confirma cachedContents.create/get/list/update/delete. Zero chamadas de modelo e zero criações de cache. Job temporário excluído após execução.
Testes locais finais: 214 passaram, 2 integrações PostgreSQL externas puladas; ambas passaram no CI (216 no total). pip check, compileall, sintaxe Node, wheel e git diff --check passaram. Smokes de produção /health, /v8, assets e API privada 401 passaram; nenhuma geração editorial paga.
Main HEAD na inspeção inicial de 2026-10-07: 80748b2ce2993aa8f0d28e05262abef4b1a242a7. Branches main e hefesto-v8 sincronizadas por fast-forward após desenvolvimento na branch V8; commits documentais posteriores não mudam a imagem implantada.
Alterações: V8 completo em domínio/persistência/corpus/editor/Hermes/runtime/API/UI; parsers preservados. Checkout local raso validado contra SHA/tree/blobs exatos do GitHub. GitHub permanece memória canônica e fonte do histórico completo.

## Autoridade e continuidade
Este documento é o guia canônico e registro vivo da implementação na branch hefesto-v8 do repositório valtersclavi/comunicamundo-v4. Cada sessão começa lendo este documento no GitHub e verificando HEAD, branch, alterações e estado real. Não confiar na memória do chat. Atualizar tarefas, SHA, andamento, próxima tarefa exata, decisões, impedimentos e desvios justificados. Cada tarefa atômica concluída recebe commit coerente e atualização deste registro. Não desenvolver diretamente em main.

O último commit conhecido no plano de origem era 80748b2, sem garantia de ser HEAD. Inspecionar main e commits posteriores, inclusive Copilot; não presumir correção, não descartar, sobrescrever ou corrigir silenciosamente. Registrar reaproveitamento, correção ou abandono com fundamento no código real. Inspeção não inclui correções funcionais.

## 1–5. Objetivo e princípios
V8 é a oitava e última arquitetura prevista: não existe V9 para resolver depois. Construir produto genérico, robusto, recuperável, financeiramente viável, suficientemente rápido, editorialmente bom e comercializável. O legado pode funcionar temporariamente; comparação A/B V4/V8 não é objetivo, pois V4 foi considerado inviável.

Motivação: teste real com cerca de 3,5 milhões de tokens, pelo menos 25 minutos, três fontes, corpus estimado de 80 mil tokens e podcast de 20 minutos com resultado abaixo do esperado. O texto parecia colagem de fragmentos, em vez de obra nascida da compreensão do conjunto. Hipótese V8: fragmentação excessiva prejudicou uma atividade intelectual integrada que modelos modernos conseguem realizar.

Briefing dirige; LLM interpreta, relaciona, escolhe ênfases, sintetiza, explica, confronta e organiza narrativa; fontes fornecem lastro, credibilidade e verificabilidade. Afirmações substantivas verificáveis precisam de suporte recuperável. Fontes não são o cérebro do editor. Diferencial: controle editorial humano combinado com fundamentação identificável.

## 6–12. Projeto Editorial, remoque e busca
PE é proposta editorial curta, clara e humanamente editável: ideia central, estratégia narrativa, percurso de desenvolvimento e fechamento, sem prender esses nomes em schema rígido. Estratégias possíveis: cronologia, problema/consequências/soluções, personagem/contexto/interpretação, contraste, duas temporalidades e investigação progressiva.

PE não é resumo de fontes, inventário de evidências, matriz de cobertura, coleção de assuntos ou centenas de objetos. Não há interpretação única correta; usuário pode alterar tese, ênfase, pergunta, narrativa e conclusão.

Remoque é nova direção editorial: reconsiderar corpus e propor nova versão, não pequena edição textual. Exemplo: mudar de acompanhar a criança da terminalidade ao pós-morte para estratégias que minimizem seu sofrimento. Não reingerir ou redigerir tudo. Reutilizar mecanismo atual do provider (sessão, contexto persistente, cache, arquivos ou equivalente), verificando documentação/API atual antes de escolher. Corpus e estado próprios sobrevivem à expiração de cache.

Avaliar suporte à nova direção: suficiente → novo PE com corpus existente; insuficiente → Hermes busca, seleciona, adquire, incorpora e gera novo PE automaticamente. Remover da experiência V8 “Sugestões do LLM” e “Sugestões de fontes a serem buscadas”; mudanças entram por remoque.

Busca termina semanticamente somente em sucesso ou ausência justificável de suporte confiável após busca adequada. Nunca por número arbitrário de rodadas, consultas ou páginas. Não inventar lastro, aceitar fonte irrelevante nem tratar infraestrutura (timeout, rede, 429, indisponibilidade) como conclusão editorial. Interrupções técnicas são recuperáveis. Terraplanismo pode ser objeto histórico, mas não ganhar suporte científico fictício como modelo orbital.

## 13–18. Fluxos e ingestão
Com fontes: fontes → ingestão mecânica → corpus → contexto editorial → PE → remoque(s) → aprovação → redação → verificação → produto.
Sem fontes: briefing → Hermes (busca/seleção/aquisição) → corpus → mesmo fluxo V8. Não criar pré-PE, proposta baseada apenas em conhecimento do LLM, sugestões ou segundo pipeline.

Ingestão adquire, preserva, extrai, endereça e torna recuperável. Preservar original (PDF/EPUB/HTML/TXT/imagem etc.), identidade (título, autor, publicador, URL/DOI/origem, versão), texto integral, localização (página/seção/parágrafo/posição), tokens, integridade hash/checksum e método de extração.

Não antecipar compreensão: nenhuma ideia, resumo, argumento, conclusão, importância estimada, cobertura ou minidocumento editorial. Segmentos podem servir armazenamento, recuperação, embeddings e indexação; chunk é técnico, não intelectual.

Inspecionar parsers e reutilizar extração confiável. Não reescrever ou melhorar partes estáveis sem requisito V8 concreto. Antes de modificar, documentar função, invariantes e dependentes. Nenhuma refatoração cosmética.

## 19–25. Corpus e orçamento
Corpus é conjunto completo das fontes do projeto; tamanho principal em tokens, não páginas, MB ou número de fontes. Consultar capacidade real do modelo/provider, não números lembrados.
Orçamento útil = janela real − system prompt − briefing − PE/histórico necessário − instruções − output esperado − margem tecnicamente justificada.
Contar entrada real antes de chamadas. Nunca truncar nem inventar orçamento por conveniência.

Produto admite no máximo dois contextos úteis: cabe em um → direto; cabe em dois → dois grandes blocos; excede dois → pedir divisão do projeto antes de chamada paga. Não árvore ilimitada, mega-RAG ou vinte janelas.
Dividir preferencialmente entre fontes, capítulos, grandes seções ou conjuntos coerentes; representar integralmente toda parte, sem cortes silenciosos.

Uma janela: briefing + corpus integral + contrato editorial → PE, sem digestão. Prompt: não resumir fonte a fonte, não dar peso por tamanho/chunks/páginas/ocorrências; briefing define importância; perceber sustentação, qualificações, contradições e relações; construir visão narrativa.

Duas janelas: cada bloco recebe briefing completo e mesma orientação e produz leitura fundamentada preservando ideias/fatos relevantes, relações, tensões, contradições, nuances, elementos indispensáveis e localizações originais. Não resumos proporcionais. Leituras A+B+briefing → PE único. Esta é a única representação editorial intermediária normal, justificada pela fronteira física de contexto.
Uma fonte extensa pode contribuir três ideias; artigo curto pode carregar quase toda a narrativa, como no corpus de luto infantil com livro sobre cuidadores, artigo sobre crianças e material Kübler-Ross.

## 26–33. Escrita, verificação e proveniência
Após aprovação: briefing + corpus + versão aprovada do PE → obra. Uma janela usa originais novamente; PE orienta, não substitui corpus.
Narrativa existe antes da primeira frase; fontes servem à narrativa, que não segue ordem das fontes. Quando output cabe no limite real, gerar obra inteira continuamente (por exemplo podcast de 20 minutos), sem escritores independentes por seção.

Antes de gerar, calcular extensão e capacidade real de saída. Se não cabe, regra técnica explícita: divisão apenas em fronteiras narrativas aprovadas, preservando PE completo, estado narrativo, fatos introduzidos, transições e conclusão. Só construir mecanismo se formatos atuais realmente exigirem; não truncar, ocultar ou descobrir tarde.

Eliminar editor narrativo corretivo automático e revisão de estilo que reescreve toda obra. Verificador detecta requisitos ausentes, afirmações sem lastro, contradições, repetição grave, duração, truncamento e inconsistência. Problema → texto existente + problema específico + PE + fontes relevantes → correção localizada, preservando restante.

Proveniência interna recuperável por source_id/página/parágrafo/seção/localização, sem exigir podcast acadêmico ou longas citações intermediárias. Conhecimento geral serve raciocínio, linguagem e estrutura; fatos substantivos que exigem fundamentação devem encontrar suporte no corpus, sem fonte fantasma.

## 34–42. Persistência, recuperação e telemetria
Reutilizar Neon/PostgreSQL e armazenamento funcional. Persistir projeto, briefing, fontes, originais, texto extraído, manifesto, tokens, versões, PE e versões, remoque, aprovação, buscas Hermes, novas fontes, obra, suporte, job, checkpoints, telemetria e respostas brutas importantes. Banco preserva estado, não pensamento completo. Ligação estável registro↔original↔representação; bytes não precisam estar no relacional.

Persistir raw response ANTES de parsing. JSON inválido: preservar; recuperar deterministicamente; reparar somente resposta problemática se preciso; não reler corpus nem repetir centenas de milhares de tokens nem perder progresso. Structured output não é única barreira.
MAX_TOKENS: reconhecer, preservar parcial e continuar/reparar controladamente; nunca reiniciar tudo sem necessidade, reduzir arbitrariamente ou truncar silenciosamente.

Tarefas atômicas com objetivo claro: extração, corpus, contagem, contexto, PE, remoque, aquisição, escrita, verificação. Atomicidade operacional não fragmenta pensamento: propor PE ou escrever obra inteira que cabe é tarefa atômica.
Etapas caras retomáveis após 429, timeout, crash, conexão, parsing, JSON, restart. Identidade de operações inclui input/output e conclusão para impedir duplicação acidental e repetir somente quando necessário.

Telemetria por etapa: modelo/provider, versão/prompt, tokens entrada/saída/cache, latência, custo, chamadas, erro, retries, source IDs. Saber custo total e distribuição.
Caching/context reuse somente quando suportado e vantajoso; otimização, nunca verdade persistente. Cache perdido reconstrói sem perder corpus/PE/remoque/projeto.

## 43–50. Fronteiras e disciplina genérica
Hermes busca/seleciona/adquire; Hefesto compreende editorialmente, propõe PE, interage e escreve.
Acquire adquire exatamente fonte escolhida; cópia institucional/PDF/versão equivalente somente do mesmo documento, sem substituir por publicação parecida.

North star futuro: episódios de voiceover bíblico planejados externamente, um episódio por execução, integrando contexto histórico, formação textual, conteúdo e narrativa do livro, religião, temporalidades, controvérsias e acessibilidade. Êxodo/Levítico distinguem tempo narrado e composição numa narrativa fluida.
“80%” significa compreensão, não reprodução de versículos: leitor entende o que há no livro, razões, ideias e passagens estranhas (sacrifício, sangue, sacerdócio, pureza, expiação, festas, santidade, Jubileu, exemplos e contexto), sem enumeração.
Nunca condições especiais para Bíblia/luto/título/arquivo/fixture. Resolver propriedades genéricas para qualquer domínio. Testes demonstram defeitos, não definem software. Não melhorias oportunistas; mudanças só por requisito real, preservando componentes estáveis.

## 51–53. Estratégia e fases atômicas
Branch hefesto-v8; legado isolado durante transição. Não copiar sistema inteiro: reutilizar armazenamento, autenticação, infraestrutura, parsers, banco e jobs confiáveis, criando lógica editorial necessária com fronteiras explícitas.

Legenda: [ ] PENDING; [~] IN PROGRESS; [x] DONE; [!] BLOCKED.
Ordem só muda por dependência demonstrada no código e registrada com justificativa.

- [x] Fase 0 — documentar/congelar: branch, plano, commit só documental, HEAD main e SHA do plano.
- [x] Fase 1 — inspeção: histórico pós-80748b2/Copilot, fluxo, schema, ingestão, Hermes, PE, remoque, geração, jobs, checkpoints, UI/API; atualizar plano sem correções.
- [x] Fase 2 — contratos mínimos Source, Corpus, EditorialProject, Remoque, GeneratedWork, SupportReference, ExecutionState; adaptar existentes.
- [x] Fase 3 — persistência aditiva necessária: versões, raw, checkpoints, tokens, status; preservar V4.
- [x] Fase 4 — ingestão mecânica contínua/endereçável, extração confiável, sem transformação editorial no V8; manter legado.
- [x] Fase 5 — Corpus Builder: fontes/tokens/manifesto/orçamento real/1 ou 2 contextos/rejeição, sem chamadas editoriais.
- [x] Fase 6 — Context Provider simples: mecanismo atual recomendado, encapsular Gemini fora do domínio.
- [x] Fase 7 — PE direto: briefing+corpus, raw antes de parse, sem digestão de uma janela.
- [x] Fase 8 — remoque com contexto existente e nova versão, preservando anteriores.
- [x] Fase 9 — avaliar necessidade de fonte e acionar Hermes, sem sugestão de busca.
- [x] Fase 10 — zero fontes via Hermes→corpus→fluxo normal, sem pré-PE.
- [x] Fase 11 — dois contextos: leituras fundamentadas A/B→PE único, origens preservadas, máximo dois.
- [x] Fase 12 — aprovação explícita da versão, obrigatória antes de escrita.
- [x] Fase 13 — redação contínua com briefing+corpus original+PE aprovado.
- [x] Fase 14 — escrita mínima em duas janelas preservando narrativa, grounding, leituras e referências; sem máquina de chunks.
- [x] Fase 15 — proveniência recuperável de afirmações importantes, sem poluir texto público.
- [x] Fase 16 — verificação limitada e correção localizada.
- [x] Fase 17 — recuperação genérica: malformed,429,timeout,restart,parcial,MAX_TOKENS,cache perdido sem repetir caro.
- [x] Fase 18 — telemetria custo/tokens/latência por etapa.
- [x] Fase 19 — API/UI fontes ou sem→PE→remoque→aprovar→produzir, remover sugestões/matrizes/estados obsoletos do V8.
- [x] Fase 20 — limpeza só após caminho funcional, remover/isolar editorial V4 sem uso; legado não contamina V8.
- [ ] Fase 21 — validação real autorizada: três fontes existentes de luto infantil; avaliar PE, remoque, narrativa, duração, fundamentação, custo/tokens/tempo; sem A/B.
- [ ] Fase 22 — prova editorial complexa futura compatível com projeto bíblico, após estabilidade; não bloqueia infraestrutura.

## 54. Aceitação
Não basta rodar. Ingestão preserva original/conteúdo/endereço sem compreensão antecipada. Corpus tem tamanho conhecido, cobertura integral e decisão por capacidade real. PE curto/compreensível/narrativo/editável. Remoque reutiliza corpus e preserva versões, buscando quando necessário. Zero fontes converge ao mesmo fluxo. Busca conclui por suporte ou ausência justificada, não contador. Escrita tem começo/meio/fim sem colagem ou ordem das fontes. Afirmações relevantes têm lastro. JSON/429/restart preservam progresso; MAX_TOKENS não é silencioso. Telemetria mostra tokens, latência e custo. Engenharia sem exceções de fixture, melhorias oportunistas, regressões silenciosas ou limites arbitrários.

## 55–60. Commits, sessão, testes, custo e deploy
Cada tarefa concluída: commit coerente, SHA registrado, notas, testes e status. Um SHA não pode estar no próprio commit: registrar SHA já existente na atualização seguinte; commit atual é verificável pelo histórico Git.
Antes de terminar sessão: atualizar plano, último commit, parada exata, próximo passo, testes e deploy. Incompleto é IN PROGRESS e preservado na branch.

Testes protegem contratos genéricos: sem truncamento; raw sobrevive JSON inválido; retomada não repete etapa; >duas janelas rejeita antes de cobrança; remoque versiona; referências válidas; Acquire mantém identidade. Não fixtures para “fazer passar luto”.

Até infraestrutura pronta, nenhuma chamada paga para experimentar; mocks/fixtures/respostas gravadas/testes locais. Antes de primeira validação paga informar modelo, input aproximado, chamadas previstas e caminho, obtendo autorização. Autorização atual: NÃO.

Commit não implica deploy. Deploy só unidade funcional validável, após confirmar migrations, configuração, secrets esperados, branch/SHA e testes sem custo; registrar SHA efetivamente implantado.

Limitação real deve ser documentada/exposta/tratada. Nunca decisões silenciosas: truncamento, descarte, fonte substituta, redução de escopo, rodadas/chunks/caracteres arbitrários ou timeout como conclusão.

## 61–63. Princípio final e execução
Adicionar complexidade só por requisito real; remover somente entendendo proteção ainda necessária. Buscar menor arquitetura que cumpra integralmente produto, não menor número de linhas.
Hefesto V8 recebe briefing e corpus fundamentado, lê conjunto, propõe interpretação editável pelo usuário e, após aprovação, produz narrativa coerente sustentada. Hermes consegue fontes; ingestão prepara; banco preserva estado/proveniência; LLM pensa/escreve; usuário dirige.

Primeiro passo obrigatório: abrir repo; descobrir main HEAD; criar/usar hefesto-v8; gravar plano; commit exclusivamente documental; informar SHA; inspecionar código e commits posteriores; atualizar CURRENT STATUS; seguir fases atomicamente. Conflitos são registrados e resolvidos pelo objetivo arquitetural, sem adivinhação/correção oportunista.

## Registro de execução e decisões
2026-10-07: connector GitHub confirmou main em 80748b2ce2993aa8f0d28e05262abef4b1a242a7; busca não encontrou branch hefesto-v8; branch criada exatamente nesse SHA. Alegação de commits posteriores ainda será confrontada com comparação/histórico e branches. Nenhum código alterado. Clone local HTTPS requer credencial indisponível; acesso GitHub via connector funciona, portanto não é bloqueio da documentação/inspeção.

## Fase 1 — inspeção real, 2026-10-07 (em andamento)

### Histórico confirmado
Comparação 80748b2...main: identical, ahead=0, behind=0. Não há commits posteriores ao marco em main. Histórico recente: 80748b2 ← 44c1399 (remove speculative search/LLM suggestions) ← fd3f3fa ← 488a702. O conteúdo real contradiz uma interpretação de que 44c1399 removeu tudo: contratos, serviço, API e UI ainda carregam sugestões e pré-PE. Não presumir intenção pelo título do commit.

Branches observadas:
- main: 80748b2ce2993aa8f0d28e05262abef4b1a242a7.
- hefesto-v8: plano inicial cdfd5a9c1920adfcff9aacae8350ac6718551bc2 antes desta atualização.
- fix/final-narrative-editor: 391ea3f, ancestral de main, 13 commits atrás.
- refactor/no-speculative-suggestions: fd3f3fa, ancestral de main, 2 commits atrás.
- hefesto/pdf-caption-assets: fb1236e, divergente (2 commits próprios; 145 atrás); revisar patch integral antes de decisão.
- hefesto/text-first-incremental-ingestion: fd6c31f, divergente (7 próprios; 146 atrás), commits de 2026-09-24, não posteriores cronologicamente a main. Patch lido: PDF nativo, extração incremental, persistência por fonte, progresso UI e testes; também desativa estrutura/imagens de tabelas/figuras e documento estruturado. Main já apresenta vários mecanismos de PDF nativo e extração incremental. Não mesclar automaticamente nem importar redução de preservação dos assets. Nenhuma branch sobrescrita.

### Mapa funcional e reutilização
| Componente | Evidência no código | Decisão V8 |
|---|---|---|
| contracts.py | EditorialBrief; NormalizedSource.text; Paragraph com página/offsets; hashes; assets; SourceMode só provided | Reusar briefing/localizações/identidade; criar domínio V8 mínimo separado, sem herdar PE baseado em assuntos |
| ingestion.py | originais imutáveis, hashes binário/texto, normalização, callback por fonte, ThreadPoolExecutor; falhas de fontes registradas | Reusar extração; adapter V8 deve exigir manifesto explícito de integridade/completude, sem ignorar falhas/partial |
| document_normalization.py | pypdf-native-text-v1, canonical_text_uri, páginas/offsets; Docling para outros formatos | Preservar parsers; verificar requisitos de representação antes de qualquer alteração |
| storage.py | persist_bytes_immutable com if_generation_match=0; conflito compara bytes | Reusar armazenamento e invariantes; não duplicar sistema |
| postgres.py | runs JSONB, paragraphs/sentences/assets, atomic_model_tasks, subject_readings, call_usage, receipts, auth/uploads | Reusar acesso/infra; persistência V8 aditiva e versionada, sem sobrescrever PE anterior |
| service.py | select→propose_project; _create_project usa scaffold do briefing, routing e _create_subject_corpus; budgets 24k/120k/60k/5k; remoque por plano de assuntos | Manter legado isolado; V8 direto não chama seleção/compreensão por assunto |
| service.py produce_text | arquitetura narrativa, draft, review, correction, style, preservation review, final_narrative_edit | Não usar essa cadeia no V8; escrita contínua e verificação localizada |
| source_research.py | max_search_rounds=4, queries por assunto, SourceResearchExhausted | Não reusar política editorial de término; transportar clientes/acquisition sem número de rodadas como conclusão |
| source_pipeline.py | DOI/URL dedup; You/OpenAlex; aquisição direct/You/Jina/Browser Use; candidato preservado | Reusar transportes com testes de identidade/completude; identidade declarada no payload não prova equivalência documental |
| gateway.py | VertexGateway default gemini-2.5-flash; count_tokens; generate com schema | Adapter V8 separado, capacidade/provider atual verificados antes de orçamento/contexto |
| atomic.py | hash de tarefa, checkpoints e reparo; input_budget=64000; paginação por caracteres | Reaproveitar princípios/testes, não paginação editorial nem orçamento fixo no V8 |
| api.py/worker.py | autenticação, uploads, ownership, receipts, jobs/actions e retomada; pré-PE/suggestions persistem | Reusar infraestrutura; rotas/estado V8 explícitos |
| web/assets/app.js | find_for_me, pré-PE, suggestions e PE por assuntos | Adaptar só na Fase 19; legado intocado por ora |
| migrations | 1000 runs/paragraphs/sentences/usage; 1005 assets; 1007 atomic tasks | Aditividade; revisar demais migrations antes de schema V8 |
| .github/workflows/main.yml | push branches=[main], pytest e deploy Cloud Run/migrations/smoke | Commit V8 não dispara esse deploy por push; nenhum workflow disparado manualmente |

### Riscos concretos encontrados (não corrigidos nesta inspeção)
1. gateway.generate faz parsing antes de retorno; ModelResult não transporta raw. No finish_reason não STOP, lança erro antes de parsing sem response_text parcial. Fases 3/6/17 precisam garantir persistência raw anterior a parsing/validação/finish handling.
2. atomic.py persiste resultado validado no caminho curto, não resposta bruta anterior à interpretação. Reparo existente não satisfaz sozinho requisito absoluto V8.
3. gateway._usage fixa preços 0.30/0.03 (e preço de saída no código); telemetria V8 deve identificar modelo/provider e fonte/data de preços, sem assumir tarifa.
4. ModelGateway Protocol contém implementação embed duplicada; VertexGateway não mostra método embed concreto. Não refatorar oportunisticamente; V8 direto não precisa embeddings.
5. postgres.get_run contém normalização duplicada do PE antigo; cosmético, deixar intocado.
6. ingestão pode montar PlanningRequest com fontes bem-sucedidas e registrar falhas de outras. V8 não pode tratar corpus como integral sem decisão explícita sobre fontes pendentes/falhas.
7. SourceMode provided rejeita zero fontes, mas UI preserva find_for_me. V8 resolve pela entrada Hermes, sem reativar pré-PE legado.

### Conflitos com AGENTS.md e decisões
AGENTS.md lido. Regras de ingestão congelada, divisão em TODAS as chamadas sem teto de obra, enriquecimento por sugestões e editor narrativo refletem arquitetura anterior. Instrução explícita V8 do usuário prevalece somente no caminho V8: ingestão mecânica reaproveitada, teto de dois contextos, escrita contínua, sem sugestões/editor narrativo automático. Legado permanece protegido. Sem mudança silenciosa do AGENTS ou código atual. Mantêm-se proibições de truncamento, fixtures especiais, perda de estado e chamadas pagas não autorizadas.
Ordem de fases mantida. Capacidade/caching/provider atuais ainda NÃO verificados: obrigatório antes das Fases 5/6, sem hardcode de memória.
SHA Fase 0: cdfd5a9c1920adfcff9aacae8350ac6718551bc2.
Testes: nenhuma execução funcional; inspeção somente. Deploy: nenhum. Chamadas pagas: nenhuma.

## ADENDO OBRIGATÓRIO — ESTRATÉGIA DE EXECUÇÃO (2026-10-07)
Este adendo prevalece sobre qualquer interpretação que transforme V8 em pequenas entregas independentes.
1. Uma arquitetura, uma implementação integrada, uma entrega técnica. Avançar autonomamente pelo caminho completo até funcional e pronto para usuário testar. Tarefas/commits atômicos são disciplina interna, não pontos de parada, confirmação ou validação manual.
2. Plano é memória operacional (decisões/estado/retomada/próximo passo), não burocracia nem obrigação de parar em cada item.
3. Executar autonomamente unitários, código, integração, smoke, schemas, migrations em ambiente apropriado, build, rotas/estados e mocks/fixtures. Corrigir falhas antes da entrega.
4. Teste editorial real é do usuário. Não executar três fontes de luto infantil, PE/podcast pagos, avaliação editorial final ou comparação sem solicitação explícita. Entregar implementado, commitado, documentado, smoke e testes técnicos passando.
5. Não fazer A/B, V4 não é baseline. Legado pode permanecer isolado, sem infraestrutura de comparação.
6. Caminho exigido: fontes ou zero fontes→corpus→PE→remoque→eventual busca adicional→aprovação→redação→verificação→persistência/retomada. Incluir contexto, orçamento tokens, máximo dois blocos, erros, proveniência e telemetria.
7. Não parar por microfase. Fases 0–20 são checklist interno de uma entrega; fases editoriais 21/22 ficam para usuário após entrega técnica.


## Inspeção encerrada e implementação integrada (2026-10-07)
- Branch pdf-caption-assets comparada integralmente: blob document_normalization.py d29ad7f e teste ingestion 5195742 já iguais a main. Não mesclar branches antigas.
- Migrations 1000–1007/999 e testes existentes lidos. 144 testes legados passaram antes da implementação.
- Contratos V8 mínimos reutilizam EditorialBrief/SourceInput; PE é documento livre versionado, não assunto/matriz.
- Migration 1008 é estritamente aditiva: v8_projects e v8_operations. Reutiliza pool PostgreSQL, autenticação/uploads/GCS/jobs. Casos de perda de resposta externa são explicitamente incertos, sem promessa impossível de exactly-once em rede.
- Teto calcula capacidade documentada/datada do provider, sistema/schema/histórico e reserva máxima real de output mais próximo artefato PE/obra para verificação. Inline com prefixo estável e cache implícito; cache não faz parte da verdade.
- Dois blocos somente quando necessário; preferência por fontes/parágrafos, corte técnico apenas se unidade indivisível não cabe. Leituras fundamentadas e originais referenciados chegam à redação/verificação. Não árvore de chunks.
- Gateways legados/editor narrativo/sugestões/pré-PE não são utilizados pelo V8. Sem refatoração cosmética de duplicações legadas.
- Única extensão de transporte compartilhado: SourceAcquirer recebe predicado de aceitabilidade opcional, mantendo default legado. V8 não rejeita fonte por 800 caracteres/100 palavras; verifica identidade/completude/lastro semanticamente.
- Não iniciado teste editorial nem chamada paga, deploy ou merge.

### Núcleo integrado — conclusão técnica
Fases 4–18 implementadas em hefesto/v8: ingestão adaptada, corpus, contexto,
PE direto, remoque versionado, suporte/Hermes, zero fontes, dois blocos, aprovação,
redação única, proveniência, verificação localizada, recuperação e telemetria.
Commit dos contratos/persistência: 26e7a8f8c28ee38d10f33dd3dfa9dd363951ed1b.
Testes do núcleo: 22 genéricos V8 (incluindo percurso completo, transportes,
JSON/raw, parcial/MAX_TOKENS, restart, 429, timeout, cache, busca e identidade,
fonte curta, corpus e recuperação por ação). Nenhuma chamada paga.
Não afirmar custo zero de respostas perdidas nem de APIs externas sem tarifa;
telemetria marca custo desconhecido. Uma requisição interrompida antes de guardar
resposta só é repetida com reconhecimento explícito na interface de retomada.
No MAX_TOKENS, continuação substantiva recebe o contexto original reutilizável e
o prefixo já produzido; reparo JSON recebe somente resposta/endereço/erro.
Caminho duas janelas preserva originais referenciados; se representação final de
lastro não cabe, expõe limitação em vez de cortar conteúdo.
Modelo e preços verificados e documentados em HEFESTO_V8_PROVIDER.md. Aposentadoria
anunciada para gemini-2.5-flash em 2026-10-20 exige perfil/modelo explícito nessa
data, sem troca silenciosa. Uso inline/cache implícito não cobra cache explícito.

## Commit do núcleo integrado
Commit: bb7baadf1bac32ae8b568a23d0775e1a73ab8bcd. Fases 4–18 implementadas. 166 testes locais passaram (144 legados + 22 V8). Smoke visual com fixtures transferido ao CI devido a falha de download do Chromium neste ambiente. Nenhuma chamada paga.

## API/UI e revisão técnica final
Commit integrado API/UI/testes: 6cb011cca0ac1c1f2d9f02d41cc52de741bbd5e5 (Fases 19–20). Legado isolado, sem remoção dos parsers ou migração destrutiva. V8 usa /v8 e tabelas próprias aditivas.
167 testes locais passaram após proteção de falha de inicialização do worker. CI inicial confirmou os 166 anteriores; falhou somente no empacotamento sem setuptools no runner limpo. Correção: build com ambiente isolado declarado em pyproject, sem pressupor dependências do runner. Smoke navegador usa localhost como contexto seguro; não chama providers.

## Modelo e evidência de CI
Modelo retirado ou perfil desconhecido agora produz pausa de configuração recuperável, não erro de tamanho do corpus; nenhuma capacidade retirada é cacheada. 168 testes locais (24 V8) passaram. Modelo padrão mantém a documentação verificada em 2026-10-07 e retirada prevista para 2026-10-20; troca exige modelo/perfil explícito, nunca fallback silencioso.
CI https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37628573456 (e9777d6): testes/build/migrations e smoke navegador passaram; imagem Docker em execução. Smoke navegador: login, zero fontes, proposta, histórico de versões, remoque, aprovação, produção, referências e viewport iPad, com fixtures e zero chamadas LLM.
Commits somente docs não repetem o CI caro sem mudanças de código. A validação de imagem usa o Dockerfile existente e parsers/modelos documentais já previstos; não toca em produção.

## Recuperação de processo interrompido na UI
A API já permite retomar estados ativos preservados após crash; a UI agora também oferece Retomar nesses estados. Uma execução ainda ativa é protegida pelo advisory lock (409), enquanto o processo morto libera o lock e reaproveita o journal. Smoke de navegador inclui a retomada. CI sem deploy cancela revisões anteriores quando chega nova correção para validar apenas a versão atual.
Commit anterior: b75e92d0a0b29cea2af450cf5c225c5718981972 (falhas de configuração do provider; 168 testes locais passaram).

## Persistência concorrente em Neon
Invariante: pg_try_advisory_lock é de sessão; Neon/PgBouncer em transaction pooling não preserva essa sessão. Documentação primária consultada em 2026-10-07: https://neon.com/docs/connect/connection-pooling e https://github.com/neondatabase/website/blob/main/content/docs/get-started/connect-neon.md. V8 exige HEFESTO_POSTGRES_DIRECT_URL (rejeita hostname Neon -pooler), mantendo o pool legado. Deploy existente recebe a vinculação adicional desse secret na API; job já possuía o secret. Nenhum deploy realizado.
Teste de integração independente em PostgreSQL 17 no CI verifica dois pools/sessões, exclusão de worker concorrente, revisão stale, fechamento/reabertura e preservação de PE/action/raw malformado. Local: 168 passaram, 1 integração foi pulada por falta de PostgreSQL externo; SQL completo já verificado em PostgreSQL WASM. O teste real será executado automaticamente no CI isolado, sem Neon de produção, sem LLM.

## Idempotência na fronteira UI/API
A UI preserva project_id e uploads bem-sucedidos ao repetir a mesma submissão depois de falha de resposta HTTP. O Backend identifica a mesma execução e devolve o job existente, evitando nova geração por retry acidental. Novo projeto limpa a identidade. Smoke navegador simula perda de resposta e exige submissão idêntica no retry, além da retomada de processo interrompido.

## ENTREGA TÉCNICA CONCLUÍDA — 2026-10-07
Revisão funcional validada: 39dc77942654cb0552120585bdd9d5f59284846a.
Registro documental anterior: c5691e304697d352a6505cb35b4372818e2589cf; o SHA deste registro final está no histórico Git (não pode ser inserido no próprio commit).
CI SUCCESS: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37630267314, job contracts-build-smoke 112822929832.
Evidência dos logs: 169 passed, 1 warning in 2.96s. O warning é depreciação de Starlette/AnyIO, sem falha.
Todos os gates passaram: dependências; contratos/unidade/integração/rotas; PostgreSQL real (locks entre sessões, stale revision, raw e retomada); migrations históricas+1008/reaplicação/FK; compile; JS syntax; wheel/worker; smoke navegador (login, zero fontes, retry idempotente, retomada, PE/versões/remoque/aprovação/produção/fundamentação/iPad); Dockerfile de produção sem alterações; smoke documental da imagem; healthz; /v8; /v8/projects sem autenticação retorna 401.
Nenhuma chamada editorial paga, nenhum A/B, nenhuma migration em produção, nenhum merge e nenhum deploy. Main permanece 80748b2ce2993aa8f0d28e05262abef4b1a242a7. O caminho V8 é /v8 após deploy, não a tela legada /.
As Fases 21/22 são validações editoriais futuras do usuário, não tarefas técnicas pendentes do assistente neste esforço. Qualidade editorial real e disponibilidade/configuração de provider em produção não foram testadas por chamadas pagas.
A entrega está pronta para implantação e teste editorial pelo usuário depois da implantação. Antes do deploy, conferir secrets esperados no documento de operações, direct URL na API/job, migrations e revisão exata. Modelo padrão/provider/capacidade/preços/retirada estão no documento HEFESTO_V8_PROVIDER.md; retirada anunciada em 20/10/2026 exige modelo/perfil explícito atualizado, sem fallback silencioso.

## Autorização e preparação de deploy — 2026-10-07
Usuário solicitou explicitamente: terminar deploy/commits e deixar pronto para o teste. Isso autoriza promover a branch validada para produção; desenvolvimento continua em hefesto-v8, sem alterar main diretamente durante implementação. Main reconfirmada em 80748b2; V8 em cb40b3e (mesmo código funcional 39dc779 validado pelo CI SUCCESS 37630267314).
Pipeline existente publica API/job na mesma imagem por digest, aplica migrations antes de atualizar API e executa smoke do job com --help (nenhum LLM). Secrets esperados: pooler/direct PostgreSQL, You/OpenAlex/Jina/Browser Use; job já vinculava todos, API vincula pooler/direct. Vertex V8 é explicitamente configurado com modelo/perfil já verificado e localização global. Nenhuma chamada editorial paga autorizada.
Preparação adiciona smoke HTTP publicado (/healthz, /v8, asset JS, projetos sem sessão=401) e registra recibo no resumo/logs de Actions. Remove comentário automático em issue: o usuário autorizou deploy, não mensagens para terceiros. Commits somente docs não provocam redeploy. UI V8 fica em /v8; / permanece legado durante transição.

## Checkpoint do deploy ativo
Commit de release: 8e75454af470ddea35831d5ca6957ce15a8eadec. Promoção main: fast-forward de 80748b2, expected SHA conferido, sem overwrite ou force. Branch de desenvolvimento permanece hefesto-v8.
Run produção: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37634099690. Job 112835638798. CI técnico concomitante: run 37634067634.
Histórico real: deploy anterior de main80748b2 (37157270246) falhou na execução de migration hefesto-v4-rebuild-h4pgv. A última versão deployada com sucesso antes dele era fd3f3fa (37144909653). O diff dessa versão para80748b2 não modifica nenhuma migration; a causa da falha anterior não está identificada nos logs de Actions. Não presumir conflito de checksum ou modificar migrations antigas por hipótese. A migration atual será avaliada pelo resultado real.

## Correção de infraestrutura do CI durante deploy
Run técnico 37634067634: testes 169/PostgreSQL/migrations/navegador passaram, mas Docker falhou com OSError errno28 (No space left on device), após coexistirem PostgreSQL e dependências Playwright no runner. Run de produção 37634099690 construiu e passou smoke da mesma imagem em runner sem esses recursos.
Correção genérica: separar build/smoke da imagem em job dependente com runner limpo; primeiro job mantém PostgreSQL/navegador/testes. Dockerfile/parsers/software V8 não mudam. Validar novo CI antes de fechar entrega. Não interpretar falha de infraestrutura como falha editorial.

### Diagnóstico de deploy — 2026-10-07
Migration de produção ainda aguardando conclusão. Diagnóstico somente leitura na branch V8 foi recusado pela condição WIF (run 37636817108); manter condição IAM existente e executar diagnóstico em main. Workflow dedicado lê metadata/logs, sem secret values, SQL modificador ou modelos. Workflows exclusivamente técnicos/diagnósticos não disparam rebuild de produção; ajuste de controle de CI será promovido com skip ci, sem alterar imagem já publicada. Próxima ação exata: executar diagnóstico autorizado em main e resolver a causa real encontrada.

Diagnóstico run 37637011804 concluído: WIF de main funcionou; não alterar IAM. Cloud Run levou minutos para importar/iniciar containers; não era erro SQL. Migration `hefesto-v4-rebuild-lwfsn` SUCCESS aplicou somente `1008_hefesto_v8.sql`; check `hefesto-v4-rebuild-xlwhb` SUCCESS retornou banco neondb pronto. Metadata revelou que a falha antiga `hefesto-v4-rebuild-h4pgv` era de **check-postgres**, após migration antiga bem-sucedida, corrigindo interpretação anterior do nome do step. Nenhum checksum histórico foi alterado. Startup sem modelo `hefesto-v4-rebuild-dh28d` em andamento. Próxima tarefa: confirmar startup/live HTTP e registrar recibo final.

Correção necessária para entrega: diagnóstico run 37637438138 confirma /v8 e JS 200 e API privada 401 em ambas as URLs; somente /healthz retorna Google 404. Documentação oficial Cloud Run known-issues identifica reserva de paths terminados em z. Adicionar alias `/health`, preservar legacy `/healthz`, smoke público usar alias permitido. Nenhuma alteração editorial/parser/banco. Teste existente de health sem cloud clients adaptado à rota pública. Próxima ação: commit, promoção autorizada, novo deploy e smoke público.

[x] Rota pública de health corrigida — commit e372045fad460f304b3a5e96a7945db40a52b27e. 25 testes API e compile local passaram; suíte geral em execução. Migração 1008 já está aplicada na produção. Runs cancelados de CI auxiliar resultam da política cancel-in-progress enquanto commits diagnósticos eram publicados; não correspondem a falha de testes. Não repetir mudança funcional nem parar após esta tarefa: seguir até deploy/smokes finais SUCCESS.

## Recibo final de entrega técnica — 2026-10-07

[x] Deploy autorizado e concluído — commit e372045fad460f304b3a5e96a7945db40a52b27e.
- API revision: `hefesto-v4-api-00086-rnx`.
- API/job image: `us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/hefesto-v4-rebuild@sha256:2ed432caf417ab81aeb1dd07afb4272cd02988d1cf22e9fd6e1cf81bb761d71f`.
- Migration execution: `hefesto-v4-rebuild-prb44` — SUCCESS; migration 1008 já aplicada na primeira publicação, preservada na reexecução.
- Database check execution: `hefesto-v4-rebuild-hsz4j` — SUCCESS.
- No-model job startup execution: `hefesto-v4-rebuild-7v2rr` — SUCCESS.
- Public HTTP smoke: `/health` ready; `/v8` e JS disponíveis; `/v8/projects` sem sessão retorna 401.
- Production Actions run: 37637702188 — SUCCESS.
- Technical Actions run: 37637700213 — SUCCESS; 169 testes + migrations + navegador + imagem. Falha de espaço anterior no runner foi resolvida separando build da imagem do runner PostgreSQL/Playwright; não alterar parsers/Dockerfile.
- Rota `/healthz` preservada como alias local; usar `/health` no Cloud Run, conforme documentação de paths reservados. Diagnósticos de produção foram somente leitura e WIF permanece restrito a main.
- Migrations, configuração, secrets referenciados, branch/commit e testes técnicos conferidos antes e durante deploy; nenhuma credencial secreta registrada neste documento.
- Nenhum teste editorial real, geração paga de PE/podcast ou A/B foi executado.
- Próximo passo exato: usuário testa o produto pelo link acima. Fases 21–22 permanecem validações editoriais futuras sob responsabilidade/solicitação do usuário conforme adendo; não há tarefa de implementação interrompida nesta entrega.

Fim de sessão: todos os arquivos funcionais commitados; registro final documental é commitado nas duas branches, sem rebuild por alteração apenas em docs. Estado de produção corresponde ao commit funcional e digest registrados acima. Não confundir HEAD documental com revisão da imagem. Configuração/modelo e prazo de disponibilidade estão em HEFESTO_V8_PROVIDER.md; verificar novamente em sessões futuras.

## Correção isolada do provider — maximum thinking (2026-10-07)

[x] Provider V8: usar thinking_budget_max=24576 do perfil verificado Gemini 2.5 Flash e temperature=1.0. Consulta oficial refeita hoje: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking e https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash. Fonte/prices: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing.

Escopo: somente provider.py, testes técnicos e documentação deste provider/registro. Nenhuma alteração em ingestão/corpus/PE/remoque/Hermes/busca/banco/schemas editoriais/redação/verificação/proveniência/UI/zero-fontes/caching ou workflows. Limite cognitivo e fonte pertencem ao perfil por modelo; perfil ausente/inválido pausa antes de chamada, sem fallback. max_output_tokens existente (65536) mantido; telemetria/custo de reasoning já existente mantida e protegida por teste do journal/reuso. topP/topK intactos. Sem chamadas de geração pagas.

Testes locais desta correção: 177 passaram, 1 integração PostgreSQL externa pulada; 33 testes V8 passaram, incluindo 9 novos casos parametrizados. pip check, compileall, Node syntax, git diff --check passaram. Scan do caminho src/comunicamundo_v4/hefesto/v8 não encontrou thinking_budget=0 ou temperature=0.3. Smoke navegador/PostgreSQL/imagem serão executados pelo workflow técnico existente na branch antes da promoção para deploy.

[x] Código da correção mínima do provider — commit 96adcf86545ae103dd528678aceba7512e3465ae. [~] CI técnico 37650271569; após SUCCESS promover fast-forward para main, executar deploy/smokes não editoriais e registrar SHA/imagem. Não há alteração de workflows ou de outros componentes funcionais.

[x] CI técnico desta correção — run 37650271569 SUCCESS: 178 testes, PostgreSQL 17, migrations, navegador, wheel/build/dependências, imagem/startup/rotas sem modelos. [~] Deploy da correção — run 37651327855, commit alvo 96adcf86545ae103dd528678aceba7512e3465ae. Promoção respeitou HEAD esperado de main; documentação posterior não dispara rebuild. Apenas provider/tests/docs alterados; nenhum teste editorial pago.

### Recibo final da correção mínima do provider — 2026-10-07

[x] Correção e deploy concluídos — SHA efetivamente implantado `96adcf86545ae103dd528678aceba7512e3465ae`.
- Gemini 2.5 Flash: thinking_budget=24576 (máximo manual do perfil verificado); temperature=1.0 (padrão nativo). Sem modo automático, orçamento econômico ou heurística por etapa. topP/topK intactos.
- Limite cognitivo e fonte são específicos do perfil; modelo sem limite verificado positivo/fonte pausa antes de generate_content. Nenhum fallback silencioso.
- Registro de input/output/cached input/reasoning/modelo/custo mantido; teste SDK+journal confirma pensamentos efetivos e custo combinado, sem repetir chamada concluída.
- CI técnico 37650271569 SUCCESS: 178 testes + migrations + navegador + imagem.
- Deploy 37651327855 SUCCESS; API `hefesto-v4-api-00087-p5p` e worker `hefesto-v4-rebuild` com `us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/hefesto-v4-rebuild@sha256:c60e9c2783ee1bffe988698811c44699166b957749338aefa74908636973e7c2`.
- Migration/check existentes: `hefesto-v4-rebuild-vvvds` / `hefesto-v4-rebuild-62rqv` SUCCESS; nenhuma migration ou mudança de banco nesta correção.
- Startup sem modelo `hefesto-v4-rebuild-wmpxr` SUCCESS; smoke HTTP público /health + /v8 + assets + API privada 401 SUCCESS.
- Escopo confirmado no diff do commit: somente provider.py, tests/test_v8.py, documentação do provider e plano operacional. Nenhuma outra lógica funcional ou workflow alterado.
- Nenhuma geração editorial paga e nenhum teste de luto infantil. Próxima tarefa exata: usuário realiza o primeiro teste editorial.

Fim desta sessão: correção commitada e implantada; branches main/hefesto-v8 sincronizadas por fast-forward; registro documental posterior não altera a imagem. Não há mudança funcional incompleta nesta correção. Consultar o SHA funcional/digest acima para identificar produção, e HEAD para documentação atual.

### Correção obrigatória de Explicit Context Caching — 2026-10-07

[x] Implementação isolada do reutilizador econômico de corpus — commit funcional 94aed16210ded9d327bb0695a73d50aa0c4633c5; sem alteração editorial.

Documentação oficial Google Cloud/Google Gen AI SDK consultada antes do código, URLs em HEFESTO_V8_PROVIDER.md: caches.create/get/update/list/delete; cached_content; mínimo Gemini 2 = 2048 tokens; TTL padrão 60 minutos; global suportado; 10 MB inline, acima via GCS; criação como input normal, leitura com desconto 90% e storage Flash USD 1/M tokens/h. Limites e preços pertencem ao perfil verificado por modelo.

Decisões: cache apenas corpus endereçado imutável; identidade por execução/provider/modelo/projeto/localização/manifesto/bloco/formato; PE/remoque/schema/operação dinâmicos. Cache sem system instruction e request cached sem system_instruction (restrição real); contrato/schema preservados em conteúdo dinâmico. Persistir metadados/eventos no v8_operations existente; nenhuma migration/schema editorial. TTL 3600s renovado ao reutilizar; inatividade expira; reconstrução do corpus persistido. Recurso substituído é descartado depois de existir o novo. Erro elegível pausa recuperavelmente, sem reenviar corpus inline. Listagem reconciliada recupera criação cuja resposta se perdeu. Journal editorial não inclui resource name na identidade.

Uma janela usa um cache para PE/suporte/remoque/redação/verificação/correção localizada; duas janelas usam seus dois caches nas leituras. A síntese existente de leituras + suporte necessário permanece inline, sem terceira janela/cache. Reparo sintático usa apenas resposta problemática. Preflight soma cache + input dinâmico + output uma vez. Telemetria separa input lógico/cacheado/novo/output/reasoning, criação/reuso/recriação/expiração/descarte, custo de geração/criação e estimativa de storage/lease. Thinking 24576 e temperatura 1.0 preservados.

Escopo funcional: context.py novo, provider.py transport/journal, runtime.py apenas projeção de telemetria e engine.py apenas indicação neutra de bloco para transporte da correção localizada. Ingestão/corpus builder/Hermes/PE/remoque/redação/verificador/proveniência/UI/schema/migrations/workflows intactos em sua lógica. Testes novos inteiramente mocks; integração PostgreSQL persiste cache e retoma em conexão independente. Nenhuma geração editorial ou cache pago criado; nenhum teste com luto infantil.

Testes locais da correção de cache: 195 passaram, 2 integrações PostgreSQL externas puladas (CI usa PostgreSQL 17 real). pip check, compileall, sintaxe Node e git diff --check passaram. Build wheel em validação; CI completo será obrigatório antes da promoção. O teste de correção localizada inicialmente tinha um mock sem o campo obrigatório kind; o mock foi corrigido, sem mudança no contrato/software editorial.

[x] Build wheel local SUCCESS; SDK/python imports/sintaxe/dependências validados. Testes do CI permanecem pendentes.

[x] Correção de explicit caching commitada — 94aed16210ded9d327bb0695a73d50aa0c4633c5. [x] CI técnico run 37657414964 SUCCESS: 197 testes sem skip, PostgreSQL 17 real (inclusive retomada de cache em conexão independente), migrations, build wheel, dependências, navegador e imagem/startup/rotas sem modelo. Jobs 112915937454 e 112916611494 SUCCESS. [x] Deploy por fast-forward para SHA funcional — run 37660818246 SUCCESS. Nenhum cache real pago criado e nenhuma geração editorial paga.

### Recibo final da correção de explicit context caching — 2026-10-07

[x] Implementação/CI/deploy — SHA funcional implantado 94aed16210ded9d327bb0695a73d50aa0c4633c5.
- API hefesto-v4-api-00088-ldh; worker hefesto-v4-rebuild; digest e64fb6ce712ff8fb05743ae75aca4cd134b5cb045e9a976761e182193914b28c. Deploy 37660818246 SUCCESS.
- Migration hefesto-v4-rebuild-ns8dw, check hefesto-v4-rebuild-6chsr e startup sem modelo hefesto-v4-rebuild-v2qw9 SUCCESS; nenhuma nova migration/schema nesta correção. HTTP público /health + /v8 + assets + API privada 401 SUCCESS.
- Mecanismo: Google Gen AI SDK aio.caches.create/get/update/list/delete e generation.cached_content. Corpus endereçado é conteúdo imutável; briefing/PE/remoque/contrato/schema continuam dinâmicos; nenhuma duplicação inline do corpus nos requests de geração elegíveis.
- TTL deslizante 3600 segundos, mínimo do perfil Flash 2048 tokens; limite inline 10 MB, acima disso representação mecânica imutável no GCS existente. Expiração/perda reconstrói apenas corpus persistido. Cache não compõe identidade editorial. Erro elegível pausa sem fallback integral. Duas leituras usam no máximo seus dois recursos; síntese existente mantém leituras + suporte inline, sem terceira janela/cache.
- Telemetria registra criação/reuso/recriação/expiração/descarte, modelo/manifesto/bloco/recurso/tokens, input lógico/cacheado/novo, output/reasoning, latência, geração/criação e estimativa de armazenamento/lease. Thinking 24576 e temperatura 1.0 preservados.
- CI funcional 37657414964: 197 testes sem skip, PostgreSQL 17 e retomada entre conexões, migrations/build/navegador/imagem.
- Diagnóstico técnico estritamente necessário acrescentado ao workflow já existente para confirmar configuração de cache e esclarecer espera do Cloud Run. A tentativa inicial de getIamPolicy (37664292953) falhou porque a conta de deploy não possui leitura de políticas de projeto; nenhuma permissão foi ampliada. Substituída por testIamPermissions na própria conta runtime via job temporário sem secrets, modelo ou cache. Commit de diagnóstico 118d32d1720b4ca8c0174fbe795b688059b74613; run 37664761799 SUCCESS.
- IAM runtime confirmou aiplatform.cachedContents.create/get/list/update/delete. Execução hefesto-v8-cache-iam-37664761799-dzjxp SUCCESS; job temporário hefesto-v8-cache-iam-37664761799 excluído. Produção/IAM/secrets não foram alterados pelo diagnóstico. Documentação oficial do teste: https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions .
- Nenhuma geração editorial paga, nenhum cache real pago criado, nenhum teste de luto infantil. Próxima tarefa exata: usuário realiza o primeiro teste editorial no produto.

Fim da execução funcional: commit e imagem implantados, sem mudança editorial incompleta. Registro documental não dispara rebuild de produção. CI final do diagnóstico 37664763899 SUCCESS (jobs 112941394219 / 112942948754); encerramento documental concluído. Branches main/hefesto-v8 atualizadas sem sobrescrever código externo. Nenhuma tarefa funcional incompleta e nenhum job temporário deixado ativo.


## Correção isolada — colisão de snapshot na ingestão (2026-10-08)
[x] Namespace estável por operação de extração de fonte. Commit: 03535f2b91c8f35975ff784e54207cfdf28ea732.
[x] CI completo: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37774687636 — SUCCESS, 200 testes sem skip, PostgreSQL 17 real, migrations, navegador e imagem/smoke sem modelo.
[x] Deploy/smokes: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37775589797 — SUCCESS, SHA funcional 03535f2b91c8f35975ff784e54207cfdf28ea732. main promovida por fast-forward após CI; commits documentais não disparam deploy.

Estado verificado: main e hefesto-v8 em 8ebd53a1d10834247a640bbec7f054e1085b9104. Produção ainda no código funcional 94aed16210ded9d327bb0695a73d50aa0c4633c5, revisão hefesto-v4-api-00088-ldh.

Erro relatado: immutable object conflict em hefesto-product/projects/6d9d95bc-8d4c-4781-8b23-a93d647175cb/normalized-request.json. Não é JSON malformado. O adaptador V8, introduzido no commit bb7baadf1bac32ae8b568a23d0775e1a73ab8bcd, chama o ingestor legado uma vez por fonte usando o mesmo project_id editorial. O ingestor preservado escreve um snapshot imutável por project_id; a segunda fonte tenta gravar outro JSON válido no caminho da primeira. Correções posteriores de thinking e explicit caching não alteraram a ingestão.

Decisão: somente Ingestor.request no adaptador V8 usa UUID determinístico derivado de execution_id + identidade canônica de SourceInput, com versão v8-source-ingestion-v1. Isso isola snapshots de cada tarefa atômica e mantém identidade estável em retries. A mesma função atende extração e aquisição Hermes. Não alterar parsers, normalização, persistência imutável, corpus, arquitetura editorial, thinking ou caching. Não apagar objetos antigos nem sobrescrever fonte/checkpoint concluído. A execução pausada preserva sua primeira fonte e pode processar somente as pendentes na retomada.

Testes novos usam o ingestor mecânico real e armazenamento simulado com semântica imutável real: reproduzem a colisão anterior, verificam duas fontes completas/endereço/original/hash sem colisão, idempotência da extração e preservação do snapshot antigo; interrompem na segunda fonte e retomam sem reler a primeira (original de entrada removido no teste); verificam isolamento por fonte/execução e estabilidade em restart. Provider é mock, sem modelo real. Testes anteriores do engine usavam ingestor mock e não cobriam essa fronteira; corrigida a lacuna de integração.

Validação local: 198 testes passaram e 2 testes PostgreSQL externos pulados; três novos passaram. pip check, compileall, Node syntax, wheel e git diff --check passaram. CI executou PostgreSQL 17 real, migrations, UI/API e imagem de produção: 200 testes, todos passaram. Deploy desta correção concluído; recibo atual no CURRENT STATUS. Nenhuma geração editorial paga, nenhum cache real criado, nenhuma retomada automática do projeto do usuário.


### Recibo final da correção de ingestão (2026-10-08 12:36 UTC)
[x] Commit funcional implantado: 03535f2b91c8f35975ff784e54207cfdf28ea732.
[x] CI 37774687636 / jobs 113302476299 e 113303106861: SUCCESS, 200 testes, PostgreSQL 17, migrations aditivas/idempotentes, browser mock, build e imagem real sem modelo.
[x] Deploy 37775589797 / job 113305472351: SUCCESS. API hefesto-v4-api-00089-gkw servindo 100% do tráfego; worker hefesto-v4-rebuild no mesmo digest sha256:ef4a9c518e96240bf46b95b404074e85e2927633d44fe07fa6287790aa402c35.
[x] Migration hefesto-v4-rebuild-pbxvr, PostgreSQL check hefesto-v4-rebuild-b8lv2 e startup sem modelo hefesto-v4-rebuild-9fs49: SUCCESS. /health, /v8, assets e API privada 401: SUCCESS.
[x] Parsers, ingestão legada e proteção de objetos imutáveis preservados; thinking máximo/temperatura/explicit caching preservados. Não foram apagados snapshots nem alterados checkpoints da execução real. Nenhuma geração editorial paga e nenhum cache real criado. Assistente não retomou o job editorial do usuário.

Ponto de parada: correção implantada e documentada; próximo passo é o usuário atualizar a página V8 e clicar Retomar processamento no projeto pausado. O teste de retomada com ingestor mecânico real confirma preservação da fonte concluída e extração somente das pendentes; não é promessa de validação editorial paga que não foi executada. Main e hefesto-v8 sincronizadas, registros documentais posteriores não mudam a imagem de produção.


## Diagnóstico somente leitura — pausa na verificação (2026-10-08 13:39 UTC)
Pedido explícito do usuário: somente diagnosticar, não corrigir. Nenhuma mudança na lógica/configuração do produto, dados/checkpoints, job editorial ou modelo; nenhum deploy ou chamada de geração/cache. Acesso recuperado pelo caminho GitHub Actions/WIF → runtime existente → Secret Manager/PostgreSQL, sem expor secrets. Job temporário somente leitura executou BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY, confirmou transaction_read_only=on, fez SELECT e ROLLBACK; job foi excluído. Workflow diagnóstico temporário retirado ao concluir; fontes/respostas continuam no banco. Commits diagnósticos não disparam deploy de produção.

Diagnóstico: projeto e87adb10-74bc-48ec-a986-4ee621a5d425 / execução 5c7cfbd8-b623-5dbd-b439-8f1b00704e58. Pausa registrada 2026-10-08 13:21:03 UTC. Operação verify 0b381b02392a8c10ce9cacd73f6f8a1de35356cc7f4074becc66a1887169a063, parse_error: support claim absent from body. Três tentativas (initial, repair, repair) com STOP e JSON válido, de 16144/14243/14243 caracteres. Dois reparos finais exatamente iguais; o digest igual provoca response repair made no progress; all raw outputs preserved.

Primeiro bloqueio: podcast contém O "mundo que a criança conhecia" torna-se confuso e desorganizado.; claim da verificação usa aspas simples no mesmo trecho. Calls.validate exige claim in body sem normalização. Verificação inicial tem oito claims ausentes literalmente; reparos mantêm cinco, quatro com diferenças de aspas e um com redação diferente. Isto é falha de validação técnica da resposta do verificador, não rejeição editorial global nem JSON malformado. Prompt de reparo contém assembled/parse_error/endereços válidos; não contém state.work.body que a validação utiliza. Não foi aplicada correção.

Podcast preservado: O Luto na Infância: Um Caminho de Cuidado e Compreensão, 8092 caracteres, 1306 palavras por body.split (aprox. 8,7 min à referência de 150 palavras/minuto). Verificador apontou duração curta (sua estimativa de 1740 palavras é imprecisa), repetição e uma comparação não explicitamente sustentada sobre luto antecipatório. Esses achados são distintos do bloqueio técnico; correções locais da verificação não chegaram a executar.

Coleta: workflow de SELECT 37785224715/job 113338130098 SUCCESS; coleta integral dos logs após ingestão 37785997587/job 113340776792 SUCCESS. Exportação integral validada por 81 partes e SHA256 46493d5bd6a11aaf116c13bfd8409ca83376f2e448179307598bd451c9abd358. Primeira coleta de logs estava incompleta por atraso de ingestão; segunda coleta só leu logs existentes, sem reexecutar consulta/modelo. Relatório entregue com podcast integral e três respostas brutas completas, sem normalização. Projeto anterior 6d9d95bc-8d4c-4781-8b23-a93d647175cb / execução 809da39d-90ed-56fb-95c2-f15686c7351d estava project_proposed, sem erro atual; sua falha anterior de Proposal.document (objeto em lugar de string) já foi reparada. Não confundir com a pausa atual em verify.

Ponto de parada: somente diagnóstico completo; produção continua no SHA funcional 03535f2, revisão hefesto-v4-api-00089-gkw e digest ef4a9c518e96240bf46b95b404074e85e2927633d44fe07fa6287790aa402c35. Aguardar orientação do usuário; não corrigir autonomamente.


## Entrega integrada — contrato editorial substitutivo de 2026-10-08
[x] Contratos/estado: planejamento interno integral versionado e PE exatamente três campos, vinculação/approval, compatibilidade aditiva com JSONB existente.
[x] Engine: concepção integral primeiro, persistência antes da síntese, remoque proporcional, lacunas reais via Hermes, escrita com concepção completa e extensão, revisão intelectual proporcional.
[x] Produto: aprovação persiste e agenda produção numa única ação; idempotência, UI três campos e planejamento oculto.
[x] Testes genéricos, PostgreSQL 17, cache/ingestão/regressão, build e UI/API offline: 216 aprovados, sem skip. Commits: 684d2d98faccd18e7b75134e03184881d31aaa6a e 4dd602ab4e3e06a982da84b78cf3c813b15ba920.
[x] Imagem de produção, CI integrada, promoção e deploy/smokes. Commit: 4dd602ab4e3e06a982da84b78cf3c813b15ba920; CI 37825641321 / deploy 37826667585 SUCCESS.
[x] Registrar SHA funcional e imagem/revisão, entregar sem teste editorial pago. Recibo final abaixo.

Inspeção: implementação anterior tinha Proposal.document livre, nenhum planejamento interno, approve separado de produce, writer condicionado a suporte de todas as afirmações e verificador com substring literal. São substituídos, não autoridades funcionais. Preservar parsers/ingestão (incluindo namespace por fonte), Builder e limite dois blocos, Contexts/TTL/perfis/thinking máximo/temperatura, Hermes mecânico, autenticação/jobs e journals brutos. Persistência existente é JSONB versionado; novos campos podem ser aditivos sem criar tabelas desnecessárias. Não reescrever originais nem projetos antigos.

### Decisões e validação da entrega canônica
- Planejamento é documento editorial completo de organização livre, expressamente produzido; não thinking privado nem matriz. Versões persistidas no JSONB existente, ligadas por UUID/versão/briefing hash/manifest à síntese em exatamente três campos. Nenhuma migration SQL nova é necessária: estrutura já suporta payload aditivo.
- Concepção é uma operação intelectual integrada com corpus; síntese do PE é uma operação curta usando o planejamento já salvo, sem repetir corpus. Remoque preserva planejamento anterior; intervenção inteiramente contemplada gera aviso objetivo sem versão fictícia. Pesquisa é decidida pelo LLM apenas por lacuna específica indispensável.
- Aprovação única salva aprovação do par planejamento/PE e identidade de produção antes de dispatch. Repetição da UI não duplica job. Falha de dispatch/worker mantém estado retomável. Projetos anteriores permanecem legíveis; sem planejamento não se fabrica concepção depois do PE: um remoque cria novo par canônico antes da aprovação.
- Escritor recebe planejamento completo, PE, briefing e corpus original/contexto V8. Mantida conversão existente 150 palavras/minuto, agora transmitida como meta desde concepção e escrita. Saída contínua preflight usa limite verificado, sem truncar.
- Obra salva antes de verificação. Verificador recebe contagem real e endereços técnicos dos parágrafos completos da obra; não cria escritores por parágrafo. Conhecimento geral/inferência/interpretação legítimos são permitidos. Apenas problemas acionáveis, com localização/explicação/correção. Substring support.claim removida; referências reais continuam validadas.
- Correção substitui apenas intervalos afetados (agrupa sobreposições; ordem descendente preserva offsets). Checkpoint salva obra e progresso juntos; retomada não repete patches concluídos. Reparo de resposta recebe obra/planejamento relevante e raw preservado, sem repetir corpus.
- Preservados ingestão/parsers/namespace por fonte, corpus Builder, máximo dois blocos, Hermes, context manager explícito/TTL 3600, perfis, thinking 24576/temperatura 1.0, telemetria/cache/journals, autenticação/storage/jobs. Sem geração paga, teste editorial, A/B, billing ou exceção temática.
- Testes locais: suíte completa passou (contagem final registrada no recibo); dois testes PostgreSQL externos não executados localmente, a executar no CI real. pip check, compileall, sintaxe Node e wheel passaram. Fixtures neutras cobrem operações/estados/entradas/recuperação, não pretendem comprovar qualidade intelectual do LLM.

Proteção final: aprovação idempotente só inicia no PE proposto ou retoma a própria aprovação pausada; não pode substituir remoque ativo/pausado de uma versão anteriormente aprovada. Teste genérico conserva identidade da revisão sem dispatch.

### Checkpoint técnico — 2026-10-08 18:38 UTC
CI https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37825641321, candidato 4dd602ab4e3e06a982da84b78cf3c813b15ba920. Job contracts-build-smoke SUCCESS: 216 passed, zero skip, PostgreSQL 17 independente/locks/CAS/journals/planejamento/approval/checkpoint, cache persistido, migrations e idempotência, wheel/pip/compileall/worker, Playwright/Chromium com login/zero-fontes/remoque/versões/PE3campos/aprovação automática/iPad. Local final: 214 passed, 2 integrações PG externas skip; ambas aprovadas no CI. Job production-image-smoke em build; nenhum modelo pago chamado. Commits dos contratos/engine/UI/testes: 684d2d98faccd18e7b75134e03184881d31aaa6a; concorrência: 4dd602ab4e3e06a982da84b78cf3c813b15ba920. Próximo passo exato: smoke da imagem → promoção main → deploy com configuração/migrations/check/startup/HTTP → recibo.

### Promoção e deploy integrado em andamento
CI 37825641321 integral SUCCESS: job contracts-build-smoke e production-image-smoke concluídos; extração/document_smoke/startup/health/V8/private401 sem modelos pagos. main promovida por fast-forward de 03b66dae66e4aa94f3f18408b6b68221d6c4d08e para 4dd602ab4e3e06a982da84b78cf3c813b15ba920 depois da CI. Run Hefesto production 37826667585/job113481179395 iniciado; configuração existente Gemini 2.5 Flash/global/thinking24576/temp1.0, secrets/migrations/workers preservados. Test product contract SUCCESS; build da imagem em andamento. Não iniciar validação editorial; aguardar deploy e recibo técnico.

### Recibo final — contrato editorial canônico (2026-10-08 19:11 UTC)
- Commit documental de autoridade antes do código: 4b4e97f0095eb8b3c9136e7076f7ad2569c00d63.
- Contratos, planejamento persistente, engine, UI e testes: 684d2d98faccd18e7b75134e03184881d31aaa6a.
- Commit efetivamente implantado (inclui proteção de concorrência): 4dd602ab4e3e06a982da84b78cf3c813b15ba920.
- CI: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37825641321 SUCCESS — 216 testes técnicos sem skip, PostgreSQL 17 real, cache/ingestão, versões/approval/idempotência/recuperação, migrations/idempotência/CAS/FK, pip/compileall/wheel/worker, Playwright/Chromium e imagem/document_smoke/startup/V8/auth.
- Deploy: https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37826667585 SUCCESS, job 113481179395. Suíte de produção: 214 passed / 2 PG externos skip (ambos já aprovados na CI real).
- API: hefesto-v4-api; revisão hefesto-v4-api-00090-7dr, 100% do tráfego confirmado pelo Cloud Run. Worker: hefesto-v4-rebuild.
- Imagem pinada API/worker: us-central1-docker.pkg.dev/comunicamundo-v4/comunicamundo-v4/hefesto-v4-rebuild@sha256:468ba5664c896fe6092ec16361fe9ffa86f9fcf53711b3d0fdf75fc75fda9365.
- Migration execution: hefesto-v4-rebuild-pbnl9 SUCCESS; PostgreSQL check: hefesto-v4-rebuild-8tgjp SUCCESS; no-model startup: hefesto-v4-rebuild-m7tfx SUCCESS.
- HTTP publicado: /health ready; /v8; assets/v8.js; /v8/projects não autenticado 401. SUCCESS.
- Produto: https://hefesto-v4-api-wk5wcgnlua-uc.a.run.app/v8.
- Implementado: concepção intelectual integral e livre documentada antes da síntese; planejamento versionado/recuperável/oculto; PE fiel exatamente três campos e ligado à versão; remoque proporcional/com manifestação e histórico; lacuna específica via Hermes; aprovação única persiste/agenda produção idempotente; escritor usa plano completo e meta de extensão; obra persistida antes de revisão; verificação intelectual proporcional sem substring/citação compulsória; correções endereçadas preservam partes válidas e retomam checkpoints.
- Infraestrutura preservada: ingestão e originais, Builder/máximo dois contextos, Hermes, PostgreSQL/GCS/autenticação/jobs, cache explícito/TTL deslizante 3600/perfis, Gemini 2.5 Flash/thinking24576/temperatura1.0, telemetria e raw journals.
- Zero geração editorial paga executada nesta entrega. Nenhum teste de luto infantil, A/B ou avaliação de qualidade intelectual real.
- Projetos anteriores preservados e legíveis. PEs históricos não têm planejamento completo; não se inventa concepção retrospectivamente. Remoque em checkpoint editorial cria novo par canônico. Para o primeiro teste desta entrega, criar projeto novo; qualidade editorial pertence à validação do usuário.
- Parada: entrega técnica concluída; nenhuma alteração funcional pendente. Próximo passo: teste editorial do usuário. Commits documentais posteriores apenas registram recibos; imagem continua correspondendo ao SHA funcional acima.

## Ordem canônica — planejamento dimensionado e verificação hierárquica (2026-10-08 America/New_York)
Autoridade funcional adicional: ordem explícita do usuário desta sessão, prevalece sobre revisão circular anterior. HEADs reais main/hefesto-v8: 8fbabcc6b767bec2600182ffacd31fdb1cd47eed; produção funcional 4dd602ab4e3e06a982da84b78cf3c813b15ba920. Inspeção: Conception.planning livre com Extent nominal; nenhuma distribuição materializada ou confirmação de suficiência; produce faz verify amplo/correção/verify novamente carregando corpus. Corrigir somente essas responsabilidades e integração necessária.
[x] Dimensionamento: movimentos livres definidos pelo LLM com desenvolvimento substantivo, relações/progressão, distribuição aproximada e justificativa; avaliação intelectual antes de consolidar/publicar PE; rascunhos/checkpoints preservados. Convenção operacional existente 150 palavras/minuto; escritor executa integralmente, faixa realizada ±10%, parcial concluída dentro da escrita.
[x] Hierarquia: completude → coerência/consistência → narrativa → clareza/estilo. Diagnóstico uma vez por etapa; problemas estáveis/localizações/causa/intervenção/condição de resolução; corretor especializado multi-trechos; confirmação apenas de problemas conhecidos. Aprovações persistentes definitivas; autoridade decrescente.
[x] Recuperação: até duas correções/confirmações na estratégia inicial por diagnóstico; depois reconsideração autônoma com decisão fundamentada e execução responsável dentro da autoridade da etapa. Não há pausa editorial ou orientação humana adicional. Resultados/versões/estratégias/motivos e aprovações anteriores preservados; pausas somente técnicas reais e retomada sem repetir operações concluídas.
[x] Contexto: verificação sem corpus integral; coerência consulta somente dúvida factual concreta por endereço original; thinking máximo 24576/temperatura1.0/perfis/cache/telemetria/journals preservados.
[ ] Testes offline/integrados/regressão/PG/build/UI/imagem, CI integrada, promoção/deploy/smokes/recibo. Nenhuma geração editorial paga.
Persistência aditiva no JSONB atual. Preservar briefing, fontes/ingestão/Hermes/corpus/remoque/PE3campos/aprovação única/escrita contínua/auth/jobs/storage; nenhuma regra temática, billing ou melhoria oportunista.

### Adendo que prevalece (2026-10-08 21:04 America/New_York)
A ordem posterior corrige a própria política de tentativas: duas intervenções encerram uma estratégia, NÃO a produção. Implementar reconsideração autônoma responsável recebendo texto/diagnóstico/intervenções/motivos/plano/PE/aprovações/autoridade; resolver por nova solução, qualificação/reformulação/substituição/retirada apropriada ou rejeição fundamentada de objeção excessiva/aceitação de imperfeição estilística menor. Encerramento fundamentado, sem nova verificação ampla. Nenhuma orientação/aprovação humana adicional por dificuldade editorial. Pausas apenas técnicas reais. Planejador continua aprofundando insuficiências e reconsiderando abordagens até consolidar dimensão; nunca transfere ao escritor/verificador. Removida a proposta não commitada de pausa editorial/retomada dirigida e sua UI/API; preservar UI de aprovação única.

### Implementação integrada desta correção — 2026-10-09 UTC
- Base remota inspecionada: main/hefesto-v8 8fbabcc6b767bec2600182ffacd31fdb1cd47eed. Commit documental anterior ao código: 6b2566a18435a931a39b97928ef74a0ef11e336a. A ordem posterior de autonomia prevalece; nenhuma pausa editorial automática após duas tentativas foi entregue.
- Planejamento: movimentos livres com propósito/desenvolvimento/relações/justificativa e distribuição; avaliação intelectual específica de cada desenvolvimento, não apenas soma ou declaração de duração. Rascunhos/insuficiências persistidos; elaboração continua reconsiderando a abordagem até suficiência. PE sintetizado só depois da consolidação persistida. Soma aproximada usa a tolerância operacional ±10% da encomenda; não cria cota temática uniforme.
- Escrita: recebe planejamento integral dimensionado; finaliza execução na tolerância ±10% antes de revisão, com estratégias anteriores e versões preservadas. Verificador não completa duração nem inventa desenvolvimento ausente no planejamento.
- Hierarquia: diagnóstico especializado uma vez; correção coordenada multi-trechos; confirmação apenas dos IDs/condições conhecidos. Quatro aprovações permanentes, monotônicas; contexto proporcional sem corpus integral. Coerência recupera somente trechos originais para dúvidas concretas por endereço.
- Autonomia: duas intervenções mal sucedidas acionam operação de reconsideração com causas, estratégias e resultados; execução diferente ou decisão fundamentada encerra o diagnóstico. Pode qualificar/substituir/retirar dentro da autoridade, rejeitar exigência indevida ou aceitar imperfeição estilística menor. Disposições devem respeitar autoridade; resolução material exige edições, não mera declaração. Aprovações incluem a decisão editorial consolidada; nenhuma volta a etapas anteriores.
- Checkpoints aditivos no JSONB existente: planejamento, escrita, diagnósticos, tentativas, confirmações, reconsideração e aprovações/versionamento. Nenhuma migration nova. Jornals/raw/idempotência recuperam operações concluídas. API pública omite planejamentos, tentativas e manuscritos intermediários; obra entregue somente em text_ready depois das quatro aprovações.
- Preservados ingestão/parsers, múltiplas fontes, Hermes, corpus máximo dois blocos, remoque/PE3campos/aprovação única, explicit cache/TTL3600, provider Gemini2.5Flash/thinking24576/temp1.0, telemetria/GCS/Neon/Cloud Run/UI editorial. Sem refatoração paralela ou regra temática.
- Testes locais: 226 passed / 2 integrações PostgreSQL externas skip; executar ambos na CI real. pip check/compileall/node syntax/wheel/worker --help/diff check aprovados. Fixtures neutras comprovam planejamento insuficiente aprofundado antes do PE, escrita concluída antes da qualidade, quatro etapas sem retorno, confirmação restrita, limite de estratégia, aceitação estilística responsável, qualificação factual grave, rejeição fundamentada de objeção excessiva, recuperação técnica sem repetir correções/etapas, consulta documental localizada e cache preservado.
- Próximo passo exato: commit funcional → CI real completo → promoção main → deploy existente → smoke e recibo com SHA/revisão/digest. Nenhuma geração editorial paga executada.

### Checkpoint de promoção — 2026-10-09 01:25 UTC
- Commit funcional: 1c8c38f8d8be7340d74e15747b17ef13aa8575b4; tree 9122bfd769a2065d0412725233cce7c75d5a9dba. Checkout local sincronizado e limpo, todos os blobs verificados contra o GitHub.
- CI https://github.com/valtersclavi/comunicamundo-v4/actions/runs/37869038198 SUCCESS: contracts-build-smoke 113622561237 (228 passed, zero skip, PostgreSQL 17, todas migrations/idempotência/FK/CAS/raw, browser login/zero-fontes/remoque/aprovação única/PE3campos/iPad); production-image-smoke 113622992508 SUCCESS (imagem, extração, health ready, V8 e API privada 401), nenhum modelo chamado.
- main confirmada sem alterações externas e promovida de 8fbabcc6b767bec2600182ffacd31fdb1cd47eed para 1c8c38f8d8be7340d74e15747b17ef13aa8575b4 por fast-forward com lease. Workflow de produção 37869705877 em execução; não declarar novo SHA implantado antes de conclusão.
- Próximo passo exato: concluir produção, conferir recibo de revisão/digest/migrations/check/startup/HTTP e atualizar CURRENT STATUS. Zero chamadas editoriais pagas.
