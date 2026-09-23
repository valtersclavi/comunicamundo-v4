# Entrada do Hefesto V4 — contrato congelado

Status: congelado em 23/09/2026, após quatro execuções de teste de ingestão. O critério de inclusão é o painel confirmar frases persistidas no PostgreSQL para pelo menos um arquivo real daquela extensão. É evidência do arquivo testado, não promessa de que qualquer conteúdo nessa extensão produzirá texto.

| Família | Extensões aceitas | Extensões comprovadas |
| --- | --- | ---: |
| Documentos e texto | `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.htm`, `.xhtml`, `.epub`, `.md`, `.txt` | 10 |
| Imagens | `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp` | 6 |
| Áudio | `.aac`, `.mp3`, `.wav` | 3 |
| Vídeo | `.avi`, `.mov`, `.mp4`, `.webm` | 4 |
| **Total** | | **23** |

## Evidência das execuções

| Execução no painel | Resultado |
| --- | --- |
| Lote 1, 10 arquivos | 9 extensões com frases conferidas no banco |
| Lote 2, 10 arquivos | 5 extensões com frases conferidas no banco |
| Lote 3, 9 arquivos | 6 extensões com frases conferidas no banco |
| Reteste de imagens, 4 arquivos | `.jpeg`: 20 frases; `.png`: 11; `.tiff`: 49, todas conferidas no banco |

O conjunto acima governa o seletor de arquivos, a validação no navegador e a validação de formatos no servidor. A interface preserva o acompanhamento individual por arquivo, o erro de leitura quando houver e a contagem de frases conferidas. Arquivos já enviados e o histórico dos testes permanecem disponíveis para inspeção.

O congelamento abrange a entrada e a interface de ingestão. Não altera extração, segmentação, compreensão editorial nem a sequência de digestão. Qualquer mudança nesse percurso exige auditoria separada, com defeito reproduzível e decisão explícita antes de editar o núcleo.
