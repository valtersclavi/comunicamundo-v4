# Incidente: repetição na pesquisa estruturada

Em 22 de agosto de 2026, a primeira produção real falhou durante a pesquisa de
um assunto. A aprovação havia concluído normalmente e o grafo restaurado era o
mesmo que produzira o Projeto Editorial.

## Evidência observada

- modelo: `gemini-2.5-flash` no Vertex AI;
- chamada: `GoogleGenAI.astructured_predict(SubjectResearchProposal, ...)`;
- resposta: JSON interrompido dentro de uma string, na coluna 83.578;
- cauda: repetição contínua de marcadores como `[3, 4]`;
- erro final: `json_invalid`, com EOF antes do fechamento da string.

O `astructured_predict` da versão fixada de
`llama-index-llms-google-genai` converte a chamada em
`aio.models.generate_content` com `response_mime_type=application/json` e
`response_schema` igual ao modelo Pydantic. Portanto, a falha ocorreu no modo
de saída estruturada do Gemini, antes da validação local.

Esse formato coincide com o problema público de repetição de tokens em saída
estruturada do Gemini: a geração entra em loop, alcança o teto de tokens e o
JSON retorna truncado. O volume da saída e a repetição descartam um limite de
saída pequeno como causa; aumentar o limite apenas prolongaria o loop.

O wrapper descartou os metadados da resposta antes de o Pydantic lançar o
erro, portanto o log não preservou `finish_reason` nem a contagem exata de
tokens. A conclusão não depende de presumir esse campo: a resposta devolvida
pelo servidor já continha a repetição e terminava no meio da string, e o mesmo
defeito está documentado para `gemini-2.5-flash` com `responseSchema`.

## Decisão

A pesquisa de assunto é matéria editorial interna em prosa. Nenhuma decisão de
código depende de seus campos. Ela passa a usar resposta Markdown comum, sem
`response_schema`, e fica guardada integralmente em `synthesis`. O teto de
4.096 tokens é um circuito de custo coerente com o briefing solicitado de 400
a 700 palavras, não uma tentativa de acomodar a resposta defeituosa.

Saída estruturada permanece nas etapas em que o programa realmente precisa de
campos tipados: Projeto Editorial, plano narrativo e crítica editorial.

## Referências

- [Gemini 2.5 Flash repete tokens até o teto em saída estruturada](https://discuss.ai.google.dev/t/gemini-2-5-flash-repeats-tokens-until-max-tokens-reached-in-structured-output/107176)
- [Relato no SDK `python-genai`: loop, teto e JSON corrompido](https://github.com/googleapis/python-genai/issues/1984)
- [API Gemini: `finishReason` e metadados de uso](https://ai.google.dev/api/generate-content)
