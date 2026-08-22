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
- nenhuma dependência de Microsoft GraphRAG;
- nenhuma dependência de Microsoft Amplifier;
- nenhum requisito de LlamaCloud ou LlamaParse.

O roteamento de modelos é configurável. A base usa Flash para construção do
grafo, pesquisa e crítica; Pro para Projeto Editorial, arquitetura narrativa,
redação e edição.

## Desenvolvimento local

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
