# ADR 0001 — Fundação do Hefesto V4

Status: aprovado em 2026-08-22.

## Decisão

O Hefesto V4 será construído como base limpa sobre LlamaIndex. Microsoft
GraphRAG e Microsoft Amplifier não fazem parte do caminho principal.

O objetivo editorial prioritário é a qualidade do texto final. As fontes
orientam a pesquisa e a escrita, mas o produto não manterá um ledger de
afirmações nem tentará provar documentalmente cada frase.

## Fluxo do produto

1. Receber fontes normalizadas e o tripé tema, abordagem e duração.
2. Construir o grafo do projeto.
3. Consultar o grafo segundo a intenção do usuário.
4. Produzir um Projeto Editorial estruturado como lista de assuntos.
5. Encerrar a execução e aguardar aprovação humana persistida.
6. Pesquisar separadamente cada assunto aprovado.
7. Redigir, criticar e editar o texto.
8. Entregar o produto editorial final.

O outline é interno. Não é o objeto submetido à aprovação.

## Hermes

O Hermes não fica embutido no Hefesto. O encaixe é uma porta opcional de
descoberta externa usada pelo pesquisador editorial. O caminho inicial utiliza
somente as fontes do projeto. Quando habilitado, o Hermes pode preencher
lacunas, aprofundar assuntos e encontrar contrapontos sem alterar o contrato do
workflow.

## Regra de reaproveitamento do V3

Um componente do V3 só pode entrar quando as quatro condições forem verdadeiras:

1. seu comportamento já foi comprovado;
2. seu contrato coincide com o V4;
3. não carrega GraphRAG, Amplifier ou compatibilidade transitiva;
4. adaptar e testar custa claramente menos do que reimplementar.

Na dúvida, o componente não é copiado.

API, Cloud Tasks, dispatcher privado, Cloud Run Jobs, Firestore e Storage são
candidatos à reutilização conceitual. Nenhum deles é transplantado por padrão.

