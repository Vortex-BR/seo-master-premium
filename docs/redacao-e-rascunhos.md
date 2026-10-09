# Redação e preservação dos rascunhos Video-First

A redação transforma a explicação do criador em um artigo de blog completo, com metadados, em uma única chamada. O objetivo é preservar o conteúdo e a didática da fala, organizando a leitura com parágrafos curtos e subtítulos claros.

## Da fala ao artigo

O redator recebe o percurso do plano, os insights, as evidências originais, o nome do criador e os timestamps disponíveis. Analogias, experiências, dicas e alertas seguem explicitamente em `source_spoken_insight`. Condições, restrições e limitações acompanham cada informação.

A introdução credita naturalmente o criador. Experiências e opiniões são atribuídas à pessoa que as relatou, sem transformá-las em vivências do blog. Referências como `[03:45]` localizam explicações quando o tempo está disponível na fonte. O sistema não autoriza inventar tempos, nomes, credenciais ou cenas não analisadas.

A fonte exclusiva de conteúdo é o vídeo. `agent_background_knowledge` ajuda apenas a compreender termos já falados; não cria explicações, exemplos, recomendações, seções ou citações. Materiais web ficam fora do inventário factual.

A estrutura acompanha a pergunta do leitor. Um passo a passo é usado quando o criador ensinou uma sequência; explicações e comparações seguem a organização adequada ao seu conteúdo. A meta de palavras vale para o artigo inteiro e não obriga a preencher espaço com informações novas. Clichês artificiais são proibidos e verificados localmente.

`composition.write` é a composição obrigatória dos novos ciclos. Não há divisão em `write_section` ou rodízio de editores. Uma passagem local corrige apenas problemas reproduzíveis de serialização, duplicação literal adjacente e referências conhecidas, antes da revisão factual. Não há reescrita paga para cumprir métricas. Uma solicitação que não cabe no contexto é recusada antes do envio, com a entrega anterior preservada. Variáveis de ambiente legadas não ativam redação por partes.

## Texto salvo disponível para entrega

Uma resposta de redação válida é salva imediatamente como artigo completo. `draft_delivery` registra a revisão pendente. O texto anterior permanece no histórico e os artefatos conservam a versão paga, suas dependências e a declaração de cobertura.

O usuário pode ler, editar e exportar o artigo salvo, inclusive enviar ao WordPress como postagem pendente, sem aprovação editorial. Uma falha de revisão, falta de orçamento ou interrupção posterior não apaga o texto. Os estados legados continuam disponíveis para compatibilidade; a API separa processamento, diagnóstico editorial e disponibilidade de entrega:

- `budget_exhausted`: o teto foi atingido; o rascunho pode estar completo, com conferência pendente.
- `error`: uma etapa falhou; entregas válidas anteriores continuam disponíveis.
- `needs_review`: há observações editoriais ou análise incompleta; conteúdo tecnicamente válido continua exportável.
- `ready`: o artigo está disponível. Após edição manual, uma nova análise é opcional.

A edição manual é liberada quando o processamento termina ou é interrompido. Editar o artigo arquiva a revisão e invalida a retomada automática da versão anterior, preservando a entrega. Salvar um plano ou uma resolução editorial também não chama a OpenAI. Criar artigo executa o fluxo completo; planejar e revisar separadamente continuam opcionais.

HTML, Markdown, JSON e formatos WordPress preservam os fluxos de exportação. A renderização converte H2, H3 e listas em estrutura apropriada e resolve as referências de vídeo. Revisão editorial não condiciona o envio; o aplicativo conserva o status pendente no WordPress e não publica automaticamente. [Arquitetura, correções e proteções técnicas](automacao-editorial.md).

## Chamadas e recuperação

O caminho normal usa quatro chamadas: extração, pauta, artigo completo e revisão factual global. O teto auxiliar padrão é 24; o perfil aceita de 4 a 24, respeitando também o limite financeiro acumulado do artigo. Uma recuperação por etapa pode elevar o fluxo principal a oito chamadas. Não há rodadas adicionais de reescrita editorial paga.

Novas pesquisas estão desativadas na criação e na retomada. Notas históricas permanecem como contexto interno, sem virar evidência. Não há reparo pago para alongar o artigo ou completar itens declarados como ausentes: essas observações permanecem no diagnóstico opcional.

Uma falha de transporte ou formato pode receber uma recuperação limitada da mesma etapa, sujeita ao teto. Isso não é uma segunda rodada editorial. Uma recusa local por contexto não cobra chamada.

O cache é consultado antes do limite. Um rascunho pago com as mesmas entradas pode ser recuperado mesmo com orçamento esgotado, sem cobrar novamente o redator. Mudanças de plano, fontes ou outras dependências exigem uma entrega correspondente à nova entrada.

A última chamada disponível pode produzir o rascunho. Se faltar espaço para a revisão, o texto continua exportável com diagnóstico incompleto. Retomar conserva a contagem do ciclo e o limite financeiro acumulado. Um resultado pago que não chegou a ser persistido pode precisar ser repetido.

## Conferência independente

A declaração de cobertura do redator é armazenada em `draft_coverage`, incluindo informações ausentes e diagnósticos locais. Ela não equivale a comprovação factual.

Uma única revisão factual confere o artigo inteiro contra as falas originais, incluindo título e metadados. A avaliação não recebe os pareceres de aprovação anteriores. Conteúdo previsto sem desenvolvimento fiel, informações possivelmente sem apoio, ressalvas perdidas e atribuições incorretas geram diagnósticos que precisam de evidência para justificar alterações. O diagnóstico de SEO e formatação roda em código; o resultado não condiciona a entrega nem provoca reescrita automática.

Artigos e artefatos de ciclos históricos permanecem disponíveis. Ao executar um ciclo antigo incompatível, o sistema inicia a arquitetura Video-First e conserva o histórico; não apresenta uma auditoria antiga como validação da nova versão.

Os testes com SDK e transporte simulado verificam gravação do rascunho, exportação, limite de chamadas, recuperação e cache. Não aprovam a qualidade de uma resposta real do modelo. A avaliação editorial precisa conferir o artigo contra o vídeo, especialmente analogias, experiências, condições e alertas.
