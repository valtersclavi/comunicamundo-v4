# ComunicAmundo V4

Base limpa do ComunicAmundo V4. O V3 permanece apenas como referência histórica
e não é a base de código desta versão.

## Hefesto V4

O fluxo editorial começa com fontes normalizadas e a intenção do usuário. Antes
de escolher assuntos, o Editor-Chefe propõe um **Eixo Narrativo** de uma ou duas
frases: a leitura editorial que governará a peça. O usuário aprova ou corrige o
eixo. Só então o Hefesto constrói o **Projeto Editorial**, uma lista compacta de
assuntos a desenvolver, que também é aprovada antes da produção.

Fluxo canônico:

```text
briefing + fontes
→ grafo
→ Eixo Narrativo proposto
→ aprovação/correção do Eixo
→ Projeto Editorial proposto
→ aprovação/correção do Projeto
→ pesquisa por assunto
→ arquitetura narrativa interna
→ redação
→ crítica/edição
→ artigo final
```

O Eixo Narrativo é superior às etapas posteriores. O Projeto Editorial não é
outline e não expõe perguntas internas de pesquisa: cada assunto contém apenas
título, desenvolvimento esperado e orçamento interno de duração. A profundidade
é inferida da intenção. Um guia pode exigir práxis; uma análise pode exigir
causalidade; uma comparação, diferenças; um relatório decisório, trade-offs.
Nenhuma dessas formas é imposta universalmente.

## Chassi

- LlamaIndex para `PropertyGraphIndex`, recuperação e workflows editoriais;
- Google GenAI sobre Vertex AI, usando ADC;
- Firestore para estado compacto e Cloud Storage para artefatos;
- nenhuma dependência de Microsoft GraphRAG ou Amplifier;
- nenhum requisito de LlamaCloud ou LlamaParse.

Flash continua responsável por grafo, pesquisa e crítica. Pro é usado para Eixo
Narrativo, Projeto Editorial, arquitetura narrativa, redação e edição.

## Persistência e aprovações

O grafo é salvo uma única vez no Cloud Storage como artefato imutável, com
manifesto de caminho, tamanho e SHA-256. O Eixo Narrativo fica no Firestore em
`hefesto_v4_narrative_axes`. Depois da aprovação do eixo, o Projeto Editorial é
criado reutilizando exatamente o mesmo artefato de grafo e fica em
`hefesto_v4_editorial_projects`. O Markdown final fica no Storage; o Firestore
guarda URI e metadados.

As duas aprovações são duráveis e independentes. Revisões anteriores à aprovação
são versionadas. Um Eixo aprovado não pode ser revisado; um Projeto aprovado não
pode ser revisado. Produção exige Projeto Editorial aprovado.

## Worker

```bash
comunicamundo-v4-worker plan --request gs://BUCKET/requests/PROJECT.json
comunicamundo-v4-worker revise-axis --narrative-axis-id AXIS_UUID --feedback-b64 BASE64URL
comunicamundo-v4-worker approve-axis --narrative-axis-id AXIS_UUID
comunicamundo-v4-worker revise-project --editorial-project-id PROJECT_UUID --feedback-b64 BASE64URL
comunicamundo-v4-worker approve --editorial-project-id PROJECT_UUID
comunicamundo-v4-worker produce --editorial-project-id PROJECT_UUID
```

No iPad, essas operações saem do GitHub Actions `operate` em `main`, autenticado
por WIF. A interface do workflow recebe feedback em linguagem natural e faz a
codificação necessária antes do dispatch. `inspect` mostra evidências não
sensíveis, incluindo o Eixo Narrativo e os assuntos compactos quando presentes.

## Desenvolvimento

O Cloud Agent lê `.cursor/environment.json` e instala:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest
python3 -m compileall -q src tests
python3 -m comunicamundo_v4.hefesto.worker --help
```

Push em `main` testa, constrói e publica o Cloud Run Job `hefesto-v4` no projeto
GCP de prova `comunicamundo-v4`, região `us-central1`, usando Workload Identity
Federation. Nenhuma chave permanente do Google deve ser colocada no repositório
ou no Cursor.
