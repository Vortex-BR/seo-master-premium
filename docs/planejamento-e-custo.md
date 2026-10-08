# Planejamento, arquitetura da redação e custo — 1.5.19

O incidente observado em 8 de outubro de 2026 terminou após 19 minutos e 120 chamadas. O primeiro
rascunho estava salvo desde 12 minutos e 20 segundos. As três pesquisas não acrescentaram evidência
verificada, mas provocaram novas classificações e comparações. O planejamento marcou os 38 itens
extraídos como obrigatórios. A redação tinha 43 itens de lista, nenhum H3 e aproximadamente 70% das
palavras dentro de listas. Esses dados descrevem essa execução; não são um benchmark de modelos.

## Alterações

- **Seleção global da pauta:** novos ciclos de composição usam um planejamento único quando o
  pedido completo, incluindo instruções, evidências e esquema, cabe no limite configurado. Ele
  substitui classificação de assuntos, comparações repetidas, planos por assunto e consolidação.
  A extração, a conferência dos itens e das relações dentro de cada vídeo e a revisão factual final
  permanecem independentes. Se o pedido não couber, o planejamento por partes é usado antes de
  enviar a chamada global. Nenhum trecho é truncado para fazê-lo caber.
- **Arquitetura por seção:** o contrato do plano exige, no fluxo global, um modo de apresentação,
  uma justificativa e os subtítulos H3 necessários. O redator e o revisor recebem essa arquitetura.
  H2 delimita seção; H3 subdivide uma seção que precisa de desenvolvimento; parágrafos explicam
  ações, motivos e relações; listas servem a materiais, verificações ou sequências curtas.
  O formato depende da tarefa e das fontes. Não há instrução interna específica de um nicho.
- **Escolha editorial real:** o planejador global pode selecionar `used`, `duplicate`, `out_of_scope`,
  `unsupported` ou `pending` para cada item. O esquema exige a decisão de todos os itens, mas não
  obriga o artigo a conter tudo. Uma exclusão conserva o material e sua justificativa no inventário.
- **Pesquisa idempotente:** notas e páginas inacessíveis não mudam a versão do conhecimento.
  Replanejamento depende de alteração das evidências ou das decisões sobre pendências. Resultados
  de busca e estado anterior são salvos para retomar uma extração interrompida sem pagar outra busca.
  Uma pesquisa feita durante correção não abre nova pesquisa dentro do replanejamento.
- **Dúvidas de áudio:** avisos de confiança da transcrição são encaminhados à fonte original.
  Publicações na web não resolvem o que foi dito naquele intervalo. O aviso continua explícito;
  não é dispensado por uma votação dos agentes.
- **Correções com orçamento:** quando existe revisão da versão atual, o sistema verifica o saldo
  antes de iniciar outra rodada. Rascunho e apontamentos continuam disponíveis se o saldo for
  insuficiente. Uma proposta sem alteração aplicável conserva a revisão em vez de pagar por outra
  avaliação do mesmo texto. O limite de chamadas continua valendo; não foi aumentado.
- **Leitura:** distribuição de listas, hierarquia de títulos e percurso sequencial entram nos
  diagnósticos. Excesso de listas e saltos de títulos são alertas, não novos bloqueios nem gatilhos
  automáticos de reescrita paga. Um checklist legítimo pode continuar sendo lista.
- **H3 numerados:** numeração de etapas é tratada como apresentação. Números factuais em títulos
  e no texto continuam sujeitos à conferência de origem.

## Compatibilidade e implantação

O Markdown continua sendo o conteúdo editável. O renderizador HTML, a exportação e a integração com
WordPress não foram substituídos. Os testes verificam H2, H3, parágrafos, lista e citações nos blocos
WordPress existentes. Artigos e planos antigos continuam legíveis; o campo de apresentação é
opcional ao carregar dados antigos. `planning_version=1` só é fixado em novos ciclos, preservando
o caminho e as entregas de ciclos salvos.

Uma retomada de um ciclo que já esgotou o saldo não gera uma redação nova nem aprova fatos pendentes.
Se a revisão corresponde ao artigo salvo, ela pode ser entregue como `needs_review` sem novas
chamadas. Não clique em **Gerar novamente** para validar a implantação: isso inicia trabalho pago.

## Verificação e limites

Há regressões para pesquisa vazia, interrupção de pesquisa, dúvidas de transcrição, retomada com saldo
esgotado, proposta sem mudança, planejamento global, exclusões justificadas, contexto insuficiente e
estrutura exportada. Os testes usam respostas simuladas na fronteira do provedor; não medem a qualidade
literária de uma nova geração real. A simulação local do incidente preservou o artigo e as 120 chamadas,
sem acesso à API. O pedido do planejamento global com as 38 informações coube no contexto de 160 mil
caracteres daquela instalação. Isso demonstra viabilidade técnica, não garante duração nem custo final.

O contrato usa saídas estruturadas já empregadas pelo projeto, conforme a
[documentação oficial da OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs).
Conformidade com o esquema não comprova clareza ou fidelidade: essas avaliações continuam necessárias.
