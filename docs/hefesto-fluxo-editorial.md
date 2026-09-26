# Hefesto V4 — fluxo editorial vigente

**Decisão de 26/09/2026:** a arquitetura experimental da matriz editorial foi retirada. O planejamento e a produção de texto usam novamente a esteira editorial anterior: seleção de trechos, compreensão por assunto quando necessária, Projeto Editorial e redação com revisão. A ingestão e a desmontagem de arquivos continuam congeladas.

O experimento da matriz não concluiu o Projeto Editorial do Teste B após cerca de duas horas e 1.296 chamadas ao modelo (aproximadamente 2,51 milhões de tokens). Melhorias futuras devem aperfeiçoar a esteira existente e medir qualidade, tempo e consumo antes de outra mudança arquitetural. Quando o material ultrapassar a janela do modelo, dividir o trabalho sem truncar ideias, tensões ou contradições; não reduzir outros assuntos para acomodar uma fonte nova.

## Percursos e intervenções

| Situação | Comportamento |
| --- | --- |
| Fontes fornecidas | Ingestão existente, seleção, compreensão e primeiro Projeto Editorial. Este primeiro projeto oferece contribuições pequenas do LLM e oportunidades de novas fontes. |
| Zero fontes | Pré-projeto gerado pelo LLM, aprovação do usuário, busca e aquisição de fontes, ingestão e Projeto Editorial definitivo. As duas listas de sugestões ficam vazias nessa segunda apresentação. |
| Remoque | O campo permanece sob o pré-projeto e sob todas as versões do Projeto Editorial. O pedido do usuário provoca revisão e nova apresentação para aprovação. |
| Contribuição do LLM | Ideia pequena inserida no assunto pertinente, sem alegar que veio de fonte documental. A versão revista não gera outra lista automática. |
| Enriquecimento por fonte | Busca pontual para o assunto escolhido, aquisição e ingestão apenas da fonte nova, seleção de seus trechos e revisão do projeto com a compreensão já existente. Pode aumentar a duração; preserva os demais assuntos. |

O pré-projeto aprovado orienta a busca e o projeto definitivo. Assuntos sem suporte nas fontes adquiridas não devem ser apresentados como sustentados documentalmente. A ingestão original, seus formatos, extração, normalização e estrutura persistida não fazem parte das revisões editoriais.
