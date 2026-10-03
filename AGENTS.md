# Hefesto — contratos de desenvolvimento

Estas regras são canônicas. Não substituir decisões editoriais por melhorias
inferidas, nem introduzir exceções por tema, arquivo ou execução de teste.

- O briefing define as perguntas e a direção antes da leitura das fontes.
- Fontes originais permanecem no PostgreSQL, com referências estáveis. Notas de
  entendimento editorial não substituem os trechos documentais por resumos.
- Evidências devolvidas pelo LLM contêm análise e referências, sem recitação
  documental. Recuperar os parágrafos originais pelas referências quando preciso;
  preservar sua análise e divisão por tamanho. Não exigir reprodução literal
  como condição de aceitação da compreensão.
- A ingestão/desmontagem está congelada. Não alterar leitores, normalização,
  formatos ou interpretação de documentos como parte de correções editoriais.
- Conteúdo longo deve ser dividido em unidades de processamento. Isso vale em
  TODAS as chamadas: compreensão, PE, enriquecimento, remoque, redação, revisão,
  estilo e edição narrativa, incluindo entrada e saída. Orçamentos delimitam
  cada chamada, nunca o tamanho total ou a quantidade de ideias do artefato.
- Não truncar conteúdo, cortar listas de ideias ou impor max_length editorial
  para acomodar o modelo. Detectar tamanho antes da chamada; quando o provedor
  interromper uma saída, continuar em tarefas menores e preservar o progresso.
- Não aceitar JSON/resposta interrompida como artefato completo. Montagem e
  validação finais são locais. Originais devem poder ser relidos por referência.
- Enriquecimento congela o PE anterior e agrega apenas a oportunidade autorizada;
  a proposta escolhida vira adendo obrigatório e prioritário ao briefing para
  busca, julgamento e compreensão. Contribuições aos assuntos existentes são
  bem-vindas, mas não substituem a promessa. Uma fonte externa só é incorporada
  se cumprir essa promessa, ampliar argumentos/assuntos visíveis no PE e aumentar
  a duração. Se não cumprir, trocar a fonte dentro das quatro rodadas existentes;
  ao esgotá-las, informar a contribuição não cumprida e preservar PE e promessa.
  Contribuições pontuais do LLM não têm essa obrigação de aumentar a duração.
  Remoque pode alterar o PE conforme a vontade explícita do usuário e revisitar fontes.
- Preservar a liberdade do editor de narrativa para fundir e reordenar passagens;
  fronteiras de processamento não são fronteiras editoriais.
- Testes locais devem simular limites, falhas, referências e retomada. Não usar
  chamadas pagas ao LLM nem acompanhar testes de produção sem autorização. O
  usuário conduz a avaliação real de qualidade e consumo.
