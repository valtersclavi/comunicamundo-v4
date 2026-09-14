# Baseline canônica do Hefesto V4

Status: congelada para evolução por bordas.

## Identidade

- Núcleo editorial congelado: `7eb1cd8273f805ba682623857d360618a7516687`.
- Tag canônica: `hefesto-v4-core-v1`.
- Validação de referência: GitHub Actions `34631756892`, concluída com testes, imagem, migração, deploy do Job, deploy da API e smokes verdes.
- O histórico completo da branch `main` é preservado; não há squash nem reescrita.

## Provas funcionais preservadas

| Teste | Execução | Quarta fonte | Estado exigido pela limpeza |
|---|---|---|---|
| A | `f88c65b4-414b-56d1-9820-19489159922c` | National Academies — Highlights | `text_ready`, com texto final |
| B | `d9a77477-e5aa-5dbc-ad1a-e8434a84e98e` | National Academies — Full Report | `text_ready`, com texto final |

Os registros completos, fontes, artefatos, etapas e métricas dessas duas execuções são a evidência operacional canônica. O procedimento de limpeza aborta antes de qualquer alteração se um dos IDs não existir, não estiver concluído ou não tiver texto final.

## Bloco congelado

O bloco congelado começa depois que fontes já chegaram normalizadas e termina no texto final semanticamente preservado. Ele contém:

1. estruturação em parágrafos e frases;
2. seleção por flags orientada pelo briefing;
3. compreensão e consolidação editorial sob orçamento real de tokens;
4. Eixo Narrativo e Projeto Editorial concebidos juntos;
5. aprovação humana única;
6. redação, revisão editorial e correção localizada;
7. estilização por produto;
8. verificação substantiva entre o conteúdo editorialmente fechado e a versão estilizada.

A duração é orientação editorial, não uma faixa mecânica de palavras. O verificador final lê as duas versões e decide compatibilidade substantiva; não faz auditoria de coordenadas entre fontes e blocos.

## Fronteiras para a próxima fase

O núcleo só deve mudar diante de defeito reproduzível no contrato congelado. A evolução planejada acontece em três adaptadores independentes:

- entrada: busca e seleção de fontes;
- digestão: mais formatos e normalização mais rica antes do núcleo;
- saída: renderer/formatador específico para cada produto, depois do texto final.

## Limpeza operacional

O comando `clean-operational-history` é uma ferramenta administrativa isolada. Sem `--execute`, ele apenas inventaria os alvos. Com execução explícita, preserva:

- os dois IDs acima e todas as suas tabelas dependentes;
- os prefixes de execução e de projeto correspondentes;
- os objetos de upload referenciados pelas submissões de A e B;
- esquema, credencial do produto e sessões ainda válidas.

Ele remove os demais registros editoriais, uploads não referenciados, objetos sob `hefesto-product/` e sessões expiradas. Um recibo único fica em `hefesto-product/maintenance/freeze-cleanup.json`.
