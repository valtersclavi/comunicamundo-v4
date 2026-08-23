# ComunicAmundo V4 — Cloud Agent

Este repositório é a única base de trabalho. Não inspecionar, copiar nem alterar
`comunicamundo-v3` ou qualquer outro repositório.

## Ambiente

O Cloud Agent instala o pacote com `.cursor/environment.json`:

```bash
python3 -m pip install -e '.[dev]'
```

Use o módulo do Python, não o PATH de scripts:

```bash
python3 -m pytest
python3 -m compileall -q src tests
python3 -m comunicamundo_v4.hefesto.worker --help
```

Não há servidor de desenvolvimento. Não há lint configurado. Não crie venv
neste pod: a imagem padrão pode não ter `ensurepip`.

## Mandato

A tarefa do usuário já autoriza o ciclo completo neste repositório: editar,
testar, criar branch, commit, push, PR, merge em `main` quando as verificações
estiverem verdes e acompanhar o GitHub Actions.

O push em `main` dispara o workflow que testa, constrói a imagem e publica o
Job `hefesto-v4` no projeto GCP de prova `comunicamundo-v4`. Esse é o único
destino GCP autorizado.

## Proibido

- tocar em outro repositório ou outro projeto GCP;
- alterar IAM, billing, WIF, service accounts, secrets ou credenciais;
- reutilizar secrets `STAGING_*` herdados do V3;
- expor valores de secrets ou colocar chaves permanentes do Google no Cursor;
- apagar dados persistidos ou excluir infraestrutura;
- esconder falha de prova real com mock, fake ou fallback;
- repetir execução paga idêntica depois de falha terminal sem mudança material;
- tomar decisão editorial ou de produto que o usuário não definiu.

## GCP e credenciais

Build, deploy e operação GCP passam pelo GitHub Actions com Workload Identity
Federation na branch `main`. Não configure ADC nem `GOOGLE_APPLICATION_CREDENTIALS`
neste ambiente.

O worker exige `GOOGLE_CLOUD_PROJECT` e `HEFESTO_GCS_BUCKET`. A região vem de
`GOOGLE_CLOUD_LOCATION` e assume `us-central1` quando omitida. Não execute
`plan`, `approve` ou `produce` reais a menos que a tarefa peça uma prova
delimitada. Uma prova real deve ser única, observável e sem modelo se o
objetivo for só startup.

## Entrega

Ao terminar: resultado, evidências, custos observáveis, mudanças e riscos
remanescentes. Declare bloqueio somente nas proibições acima ou quando a falha
não tiver correção técnica simples e comprovável.
