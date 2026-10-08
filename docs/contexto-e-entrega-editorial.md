# Contexto e entrega editorial — 1.5.17

## Problema observado

Uma revisão de voz ultrapassou o limite configurado depois da redação. O contexto continha cópias do artigo no corpo, no índice de passagens e nos blocos de edição. A conferência inicial considerava somente os materiais; instruções e contrato de resposta ainda eram adicionados depois.

O rascunho também apresentou desvios verificáveis: extensão muito acima da pauta, parágrafos extensos praticamente repetidos, ausência de etapas numeradas em um tutorial e resíduos de formatação. O parecer do leitor crítico minimizou problemas presentes no texto. Aprovação por um agente não é comprovação de qualidade.

## Correção de contexto

- O artigo enviado pode usar `content_parts`: partes literais e referências aos blocos ou passagens já presentes na mesma solicitação. A ordem, os caracteres, as ressalvas e o texto fora dos índices limitados são preservados integralmente.
- Os pareceres também reutilizam os trechos literais já disponíveis, mantendo todos os apontamentos. A revisão global recebe o percurso do artigo e a cobertura factual, sem repetir o plano completo e suas decisões de comparação.
- A compactação altera somente a representação enviada. Artigo, transcrições, evidências e índices originais permanecem salvos; as citações e edições continuam sendo resolvidas pelo servidor contra os textos literais.
- A preparação da solicitação é separada do envio. O limite em caracteres considera materiais, instruções e esquema de resposta. Etapas diretas são conferidas antes de criar a tentativa; callbacks são conferidos antes de abrir o cliente do provedor.
- Uma recusa local não consome chamada de IA. O limite configurado e o modelo não são aumentados automaticamente. O limite em caracteres continua sendo uma proteção local, não uma medição exata da janela de tokens do modelo.
- As entregas concluídas do mesmo ciclo continuam reaproveitáveis. A correção não reinicia extração, planejamento ou redação já concluídos.

## Diagnóstico editorial

O editor de voz recebe uma avaliação local da versão atual, além do parecer do leitor crítico. A revisão final conserva os bloqueios locais independentemente da opinião do modelo.

- Extensão acima da meta e parágrafos com mais de 180 palavras geram avisos para avaliação editorial; não encerram a geração com erro técnico.
- Parágrafos extensos quase idênticos, notas sem definição e quebras de parágrafo escapadas no texto são defeitos de entrega.
- Quando o gênero solicitado é tutorial, a verificação exige uma lista ordenada ou títulos de etapas numerados. Outros gêneros não recebem essa exigência. A presença de numeração não comprova que os passos são corretos ou úteis: isso continua dependendo de conferência semântica e editorial.
- Exemplos em blocos de código e código inline são excluídos da detecção de resíduos de formatação.
- A validação de números distingue marcadores de listas Markdown de quantidades factuais, permitindo organizar o texto sem liberar números inventados no conteúdo.

Essas verificações são genéricas e não contêm instruções específicas de um setor. Elas não reescrevem artigos automaticamente, não asseguram qualidade literária e não substituem testes editoriais com conteúdos reais.

## Validação

Testes de reconstrução verificam igualdade integral do artigo, inclusive espaços, ordem e trechos fora do índice. Testes com o SDK e transporte simulado verificam seleção de blocos, orçamento antes do envio e reaproveitamento de resultados.

A reprodução local do ciclo interrompido conserva as 58 entregas anteriores, percorre as etapas pendentes com respostas simuladas e não faz chamadas externas. A simulação não aprova a qualidade do artigo nem altera o original em produção.
