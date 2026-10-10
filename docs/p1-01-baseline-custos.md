# P1_01 — observabilidade financeira e baseline editorial

Implementação sobre `b366b4da2ef063371ea6391459208cefc57da22f` (P0_02), aplicativo **1.5.25**, pipeline editorial **7**. O manifesto dos seis documentos do pacote foi verificado antes das alterações. Evidências em `docs/evidencias/p1-01/`. Não foram feitos deploy, publicação WordPress, alteração em produção ou chamadas reais a provedores.

## Diagnóstico confirmado no checkout

`app/spending.py` já continha `spend_reservations`, reservas anteriores ao envio e retenção em timeout. Foi preservado como autoridade financeira. `scripts/premium_baseline.py` já permitia piloto com um vídeo e captura offline; foi ampliado. A auditoria financeira offline por execução é a nova projeção desse ledger, não um segundo livro de cobranças.

Foram corrigidas lacunas observadas: teste OpenAI fora do ledger; teto global de US$ 1 em contrato, normalização, servidor e formulário; limites estratégicos de tokens sem aplicação; valores ausentes convertidos em zero; soma de tokens sem validação; falta de identidade financeira de execução; impossibilidade de reconciliar cancelamentos deixados apenas como reservados.

Os documentos da auditoria anterior descrevem 11 históricos e 368 registros. Eles misturam versões e retentativas. O total histórico de US$ 3,754716 era uma projeção por tokens, não custo observado deste patch ou preço de uma geração. O banco `data/seo.sqlite3` disponível durante este trabalho tem zero jobs; sua leitura isolada preservou DB/WAL/SHM. A fonte privada do piloto é um JSON histórico separado, sem artigo ou uso registrado. Não houve nova consulta à produção para validar custos.

## Fluxo instrumentado

| Entrada | Execução financeira | Tentativas e comportamento |
|---|---|---|
| `pipeline.run` | `article`, uma identidade por invocação, inclusive retomada | Engloba fontes e etapas editoriais. Cache de fonte e agente gera evento de reutilização. O orçamento continua acumulado por ID do artigo. |
| `strategy.engine.run` | `strategy_cycle`, versão dos agentes e fingerprint | Agent/síntese reservam chamadas e limite conservador de tokens atomicamente antes do envio. Checkpoints compatíveis registram reutilização. |
| `main.test_openai` | `connection_test`, entidade própria por clique | Uma resposta limitada a 32 tokens, modelo efetivamente enviado e orçamento configurado. Não contamina histórico financeiro do artigo. |
| `image_generation.run` | `image`, ligada ao ID financeiro do artigo | `paid_image` conserva reserva, hash dos parâmetros e modelo da tarefa; não repete uma solicitação já identificada pelo mesmo ID. |
| `youtube.transcribe_audio` | Etapa de transcrição dentro da execução do artigo | Whisper pago reserva duração antes do envio; tokens inaplicáveis permanecem nulos. |
| Whisper local, legendas/proxy, Supadata e bancos visuais | Eventos da execução ativa ou `unassigned_events` quando chamados isoladamente | Despacho externo registrado antes da operação; duração, erro e incerteza persistidos. Ausência de tarifa/fatura resulta em custo nulo. Cache de aplicação identificado separadamente. |

`app.cost_observability.run` usa `ContextVar`; o contexto é aberto dentro do worker, pois o executor não transporta automaticamente o contexto da requisição HTTP. `spending.reserve` captura `run_id`/`execution_id`, `attempt_id`, provedor, etapa, origem, fingerprint, versão do pipeline, limites e snapshot da tarifa. A reserva permanece na tabela original. As tabelas aditivas `cost_runs` e `cost_events` não armazenam prompts, chaves, URLs de proxy ou corpos de exceções dos provedores nos hooks instalados.

Uma retomada recebe nova identidade de execução; as retentativas internas pertencem à mesma invocação. O relatório reconhece pedidos repetidos por etapa e fingerprint dentro da execução. Chamadas com contexto deliberadamente diferente não são automaticamente classificadas como retry. Histórico sem identidade verificável permanece separado. O orçamento do artigo nunca é renovado ao criar um run.

## Valores e tarifas

| Campo/estado | Significado |
|---|---|
| `reserved_usd` | Autorização conservadora retida; inclui margem, não é fatura. |
| `charged_usd` / `spent_usd` | Contabilização do guard existente. Pode incluir margem/estimativa histórica; preservada por compatibilidade. |
| `calculated_usd` | Cálculo pelo uso suficientemente discriminado e tarifa congelada, sem margem. Continua separado da fatura. |
| `estimated_usd` | Estimativa identificada como tal quando os dados não permitem o cálculo completo. |
| `registered_usd` | Valor registrado mediante informação externa/reconciliação com responsável e evidência. |
| `uncertain` | A cobrança pode ter ocorrido; reserva continua consumindo saldo. |
| `null` / `unmeasured` | Não observado. Não significa gratuito. |
| `known_*` | Subtotal dos registros medidos, com cobertura explícita; não substitui total desconhecido. |

Snapshots congelam tarifa, unidade, serviço e margem usados na tentativa. Alterar a tabela de preços posteriormente não recalcula uma liquidação daquela tentativa. Na transcrição, o retorno de duração do provedor, quando válido, permite uma projeção por duração; a duração medida localmente continua identificada como estimativa. Sem fatura, não se certifica cobrança efetiva. Retorno sem lista de ferramentas conserva contagem desconhecida e reserva incerta quando pesquisa foi autorizada; pedidos sem ferramentas permitem confirmar zero chamadas de pesquisa pelo próprio request.

Foi conferida a tarifa padrão de `gpt-4.1-mini`: US$ 0,40/0,10/1,60 por milhão de tokens de entrada/entrada em cache/saída. [Página oficial do modelo](https://developers.openai.com/api/docs/models/gpt-4.1-mini). Whisper permanece em US$ 0,006 por minuto. [Página oficial do Whisper](https://developers.openai.com/api/docs/models/whisper-1). As demais tarifas preexistentes, incluindo imagem, foram preservadas com status `preexisting_declared_tariff`; não são apresentadas como recém-confirmadas. Nenhum modelo foi migrado.

O cálculo e a reserva de pesquisa usam a política codificada de ferramentas e conteúdo, cuja conciliação com fatura não foi observada. A tarifa publicada distingue chamadas e conteúdo pesquisado. [Tarifas oficiais](https://developers.openai.com/api/docs/pricing). O fluxo Video-First atual não inicia novas buscas. O endpoint de contagem de entrada preexistente continua sendo uma operação de rede anterior à reserva, com fallback offline; não houve chamada real nesta validação.

Cache da aplicação evita uma nova chamada naquele evento; isso não permite estimar uma economia monetária contrafactual sem comparação. `cached_input_tokens` representa medição do fornecedor e pode reduzir o preço de uma chamada efetivamente enviada. Totais de cache do fornecedor ficam nulos se faltarem medições aplicáveis; subtotal e cobertura são separados.

## Limites efetivos

`VoiceProfile.max_spend_usd` conserva padrão US$ 1 e mínimo US$ 0,01, permitindo autorização superior pelo operador. Não existe teto global fixo ou preço obrigatório por artigo. O ledger verifica saldo acumulado antes de cada chamada OpenAI, inclusive imagens e transcrição paga. Reserva incerta reduz saldo. Histórico não mensurado bloqueia novo consumo mesmo após aumento de orçamento, até reconciliação.

`Settings.connection_test_budget_usd` tem padrão US$ 0,01 e valor positivo definido pelo operador por teste. O formulário informa que o clique executa uma chamada cobrável. SDKs continuam sem retries automáticos; um novo clique representa nova autorização limitada. Este limite não é um orçamento global por conta, projeto, chave ou período.

Na estratégia, `max_spend_usd` opcional define o orçamento do ciclo; ausente, herda o perfil. `max_agent_calls` e `max_tokens_estimate` têm guardas reais e contadores monotônicos em SQLite. O limite de tokens soma bytes UTF-8 da requisição congelada + 4.096 de framing + teto de saída por tentativa; é um limite conservador, não tokens faturados. Timeout, falha e retomada conservam esse consumo. Cache não incrementa tentativa paga.

Pesquisa e descoberta de vídeos não existem no pipeline estratégico atual, que usa snapshots fornecidos. Seus limites aparecem com `available=false`, sem afirmar execução desses serviços. Requests com ferramentas não integradas são recusados. Ciclos históricos com tentativas sem contador verificável permanecem consultáveis, mas não recebem novas chamadas pagas automaticamente. Um novo ciclo exige ação explícita.

O controle OpenAI não consolida créditos/faturas de Supadata, proxy, banco de imagens, CPU, armazenamento ou trabalho humano. Esses custos ficam visíveis como não mensurados. Provedores externos mantêm seus controles de requisição/cache e incerteza; um limite USD total incluindo esses contratos ainda depende de tarifação e conciliação próprias.

## Consulta, métricas e operação

O link **Relatório de custos por execução** fica no histórico do artigo. Rotas autenticadas:

- `GET /api/jobs/{job_id}/cost-report`, com `run_id` opcional.
- `GET /api/costs/report`, com `job_id` e `run_id` opcionais.

Inclusive a autenticação dessas rotas lê uma cópia isolada. O original não é aberto pelo SQLite. O método compara duas capturas de DB/WAL, repete em caso de mudança e falha explicitamente se não obtiver estabilidade. A cópia temporária privada é lida com `query_only`, sem inicialização de tabelas. SHM é reconstruído somente na cópia. Esses relatórios não chamam `summary()` mutante ou migração.

CLI na raiz, preferindo saída privada:

```powershell
.venv/Scripts/python.exe scripts/cost_observability.py --database data/seo.sqlite3 --output .local/custos/relatorio.json
.venv/Scripts/python.exe scripts/cost_observability.py --database data/seo.sqlite3 --job-id ID_DO_ARTIGO --run-id ID_DA_EXECUCAO --output .local/custos/execucao.json
```

O relatório expõe tentativas, etapas, reservas inconclusivas, revisão, retry e reutilização. p50/p95 usam nearest rank somente com pelo menos 20 observações completas por escopo, operação e versão do pipeline. Geração, retomada e revisão não são misturadas. N, janela e cobertura acompanham a métrica; mistura entre grupos não produz percentil global. Percentis sintéticos dos testes não representam desempenho real da plataforma. Eficiência editorial fica nula enquanto não houver resultado humano e comparação apropriada.

As rotas podem ser desativadas com `COST_REPORTS_ENABLED=0` na implantação futura, conservando ledger, telemetria e guardas. A CLI é uma operação manual. A auditoria mantém cópias de DB/WAL em memória e diretório temporário; dimensionar RAM/disco para bancos grandes. Em servidor ativo com escrita contínua, usar uma janela estável ou snapshot consistente produzido pelo operador. Não colocar relatórios privados no Git.

Cancelamentos capturáveis marcam reserva como incerta e propagam a interrupção. No startup exclusivo do processo, `recover_inflight` transforma reservas em andamento em incertas e marca runs/eventos interrompidos, sem liberar saldo ou inventar duração. Isso é apropriado ao Docker atual com um worker. Não executar a recuperação periodicamente ou ao lado de outro worker usando o mesmo banco.

`spending.reconcile` aceita somente uma reserva incerta, responsável e referência de evidência do fornecedor. Escolher uma única resolução: uso suficientemente medido, valor registrado ou prova de ausência de cobrança. Guarda histórico antes/depois. Timeout, erro local ou ausência de tokens não comprovam ausência de cobrança. A função não consulta fornecedores nem verifica automaticamente a autenticidade de uma referência; cabe ao operador conferi-la. Não foi executada sobre produção.

## Baseline e otimizações seguintes

O piloto tem **um vídeo principal autorizado**, `cVnRvZ8uMCo`; o usuário declarou produção própria e uso para testes. Os outros dois vídeos são opcionais. Não se exige uma amostra de 12 para produzir um artigo.

`baseline-manifest.json` vincula o arquivo privado histórico por SHA256. Seus três segmentos/2.651 caracteres não certificam completude ou fidelidade ao vídeo. O baseline atual está **pendente**: artigo, run pago, custo real, latência de geração, modelo efetivamente usado e avaliação humana ainda não existem. O relatório identifica o modelo candidato do checkout; não o apresenta como modelo confirmado de produção. A ficha humana e os critérios estão em `baseline-human-review.json` e `baseline-checklist.md`. Nenhuma métrica foi preenchida artificialmente.

Componentes reutilizáveis na futura Inteligência Editorial Crítica: identidade e proveniência P0_01; snapshots e cache compatível P0_02; guardas/reservas e contratos tipados; apuração/plano/cobertura; histórico editorial; checks determinísticos; baseline com rubrica e medição por run. Uma futura avaliação crítica deve registrar seu próprio custo, evidências e revisão humana, sem renovar o orçamento ou transformar observações em bloqueio de exportação.

Experimentos recomendados, ainda não executados: comparar cache de fonte ligado/desligado sobre o mesmo material; diminuir contexto preservando informação essencial e qualificadores; testar modelo econômico em casos autorizados com rubrica idêntica; medir deduplicação e checks locais antes de acrescentar chamadas. Registrar run, custo conhecido/ausente, tempo, qualidade humana e versão em cada comparação. Não reduzir contexto, mudar modelo em produção ou prometer economia sem evidência de fidelidade e resultado.

## Migração, backup e rollback

A alteração é aditiva: ledger original e seus campos conservados; novas tabelas/índices e campos JSON coexistem com leitores anteriores. Não há backfill de identidades fictícias nem reescrita de históricos durante auditoria. Novas estruturas são criadas nas operações instrumentadas; a recuperação não cria tabelas ausentes.

Antes de eventual deploy autorizado, guardar imagem/binário anterior, configuração e backup consistente do volume. Com banco ativo, não copiar apenas o `.sqlite3` ignorando WAL. Verificar restauração em ambiente isolado e manter relatórios/credenciais privados. Preservar ledger após rollback; restaurar backup anterior pode apagar consumo posterior e exige conciliação explícita.

Rollback de aplicação: voltar à imagem anterior sem excluir `spend_reservations`, `cost_runs`, `cost_events` ou snapshots. Leitores antigos ignoram os campos/tabelas aditivos, mas voltam às limitações antigas: cap US$ 1 e ausência de novas medições/guards de tokens. Não promover reserva incerta a gratuita para facilitar rollback. Testar leitura de histórico, publicação pendente, reconciliação WordPress e exportação no ambiente isolado.

O patch preserva publicação WordPress como `pending`, HTTPS e proteção contra edição externa. Observações editoriais continuam não bloqueando exportação de artigo tecnicamente válido. Não acrescenta agente editorial, conhecimento inventado, experiências pessoais ou preenchimento por contagem de palavras.
