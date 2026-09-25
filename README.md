# Hefesto

Hefesto é um produto de planejamento editorial orientado pelas fontes e pela intenção do usuário.

O serviço web com login é a única entrada de uso editorial. Nele, o usuário:

1. envia documentos, planilhas, apresentações, e-books, texto, imagens, áudio, vídeo e/ou informa URLs públicas;
   alternativamente, escolhe **Procure as fontes para mim** e começa com zero fontes;
2. define tema, abordagem, público, produto, idioma e duração;
3. acompanha ingestão, triagem, compreensão e uso de modelo;
4. recebe o Projeto Editorial com o Eixo Narrativo em destaque;
5. aprova o conjunto em uma única decisão editorial;
6. recebe o texto final produzido numa esteira contínua de redação de conteúdo, revisão editorial, correção pontual, estilo do produto e verificação de preservação.

No fluxo de zero fontes, o usuário aprova primeiro um Projeto Editorial
provisório (ou pede alterações pelo chat). O editor então faz Search interno,
seleciona as fontes pelo eixo editorial, executa a cascata de Acquire, redigere o
material adquirido e apresenta um segundo Projeto Editorial para aprovação.

O processamento é assíncrono e persistido. Uma atualização da página não perde a execução. Cada usuário só acessa seus próprios uploads e planejamentos. Falhas mostram a etapa e o erro real, e trabalhos retomáveis podem ser continuados sem repetir trabalho concluído.

## Arquitetura

- Cloud Run Service: tela de login, interface web e API protegida por sessão.
- Cloud Run Job: processamento editorial de longa duração.
- PostgreSQL: estado canônico, propriedade, estrutura das fontes, artefatos, etapas e consumo.
- Cloud Storage: fontes originais, solicitações, representações canônicas, ativos e recibos imutáveis.
- Vertex AI: triagem por flags, compreensão editorial, redação, revisão, estilo e verificação final limitadas por tokens.
- You e OpenAlex: descoberta interna de candidatos a fonte.
- You Contents, Jina Reader e Browser Use: rotas sucessivas de aquisição pública.

As chaves `YOU_API_KEY`, `OPENALEX_API_KEY`, `JINA_API_KEY` e
`BROWSER_USE_API_KEY` são injetadas apenas no Cloud Run Job pelos secrets
`you-api-key`, `openalex-api-key`, `jina-api-key` e `browser-use-api-key`.

O deploy ocorre em `main`. O workflow testa o contrato completo, constrói uma única imagem, aplica migrações, implanta Job e aplicação web, configura autenticação e publica o recibo na issue canônica.

## Contrato do fluxo editorial

As regras de zero fontes, remoque permanente, sugestões do primeiro Projeto Editorial, enriquecimento pontual com fontes e integração à matriz estão em [docs/hefesto-matriz-fluxo-editorial.md](docs/hefesto-matriz-fluxo-editorial.md). O documento distingue o contrato de produto das partes ainda não implementadas e mantém a ingestão congelada.

## Desenvolvimento

```bash
python -m pip install -e '.[dev]'
pytest
uvicorn comunicamundo_v4.hefesto.api:app --reload
```

Em produção, somente a página inicial e o endpoint de login são públicos. Uploads, planejamentos, aprovações e histórico exigem uma sessão válida armazenada no PostgreSQL. A senha não é armazenada em texto aberto, e dez tentativas inválidas bloqueiam novas tentativas por quinze minutos.

## Baseline congelada

O núcleo editorial validado está congelado em `7eb1cd8273f805ba682623857d360618a7516687` e identificado pela tag `hefesto-v4-core-v1`. A definição do bloco, as duas provas funcionais preservadas e as fronteiras de evolução estão em [docs/hefesto-v4-baseline.md](docs/hefesto-v4-baseline.md).
