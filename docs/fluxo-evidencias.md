# Fluxo editorial Video-First

O artigo nasce exclusivamente da explicação dos vídeos: raciocínio, exemplos, analogias, dicas, experiências e alertas do criador. A linguagem oral é adaptada para leitura em tela, preservando sua naturalidade. A pesquisa web opcional esclarece termos já mencionados; não fornece conteúdo, seções ou evidências ao artigo.

## Operação

Na aba **Planejamento**, o trabalho pode ser realizado em etapas:

1. **Obter fontes** obtém as transcrições e os metadados disponíveis.
2. **Planejar sem redigir** extrai os insights dos vídeos e organiza a pauta. O artigo anterior permanece disponível.
3. **Redigir a partir do plano** produz o artigo inteiro e executa uma conferência factual global.

O perfil permite desligar a redação automática. Salvar direção, editar o plano ou registrar uma resolução editorial não chama a OpenAI. As edições ficam bloqueadas durante o processamento para evitar sobrescritas concorrentes.

O briefing define pergunta, intenção, gênero, público, exclusões e extensão. O plano registra abertura, fechamento, percurso do leitor e seções com pergunta, finalidade, informações, condições, transição e pendências. O usuário pode reorganizar as seções e justificar a exclusão de uma informação, usando a versão atual do plano.

## Transcrição original e cópia editorial

O sistema aceita de um a cinco vídeos, até 120 mil caracteres por fonte e 180 mil no conjunto. Esses limites de inventário não garantem que a solicitação caiba no contexto do modelo.

Os segmentos originais permanecem imutáveis, com texto, IDs e timestamps. `clean_spoken_transcript` gera uma cópia editorial e remove apresentações vazias, pedidos de like, inscrição, vinhetas e vícios de preenchimento. Em uma fala que mistura engajamento e explicação útil, a limpeza preserva o conteúdo substantivo. Analogias, experiências, explicações técnicas e alertas continuam disponíveis.

O extrator recebe os vídeos limpos em uma única solicitação, incluindo o fim das transcrições. Cada `SpokenInsight` contém tópico, explicação falada, dicas práticas, analogias, alertas e IDs dos segmentos de origem. O inventário em blocos serve à inspeção e à proveniência; não cria chamadas de extração ou auditoria por bloco.

A conferência local dos IDs não comprova o significado das afirmações. O vínculo literal é preservado para a revisão factual posterior. Legendas automáticas, baixa confiança, duplicações, timestamps suspeitos e outros problemas observáveis são registrados quando disponíveis. O sistema não certifica a completude da transcrição nem presume ter analisado imagens, gráficos ou demonstrações visuais.

## Pauta e contexto interno

O planejador organiza os insights na ordem mais útil para responder à pergunta. O percurso pode ser explicativo, sequencial, comparativo ou outro formato sustentado pela fala. Métodos diferentes permanecem como alternativas atribuídas aos respectivos criadores; não há rodadas de comparação para forçar uma conciliação.

Cada informação recebe uma destinação explícita no plano. Informações usadas precisam ser atribuídas às seções correspondentes; exclusões precisam de justificativa. Um plano marcado como insuficiente não libera a redação. Uma resolução manual registra justificativa e referências à fonte original, conservando o problema e seu histórico. Pesquisa web não resolve incertezas de áudio nem substitui uma conferência do vídeo.

Todo material web é marcado `internal_context_only=True`. Apenas esclarecimentos validados sobre termos presentes na fala chegam ao modelo em `agent_background_knowledge`. Notas, páginas e IDs como `rn1` permanecem fora do mapa de evidências. A pesquisa não altera os insights, a cobertura ou a pauta, e sua indisponibilidade não impede um artigo sustentado pelos vídeos.

## Quatro entregas e orçamento

O fluxo ativo é linear: **extrator → planejador → redator → revisor factual**.

Planejar sem redigir normalmente usa duas chamadas. Redigir a partir de um plano compatível usa as outras duas, conservando o orçamento do mesmo ciclo.

| Trabalho | Consumo previsto |
| --- | ---: |
| Extração conjunta dos vídeos | 1 chamada |
| Planejamento | 1 chamada |
| Artigo completo com metadados | 1 chamada |
| Conferência factual global | 1 chamada |
| Pesquisa opcional | Até 4 unidades adicionais, incluindo a reserva das ferramentas |

O padrão é um teto de **8 unidades por ciclo**, configurável entre 4 e 8. Uma pesquisa pode usar uma chamada de busca, reservar até duas chamadas de ferramenta e usar uma chamada para extrair o contexto interno. Essa reserva entra no mesmo teto, mesmo que a ferramenta use menos solicitações. A pesquisa só começa quando preserva espaço para pauta, redação e conferência factual.

Uma falha de transporte ou formato pode ter uma recuperação limitada por etapa; a recuperação também conta no teto. Não há rodadas automáticas de comparação, edição, reparo editorial ou pareceres adicionais. Leitor crítico, editor de voz, analista Yoast, editor SEO, revisor de leitura e editor-chefe não integram o fluxo ativo.

A estimativa de 4–8 descreve chamadas e reservas, não preço monetário. Obtenção de transcrição e outras operações têm consumo próprio. O limite de contexto mede caracteres da solicitação completa, incluindo instruções e contrato; não representa uma medição exata da janela de tokens do modelo. Uma entrega que não cabe é recusada antes do envio, sem cortar evidências ou mudar para redação por seções.

## Revisão, cobertura e entrega

O redator escreve o artigo e os metadados em uma única chamada. O revisor factual recebe todos os trechos do artigo — título, metadados, subtítulos e corpo — e as transcrições originais. Confere significado, condições, atribuições, analogias, experiências e foco na pauta em uma única avaliação, sem pareceres de aprovação anteriores.

A declaração de uso feita pelo redator não certifica cobertura. Uma informação prevista e sem desenvolvimento fiel permanece pendente e bloqueia a aprovação. IDs e citações são limitados ao inventário recebido; o servidor resolve as evidências contra o texto original. Isso controla a proveniência, mas não garante que o modelo detecte todo desvio semântico.

SEO e formatação são avaliados em código, sem pareceres adicionais de IA. Ausência da atribuição exigida, clichês proibidos e referências externas geram bloqueios locais. Timestamps disponíveis ajudam a localizar a fala; a ausência de marcação gera aviso e não autoriza inventar um tempo.

O rascunho completo fica disponível antes da revisão. Falhas posteriores ou orçamento esgotado mantêm texto, histórico e revisão pendente. Preview e exportação não significam aprovação editorial. HTML, Markdown, JSON e formatos WordPress continuam disponíveis; o JSON inclui apuração, plano, cobertura, pendências e dados do rascunho. O envio ao WordPress mantém sua aprovação editorial explícita.

## Retomada e migração

Entregas válidas do mesmo ciclo são reutilizadas somente quando suas entradas e dependências correspondem. O cache é consultado antes de cobrar uma nova chamada. A passagem de planejamento para redação conserva o orçamento de um ciclo compatível; retomar não cria uma nova cota.

Alterar fontes ou direção invalida apuração e plano. Alterar o plano invalida a redação e a revisão; editar o artigo invalida sua revisão. Alterações manuais não chamam o provedor.

Artigos, execuções e artefatos históricos são preservados. Ciclos anteriores incompatíveis são regenerados pelo novo fluxo quando o usuário executa o trabalho; a migração não reescreve artigos existentes por conta própria. `EDITORIAL_FLOW=legacy` e `EDITORIAL_COMPOSITION=legacy` não reativam a arquitetura anterior em novos ciclos.

Os testes usam provedores simulados para conferir contratos, persistência, orçamento, retomada e exclusividade das fontes. Eles não comprovam a qualidade de uma resposta real. Para preparar os casos de avaliação sem chamadas externas:

```powershell
.venv/Scripts/python scripts/evaluate_editorial.py
```

As saídas e critérios ficam em `.local/editorial-evaluation`. Avaliações reais exigem seleção explícita de caso e `--live`, mantendo o teto de 8 por ciclo. A qualidade editorial exige comparar o artigo com a fala original, incluindo omissões, ressalvas, atribuições e afirmações sem apoio.
