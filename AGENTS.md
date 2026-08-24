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
`GOOGLE_CLOUD_LOCATION` e assume `us-central1` quando omitida.

Do iPad, dispare `.github/workflows/operate.yml` em `main` (Actions → operate):

- `plan`: somente `request_uri` `gs://comunicamundo-v4-hefesto-30524898837/...`; cria o grafo e propõe o Eixo Narrativo;
- `revise-axis`: `narrative_axis_id` + `feedback`;
- `approve-axis`: somente `narrative_axis_id`; aprova o eixo e cria o Projeto Editorial;
- `revise-project`: `editorial_project_id` + `feedback`;
- `approve`: somente `editorial_project_id`;
- `produce`: somente `editorial_project_id`;
- `inspect`: somente `execution_name` no formato `hefesto-v4-...`.

As combinações inválidas falham fechadas. As operações de worker disparam o Job
`hefesto-v4` de forma assíncrona e gravam o nome da execução no Step Summary.
`inspect` descreve uma execução sem esperar. Feedback textual é codificado antes
de virar argumento do Job e não deve ser repetido no Step Summary.

Não execute operações com modelo a menos que a tarefa peça uma prova delimitada.
Uma prova de startup deve ser única, observável e sem modelo.

## Entrega

Ao terminar: resultado, evidências, custos observáveis, mudanças e riscos
remanescentes. Declare bloqueio somente nas proibições acima ou quando a falha
não tiver correção técnica simples e comprovável.
