# HEFESTO V4 — EXECUTAR SOMENTE O TESTE MULTIFONTE A

Execute autonomamente o Teste A completo no repositório privado `valtersclavi/comunicamundo-v4`, branch canônica `main`, usando o ambiente GCP já configurado para o projeto. Este ZIP contém o prompt e os cinco PDFs. Extraia o pacote em uma pasta temporária fora do repositório. Não faça downloads externos.

## Limites absolutos

- Não altere código, prompts, contratos ou lógica editorial do Hefesto.
- Não crie, modifique, teste ou conserte workflows.
- Não crie infraestrutura.
- Não abra PR, não crie commit e não faça push.
- Não use nem adapte o harness descartável do PR #52.
- Não execute `.github/workflows/one-shot-social-media-multisource-ab.yml` nem use commits-gatilho `[social-media-multisource-a]` ou `[social-media-multisource-b]`.
- Use somente o pipeline e os entrypoints canônicos existentes no `main` e o GCP já configurado.
- Não inicie o Teste B.
- Zero concorrência: uma etapa termina antes da seguinte começar.

## Fontes do Teste A

Use exatamente estes quatro arquivos, nesta ordem:

1. `A01_Fassi_2025.pdf`
   - Título: `Social media use in adolescents with and without mental health conditions`
   - Idioma: `en`
   - Páginas: 21
   - SHA-256: `507dc349e21ce50434fe0e56080b163415f08c937a81f5e5dfd0ceae1f14d3d3`

2. `A02_Souza_Cunha_2019.pdf`
   - Título: `Impactos do uso das redes sociais virtuais na saúde mental dos adolescentes: uma revisão sistemática da literatura`
   - Idioma: `pt-BR`
   - Páginas: 14
   - SHA-256: `02b16302c444ecdda13cfc1af8f8eb8ea62ec1d5fc5eaa938c3e45da5124e025`

3. `A03_Orben_2022.pdf`
   - Título: `Windows of developmental sensitivity to social media`
   - Idioma: `en`
   - Páginas: 10
   - SHA-256: `cb81238b66b8c5218033c577c141de1b17f6a37366dc48b896d58934599fe86a`

4. `A04_National_Academies_Highlights.pdf`
   - Título: `Social Media and Adolescent Health — Highlights`
   - Idioma: `en`
   - Páginas: 5
   - SHA-256: `356d2f8e505d15b109c9aa87d6708db06940a8ba38e1c8d5ea1697bb78111866`

O Teste A deve totalizar exatamente 50 páginas.

`B04_National_Academies_Full_Report.pdf`, SHA-256 `e25a3453909e8801e6e96fdc1a5309fd063d3b66c972f02dc078790cd99929a0`, está reservado ao Teste B. Não o inclua na requisição, digestão, contexto, grafo ou qualquer chamada do Teste A.

Valide hashes e páginas antes da primeira chamada paga. Se houver ausência ou divergência, pare sem baixar substituto.

## Briefing

Crie uma requisição inteiramente nova com:

- `theme`: `O impacto das redes sociais na saúde mental dos adolescentes`
- `approach`: `Examinar de forma equilibrada, sem pressupor uma conclusão, como diferentes formas e contextos de uso das redes sociais se relacionam com a saúde mental dos adolescentes, distinguindo correlação de causalidade e considerando heterogeneidade, benefícios, riscos, divergências e limitações das evidências.`
- `duration_minutes`: `15`
- `output_language`: `pt-BR`
- `audience`: `Leitores não especialistas interessados em adolescência, saúde mental e vida digital.`
- `product`: `article`

Não acrescente tese ou conclusão antecipada ao briefing.

## Freshness obrigatória

1. Registre o HEAD atual de `main`, sem modificar o repositório.
2. Registre projeto GCP, região, job ou operador canônico, imagem implantada e digest da imagem.
3. Confirme que não há outra execução desta calibração em andamento.
4. Gere um `project_id` UUID novo e quatro `source_id` UUIDs novos.
5. Use um caminho de requisição GCS novo e exclusivo.
6. Faça nova extração e normalização diretamente dos quatro PDFs do A.
7. Use a normalização operacional canônica do projeto.
8. Não use textos previamente extraídos.
9. Não reutilize digestão, chunks, embeddings, cache editorial, linhagem, grafo, compreensão, Eixo, Projeto Editorial ou produção anteriores.
10. Registre por fonte: caminho resolvido, título, idioma, páginas, bytes, SHA-256 do PDF, caracteres do texto extraído, SHA-256 do texto normalizado e quantidade de chunks.

## Telemetria externa, sem alterar o Hefesto

Antes da primeira chamada de modelo, prepare coleta externa das respostas reais do provedor ou dos registros equivalentes. Registre por chamada, sempre que exposto:

- timestamp e etapa;
- modelo efetivo;
- prompt/input tokens;
- cached tokens;
- output/candidate tokens;
- thinking/reasoning tokens;
- tokens de embeddings;
- custo USD.

Não use `max_tokens` como consumo e não estime tokens por caracteres. Calcule o custo a partir do consumo real informado pelo provedor e do preço oficial aplicável ao modelo, região e faixa de contexto na data da execução. Preserve preços e fórmulas. Se disponível sem atraso impeditivo, reconcilie separadamente com Cloud Billing.

Se alguma chamada não expuser uso, marque `usage_unavailable`; não invente números nem declare um total incompleto como exato. Instrumentação temporária deve ficar fora do repositório, apenas observar as chamadas e invocar os entrypoints canônicos sem alterar entradas, prompts, modelos, decisões ou saídas. Não copie a instrumentação do PR #52.

Registre timestamps UTC e tempo monotônico do total e de cada etapa: digestão, chunks, extração de grafo, indexação/embeddings, compreensão, Eixo, Projeto Editorial, aprovação, produção e persistência final.

## Execução estritamente sequencial

1. Nova digestão e normalização dos quatro PDFs do A.
2. Novo PlanningRequest.
3. `plan` até `source_chunks_ready`.
4. `extract-graph` até `graph_extractions_ready`.
5. `index-graph` até `graph_ready`.
6. `propose-axis` até persistir compreensão editorial, revisão de sustentação e Eixo Narrativo.
7. Colete o Eixo, mas não o revise e não envie feedback.
8. Aprove diretamente o Eixo com `approve-axis`.
9. Colete o Projeto Editorial proposto, todas as Dicas do Hefesto e oportunidades de enriquecimento.
10. Não chame `incorporate-hefesto-tips`; registre `accepted_tips: []`.
11. Aprove diretamente o Projeto Editorial com `approve-project`.
12. Produza o artigo completo com `produce`.
13. Espere a persistência terminal do artigo.
14. Pare. Não prepare nem inicie o Teste B.

A presença de Dicas no objeto não significa aceitação. Nenhuma Dica pode ampliar o Projeto antes da produção.

## Coleta obrigatória

Colete os objetos completos, e não apenas URIs:

- manifesto dos PDFs;
- textos extraídos e normalizados;
- PlanningRequest e PlanningRun;
- SourceLineage;
- manifesto e objetos de chunks;
- manifesto e objetos das extrações de grafo;
- manifesto, artefato e estatísticas disponíveis do grafo indexado;
- compreensão editorial;
- revisão de sustentação do Eixo;
- Eixo proposto e aprovado;
- Projeto Editorial proposto e aprovado;
- Dicas geradas, oportunidades de enriquecimento e `accepted_tips: []`;
- artigo, corpo Markdown integral, título, palavras, quality score e revision rounds;
- registros Firestore, URIs GCS, execuções Cloud Run e logs relevantes;
- telemetria por chamada, tempos e custos.

Preserve os artefatos nos locais canônicos produzidos pelo pipeline. Monte também um manifesto consolidado do Teste A em pasta exclusiva da calibração usando apenas armazenamento já existente. Não crie bucket, coleção ou serviço e não grave resultados no repositório.

## Saída

Em caso de sucesso, entregue:

`HEFESTO_MULTIFONTE_TEST_A: SUCCESS`

Inclua HEAD, imagem/digest, ambiente, todos os IDs, manifesto das fontes, caracteres e chunks, tempos por etapa, tabela por chamada, totais por etapa e modelo, custo USD total, todos os URIs, compreensão integral, Eixo integral, Projeto integral, Dicas com `accepted_tips: []`, artigo integral, score, revisões e prova de freshness, zero concorrência e Teste B não iniciado.

Antes de declarar sucesso, prove também que exatamente quatro fontes e 50 páginas entraram no A, que o relatório completo foi excluído, que tudo nasceu do zero, que nenhuma Dica foi aceita e que nenhum arquivo do repositório foi alterado.

Se uma etapa falhar, não corrija o Hefesto, não altere infraestrutura, não reinicie silenciosamente e não inicie o B. Preserve custo e artefatos parciais e entregue:

`HEFESTO_MULTIFONTE_TEST_A: FAILURE`

Informe etapa, erro, execução, persistidos existentes, chamadas realizadas, tokens, custo acumulado e diagnóstico somente de leitura.
