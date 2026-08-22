# ComunicAmundo V4

Base limpa do ComunicAmundo V4.

O V3 permanece como referência histórica e operacional. Ele não é a base de
código desta versão. Componentes só entram no V4 quando atendem diretamente ao
contrato atual e quando reaproveitá-los custa menos do que reimplementá-los.

## Primeira entrega

O primeiro corte do Hefesto V4 transforma fontes normalizadas e a intenção do
usuário em um **Projeto Editorial** estruturado e aprovável.

Entradas obrigatórias:

- tema;
- abordagem;
- duração;
- idioma de saída;
- fontes normalizadas.

O Projeto Editorial é uma lista de assuntos. Outline, capítulos e seções são
decisões internas posteriores do redator.

## Chassi

- LlamaIndex para `PropertyGraphIndex`, recuperação e workflows editoriais;
- Google GenAI sobre Vertex AI, usando ADC;
- Firestore para o estado compacto do workflow e Cloud Storage para artefatos;
- nenhuma dependência de Microsoft GraphRAG;
- nenhuma dependência de Microsoft Amplifier;
- nenhum requisito de LlamaCloud ou LlamaParse.

O roteamento de modelos é configurável. A base usa Flash para construção do
grafo, pesquisa e crítica; Pro para Projeto Editorial, arquitetura narrativa,
redação e edição.

## Persistência

O grafo é salvo no Cloud Storage como um artefato imutável. Um manifesto escrito
por último registra caminho, tamanho e SHA-256 de cada arquivo. O Projeto
Editorial e seu estado de aprovação ficam no Firestore. O Markdown final também
fica no Storage; o Firestore guarda somente sua URI e metadados.

Projeto GCP, bucket, prefixos e coleções são configuração de implantação. O
código não contém nomes herdados do V3 e usa Application Default Credentials.

## Hermes

O fluxo inicial trabalha apenas com as fontes já selecionadas. Uma porta
opcional de descoberta aceita fontes normalizadas do Hermes sem permitir que ele
se torne redator, finalizador ou dependência interna do Hefesto.

## Desenvolvimento local

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Worker

A mesma imagem executa um único lado da fronteira de aprovação por vez:

```bash
comunicamundo-v4-worker plan --request gs://BUCKET/requests/PROJECT.json
comunicamundo-v4-worker produce --editorial-project-id EDITORIAL_PROJECT_UUID
```

O worker exige `GOOGLE_CLOUD_PROJECT` e `HEFESTO_GCS_BUCKET`. A região vem de
`GOOGLE_CLOUD_LOCATION` e assume `us-central1` quando omitida. O comando
`plan` termina depois de persistir a proposta; somente uma nova execução de
`produce`, após aprovação, restaura o grafo e escreve o artigo.

## Ambiente de prova

Enquanto o produto estiver em construção, o `main` é publicado diretamente no
projeto GCP único `comunicamundo-v4`. A imagem fica no Artifact Registry
`comunicamundo-v4`, o worker é o Cloud Run Job `hefesto-v4` e os artefatos usam
o bucket exclusivo `comunicamundo-v4-hefesto-30524898837`.

O GitHub autentica por Workload Identity Federation restrita ao repositório
`valtersclavi/comunicamundo-v4` e à branch `main`; nenhuma chave permanente é
armazenada no repositório.
