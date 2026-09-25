# Hefesto V4 — fluxo editorial e matriz relacional

**Status:** contrato de produto registrado em 25/09/2026. O caminho da matriz está implementado como opção na interface, ao lado do caminho anterior. A qualidade editorial e operacional ainda depende de validação pelo usuário na interface; o Teste B permanece suspenso.

## Objetivo e fronteira

O Hefesto atual já produz Projeto Editorial e texto final. A matriz deve melhorar a qualidade dos dois ao preservar ideias, dados, limites, tensões e contradições que podem desaparecer nas condensações sucessivas. Comparar a qualidade do novo caminho com o caminho atual antes de substituí-lo em produção. Não considerar a simples conclusão de uma execução como prova de melhoria.

**A ingestão e a desmontagem de arquivos estão congeladas.** Não alterar upload, Acquire, identificação de formatos, Docling, pypdf, OCR, extração, normalização, segmentação, ativos, gravação de fontes/parágrafos/frases, recibos ou progresso. A matriz começa **depois** da persistência confirmada de parágrafos e frases no PostgreSQL. Inicialmente recebe os trechos da seleção existente. Para novas fontes, reutiliza-se a ingestão existente sem modificá-la.

Cada contribuição da matriz deve ser pequena, ter proveniência explícita e, quando derivada de fonte, vínculo verificável ao trecho original. Contribuições provenientes do conhecimento do LLM têm proveniência própria e não podem aparecer como evidência documental. Preservar conflitos entre fontes, cobertura por assunto e referências originais; o redator deve poder recuperar contribuições pertinentes sem depender de um único resumo curto.

## Percursos de entrada

| Entrada | Sequência até o Projeto Editorial |
| --- | --- |
| Fontes fornecidas pelo usuário | Ingerir as fontes → selecionar trechos → construir a matriz → gerar o **primeiro Projeto Editorial**, com eixo e as duas categorias de sugestões disponíveis. |
| Zero fontes (“Procure as fontes para mim”) | Gerar um **pré-projeto editorial pelo LLM** a partir do briefing → apresentar para aprovação, com campo de remoque → somente após aprovação buscar fontes automaticamente → adquirir e ingerir as fontes pela esteira existente → selecionar trechos, construir a matriz e gerar o **Projeto Editorial definitivo**, sem sugestões automáticas. |

O pré-projeto orienta a busca e não pode ser ignorado no projeto definitivo. O material adquirido sustenta o que for possível; assunto que não obtiver suporte não é inventado no projeto definitivo. Uma alteração por remoque no pré-projeto exige reapresentação e nova aprovação antes de começar a busca.

## Apresentação, sugestões e rodadas

O Projeto Editorial é a superfície de controle do usuário, não a exibição da matriz inteira. **O campo de remoque fica sempre abaixo do projeto apresentado:** pré-projeto, primeiro Projeto Editorial e versões revistas. O usuário pode pedir acréscimos, retiradas ou alterações; o Hefesto atende ao pedido, atualiza a compreensão editorial afetada e apresenta a versão revista para nova aprovação. Remoque não reinicia automaticamente a oferta de sugestões.

No **primeiro Projeto Editorial originado de fontes fornecidas**, oferecer duas categorias distintas:

1. **Contribuições do LLM:** acréscimos pequenos e atômicos de conhecimento do modelo dentro de um assunto. Não justificam uma busca de fonte, não são evidência documental e não exigem mudar o eixo por si só. Se escolhidos, entram com proveniência de LLM na matriz e no assunto pertinente; o projeto é revisto conforme a escolha do usuário.
2. **Oportunidades de enriquecimento com novas fontes:** propostas cujo desenvolvimento precisa de suporte documental novo. Se escolhidas, iniciam uma busca pontual pelo assunto; não entram na matriz como afirmação documental antes de haver fonte adquirida e digerida.

Uma versão revista após escolha de sugestão não oferece outra rodada **automática** de sugestões. O Projeto Editorial definitivo após o fluxo de zero fontes também não as oferece. Em todos os casos, o remoque continua disponível.

### Enriquecimento com nova fonte

É uma instância pontual da busca do fluxo de zero fontes, aplicada ao assunto escolhido:

1. Buscar e selecionar fonte que possa sustentar aquele assunto, mantendo identidade e cobertura; Acquire pode obter outra cópia da mesma publicação, mas não substituí-la por outra parecida.
2. Adquirir e **ingerir somente a fonte nova** pela esteira existente. Não reprocessar nem desmontar novamente as fontes anteriores.
3. Selecionar e digerir o material novo em unidades pequenas; integrar suas contribuições à matriz existente com proveniência, cobertura e vínculos aos trechos originais.
4. Reexaminar o conjunto atualizado e **refazer o Projeto Editorial inteiro, inclusive o eixo**, para apresentação e aprovação. A busca/digestão são pontuais; a decisão editorial considera o conjunto.
5. Se a fonte não sustentar a proposta, não inventar o acréscimo nem incluí-lo como assunto sustentado.

Se o acréscimo exigir mais tempo, aumentar a duração total. **Não reduzir o desenvolvimento nem a alocação dos assuntos anteriores** para acomodá-lo.

## Atomicidade, limites e retomada

A matriz não é um JSON gigante nem uma nova cadeia de resumos cada vez mais curtos. Respostas estruturadas do LLM podem ser JSON curto de trânsito, validado e inserido em tabelas relacionais. Cada chamada realiza uma tarefa limitada, com entrada e saída medidas, poucas contribuições completas, referências verificadas e checkpoint por lote. Persistir lotes concluídos e retomar somente os faltantes. Ao encontrar `MAX_TOKENS`, reduzir/dividir a unidade que falhou e retomá-la; não usar aumento sucessivo do limite de saída como estratégia de arquitetura.

Aplicar o mesmo princípio à relação entre contribuições, síntese por assunto, geração do eixo/projeto e redação. Nenhuma chamada deve devolver a matriz inteira nem receber um único resumo curto como substituto de toda a evidência. Registrar tokens de entrada, saída e pensamento, custos, erros e cobertura para comparação com o caminho atual.

## Estado de implementação e referências

A documentação em [product-architecture.md](product-architecture.md) descreve o fluxo existente. O caminho da matriz é opcional por execução e usa as migrações `1006_editorial_matrix.sql` e `1007_matrix_work.sql`. Extração, cobertura, contribuições, evidências, relações, roteamento por assunto e resultados intermediários ficam persistidos. A síntese e a escrita usam chamadas por lote ou parte, com recuperação das contribuições da matriz; as tarefas de síntese dividem lotes quando ocorre `MAX_TOKENS`. O caminho anterior continua disponível para comparação.

Na interface, o primeiro Projeto Editorial originado de fontes fornecidas apresenta as duas listas. A escolha de contribuição do LLM registra proveniência de modelo; a escolha de fonte pesquisa e ingere apenas a nova fonte com a esteira existente, integra suas contribuições e refaz o projeto inteiro. Revisão do projeto, ações de sugestão e aprovação estão disponíveis por rotas próprias. A interface mantém o campo de alterações abaixo do projeto, inclusive durante o processamento, quando o bloqueia temporariamente. Ação editorial concorrente é recusada e falhas restauram a possibilidade de edição.

A implementação **não equivale à validação da qualidade**: comparar projetos e textos com o caminho anterior, conferir cobertura, duração, proveniência, custo e retomada pela interface quando o usuário decidir testar. O Teste B não foi executado como parte desta implementação. O memo de retomada de 25/09/2026 registra a linha de base e o escopo congelado.
