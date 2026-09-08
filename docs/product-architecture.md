# Arquitetura do produto Hefesto

Status: canônica.

## Percurso do usuário

`login da equipe → briefing + fontes → acompanhamento → Eixo Narrativo → aprovação humana → Projeto Editorial`

A aplicação web e a API são entregues pelo mesmo Cloud Run Service. Isso mantém autenticação, interface e operações no mesmo domínio. O navegador envia PDFs em partes para a API; a API controla uma sessão retomável no Cloud Storage, sem limite prático de uma requisição HTTP única. URLs públicas também são aceitas.

Cada execução e upload pertence à identidade Google autenticada. O PostgreSQL guarda a relação de propriedade e é a fonte canônica para estado, estrutura editorial, métricas e artefatos. O Cloud Storage preserva os arquivos e recibos imutáveis. O Cloud Run Job executa o trabalho demorado sem manter a solicitação do navegador aberta.

## Processamento editorial

1. A ingestão valida cada PDF, preserva o original, extrai texto por layout e normaliza parágrafos completos.
2. Parágrafos e frases são persistidos relacionalmente antes da primeira chamada de modelo.
3. Cada parágrafo recebe uma única leitura editorial, orientada pelo briefing completo.
4. Uma síntese única integra o corpus.
5. O Eixo Narrativo é proposto e a execução para obrigatoriamente.
6. Apenas uma aprovação humana explícita inicia o Projeto Editorial.

O briefing contém tema, abordagem, público, produto, idioma e duração. A duração determina seleção e profundidade; nunca reduz a leitura das fontes nem corta parágrafos.

## Contratos editoriais

### Digestão de parágrafo

Entrada: um parágrafo completo e o briefing. Saída: observações distintas, tags livres e relevância para o briefing.

### Entendimento editorial

Entrada: briefing e notas ordenadas dos parágrafos. Saída: síntese integrada, ideias centrais, relações e tensões, limites e possibilidades editoriais.

### Eixo Narrativo

Entrada: briefing e entendimento editorial. Saída: uma direção intelectual/editorial e sua justificativa.

### Projeto Editorial

Entrada: Eixo aprovado, briefing e entendimento. Saída: título de trabalho, promessa, assuntos sustentados pelas fontes dimensionados para a duração, sugestões opcionais de conhecimento do modelo e oportunidades de novas fontes.

## Garantias operacionais

- A mesma solicitação possui identidade determinística e não abre deliberadamente outra execução paga.
- Trabalho concluído é retomável sem reler parágrafos persistidos.
- Toda chamada paga registra tokens de entrada, cache, saída, raciocínio, total, latência, resultado, erro e custo tarifado.
- Toda etapa registra duração, estado e artefatos produzidos.
- Falhas são persistidas e exibidas ao usuário com a causa real.
- Nenhum endpoint capaz de ler dados ou iniciar processamento aceita acesso sem sessão.
- A interface, a API e o Job usam o mesmo contrato genérico para qualquer tema ou conjunto de fontes.
