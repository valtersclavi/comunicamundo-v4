# Hefesto

Hefesto é um produto de planejamento editorial orientado pelas fontes e pela intenção do usuário.

O serviço web com login é a única entrada de uso editorial. Nele, o usuário:

1. envia PDFs e/ou informa URLs públicas;
2. define tema, abordagem, público, produto, idioma e duração;
3. acompanha ingestão, estruturação, digestão e uso de modelo;
4. lê e aprova o Eixo Narrativo;
5. recebe o Projeto Editorial.

O processamento é assíncrono e persistido. Uma atualização da página não perde a execução. Cada usuário só acessa seus próprios uploads e planejamentos. Falhas mostram a etapa e o erro real, e trabalhos retomáveis podem ser continuados sem repetir trabalho concluído.

## Arquitetura

- Cloud Run Service: tela de login, interface web e API protegida por sessão.
- Cloud Run Job: processamento editorial de longa duração.
- PostgreSQL: estado canônico, propriedade, estrutura das fontes, artefatos, etapas e consumo.
- Cloud Storage: PDFs, solicitações e recibos imutáveis.
- Vertex AI: digestão editorial, entendimento, Eixo Narrativo e Projeto Editorial.

O deploy ocorre em `main`. O workflow testa o contrato completo, constrói uma única imagem, aplica migrações, implanta Job e aplicação web, configura autenticação e publica o recibo na issue canônica.

## Desenvolvimento

```bash
python -m pip install -e '.[dev]'
pytest
uvicorn comunicamundo_v4.hefesto.api:app --reload
```

Em produção, somente a página inicial e o endpoint de login são públicos. Uploads, planejamentos, aprovações e histórico exigem uma sessão válida armazenada no PostgreSQL. A senha não é armazenada em texto aberto, e dez tentativas inválidas bloqueiam novas tentativas por quinze minutos.
