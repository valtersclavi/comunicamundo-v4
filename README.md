# ComunicAmundo — Hefesto V8

Sistema de produção editorial: briefing e fontes → rascunho intelectual →
Projeto Editorial → aprovação ou remoque → escrita e qualidade → texto final.
Sem fontes fornecidas, Hermes busca e adquire o corpus antes do planejamento.

O contrato editorial único está em
[HEFESTO_V8_EDITORIAL_CONTRACT.md](docs/HEFESTO_V8_EDITORIAL_CONTRACT.md).
As regras de desenvolvimento estão em [AGENTS.md](AGENTS.md), e configuração,
persistência e implantação em [HEFESTO_V8_OPERATIONS.md](docs/HEFESTO_V8_OPERATIONS.md).

## Desenvolvimento

Python 3.12. Instale `pip install -e '.[dev]'` e execute `pytest`.
A interface principal está em `/`, também disponível em `/v8`.
API: `uvicorn comunicamundo_v8.hefesto.api:app`.
Worker: `comunicamundo-v8-worker v8-run --execution-run-id=UUID`.
Migração: `comunicamundo-v8-worker migrate-postgres`.

Código, contratos, testes e documentação pertencem exclusivamente ao V8.
Identificadores remotos de GCP e GitHub continuam apontando para os recursos
existentes, independentemente da versão contida em seus nomes.
