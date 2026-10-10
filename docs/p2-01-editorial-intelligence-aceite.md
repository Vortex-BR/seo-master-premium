# P2_01 — Aceite e evidências

O aceite distingue implementação, comportamento reproduzido em fixtures e resultado editorial real. Nenhuma chamada paga real ou publicação em produção foi executada para demonstrar esta etapa. Artigos válidos permanecem exportáveis quando a IEC não encontra melhoria ou não consegue concluir a análise.

Base: `49a1f3e92ece84ae2d7f9ae84e426341a7ab05cb`, app `1.5.26`. Versão do checkout implementado: app `1.5.27`, IEC `1`, política `iec.policy.v1`; editorial `7`, agentes `7` e evidências `2` preservados. A arquitetura e operação estão em [p2-01-editorial-intelligence.md](p2-01-editorial-intelligence.md); o manifesto do pacote está em [package-integrity.json](evidencias/p2-01/package-integrity.json).

## Comandos reproduzíveis

Os comandos abaixo usam fixtures isoladas, sem banco de produção ou credenciais pagas. Em Windows, o Python utilizado é o `.venv` do projeto. Logs locais e arquivos de banco das fixtures não devem ser publicados.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_editorial_intelligence_core.py
.venv/Scripts/python.exe -m pytest -q tests/test_editorial_intelligence_finance_research.py
.venv/Scripts/python.exe -m pytest -q tests/test_editorial_intelligence_runtime.py
.venv/Scripts/python.exe -m pytest -q tests/test_editorial_intelligence_review.py
.venv/Scripts/python.exe -m pytest -q
node --test tests/frontend_export.test.cjs tests/frontend_sources.test.cjs tests/frontend_consistency.test.cjs tests/frontend_costs.test.cjs
git diff --check
```

O build Docker e os cinco checks JS seguem [ci.yml](../.github/workflows/ci.yml). O workflow usa Python 3.12 e Node 22 no Linux. A execução local do núcleo usou Python 3.12.10. A aprovação final deve indicar commit e run específicos, sem usar sucesso de patches anteriores como substituto.

| Verificação | Resultado observado / estado |
|---|---|
| Núcleo puro IEC | 96 casos aprovados; DB, configuração persistida e cliente de provedor bloqueados pelas fixtures. |
| Finanças e pesquisa | 53 casos aprovados, incluindo leitura HTTP com custo adicional de IA conhecido zero e infraestrutura desconhecida. |
| Runtime IEC | 37 casos aprovados, incluindo cache vencido, renovação e preservação do orçamento acumulado. |
| Revisão posterior de artigo enriquecido | 15 casos aprovados: origem externa, offsets, fontes vencidas, metadados, histórico, alinhamento e áudio incerto. |
| Comparação de request sem provedores | [request-context-comparison.json](evidencias/p2-01/request-context-comparison.json): 538 caracteres redundantes removidos por estágio nesta fixture, com fontes, demais materiais, instruções e esquema idênticos. Tokens e preço real não medidos. |
| Regressão Python integral | 1.242 testes aprovados em 322.47 s, Python 3.12/Linux; [CI do código](https://github.com/Vortex-BR/seo-master-premium/actions/runs/38070988349). |
| Node e checks de sintaxe | 73 testes e cinco checks de sintaxe aprovados localmente em Node 24.11.1; CI final registra Node 22. |
| Build Docker | Aprovado no [CI do código](https://github.com/Vortex-BR/seo-master-premium/actions/runs/38070988349); nenhum deploy executado. |
| Avaliação humana cega / APIs reais / produção | Não executada; não verificada. |

Os 201 casos IEC passaram juntos em 145.79 s, com Python 3.12.10 no Windows. O [recibo de validação](evidencias/p2-01/validation.json) registra cada caso, comando, tempo, versão e SHA256 dos arquivos. A suíte integral e o Docker foram aprovados no CI do código, com hashes conferidos no recibo.

## Cenários do pacote

As funções abaixo são âncoras específicas para repetir cada cenário. O runtime usa provedor simulado com requests reais preparados, reservas reais em SQLite isolado e uso simulado não zero. A pesquisa HTTP/WordPress é simulada quando aplicável.

| Cenário do plano | Evidência reproduzível | Relação com o código e resultado requerido |
|---|---|---|
| Artigo já claro | `test_editorial_intelligence_runtime.py::test_clear_article_uses_one_bounded_detection_without_search_or_rewrite`; core `test_no_change_is_empty_without_rewrite` e `test_shadow_only_reports_explicit_signals_and_existing_gaps` | Shadow sem IA; ativo retorna `no_change` após uma detecção solicitada. Nenhuma pesquisa, composição ou reescrita. Repetição reutiliza o checkpoint. |
| Lacuna com fundamento no vídeo | Runtime `test_video_suggestion_three_calls_manual_apply_and_undo_retain_original_trace`; core `test_original_cue_cites_parent_segment_and_original_cue_time` e `test_absent_time_does_not_invent_timestamp` | Fonte original recuperável; ID de segmento e timestamp reais, ou ausência explícita de tempo. Aplicação opcional com revisão e undo. |
| Lacuna com fonte complementar | Runtime `test_manual_https_sources_are_read_once_distinguished_and_never_promote_internal_notes`; core `test_external_link_is_server_owned_without_fake_video_citation` | Página externa lida uma vez, proveniência/hash conservados e hyperlink criado pelo servidor. Sem promoção de pesquisa antiga ou citação falsa ao vídeo. |
| Hipótese não verificável | Runtime `test_redundancy_unverified_hypothesis_and_semantic_uncertainty_keep_article`; core `test_personal_claims_and_hypotheses_not_universalized` | Hipótese sem fundamento e validação incerta não viram complemento. Artigo permanece salvo e exportável. |
| Explicação existente posteriormente | Core `test_full_article_later_explanation_is_not_duplicated` e `test_multiple_candidate_dedup_is_global`; runtime caso de redundância | Artigo inteiro é consultado; duplicação literal anterior, posterior ou entre candidatos é recusada. Deduplicação semântica depende do validador independente. |
| Natureza da fala | Core `test_attributed_experience_remains_provisional_and_original_nature`, `test_material_condition_is_not_dropped`, `test_external_context_is_never_attributed_to_video_creator` | Opinião/experiência/analogia mantêm natureza e atribuição. Condições explícitas não são removidas; externo não é testemunho do apresentador. |
| Versão concorrente | Runtime `test_concurrent_manual_article_or_context_edit_survives_paid_usage_and_old_proposal`, `test_apply_reloads_context_in_transaction_even_if_caller_article_is_identical`, `test_stale_proposal_status_cannot_resurrect_rejected_change`; core `test_old_block_id_cannot_edit_new_paragraph` | Artigo, contexto e status são recarregados na transação. A análise antiga não sobrescreve edição nova ou revive proposta rejeitada. |
| Custo e timeout | Runtime `test_timeout_reservation_stays_uncertain_retry_reuses_completed_detection_and_redacts_body`, `test_call_or_financial_limit_stops_new_requests_and_keeps_saved_article`; finance `test_incremental_allowance_retains_uncertain_attempt_across_resume` | Reserva e tentativa persistem. Retomada reutiliza entrega concluída, não zera consumo, não divulga corpo privado do erro e mantém artigo disponível. |
| Benchmark pareado | Fixtures de comparação off/shadow e receipts de custo/latência; avaliação humana ainda pendente | Os testes técnicos não substituem pares A/B reais com nota humana cega. Não há prova observada de melhora editorial ou economia real. |
| WordPress | Runtime `test_pending_wordpress_reconciliation_and_external_edit_protection_remain_after_iec`; comparação de cinco formatos off/shadow | Envio simulado preserva `pending`, reconcilia resultado incerto e recusa sobrescrever edição externa. Sugestões pendentes não vetam exportação. |

## Integridade, origem e conteúdo incompleto

| Caso | Arquivo / testes |
|---|---|
| Foundation adulterada, interna ou de origem inválida | Core `test_tampered_or_internal_foundation_is_rejected` |
| Offset/excerpt incorreto | Core `test_literal_anchor_and_offsets_must_match` |
| Mudança da fonte completa sem mudar o texto | Core `test_changed_original_source_rejects_saved_foundation_even_if_text_same` |
| Referência ambígua | Core `test_ambiguous_reference_is_not_foundation` |
| Proveniência externa ausente, vencimento inválido, HTTP ou rede privada | Core `test_external_provenance_is_required`; finance `test_page_reader_reuses_dns_pinning_rejects_private_and_ignores_scripts` |
| Números sem evidência selecionada | Core `test_numbers_must_appear_in_selected_literal_support` |
| First person inventada, HTML, links do modelo, timestamp falso, citações `rn` ou `v` fornecidas pelo modelo | Core `test_unsafe_redundant_or_unanchored_additions_are_rejected` |
| Parágrafo repetido, cabeçalho, bloco antigo e propostas sobrepostas | Core `test_repeated_identical_paragraph_cannot_receive_ambiguous_edit`, `test_heading_is_not_an_enrichment_target`, `test_duplicate_proposals_to_same_block_all_rejected` |
| Contexto integral sem truncamento e fontes de bastidor excluídas | Core `test_article_index_is_exhaustive_literal_with_crlf_and_no_truncation`, `test_full_context_uses_original_sources_never_historic_research` |
| Aprovação semântica incompleta ou duplicada | Core `test_semantic_acceptance_requires_every_criterion`, `test_semantic_validation_does_not_allow_duplicate_approvals` |
| Prova histórica adulterada | Runtime `test_corrupt_proof_is_rejected_before_any_saved_article_change` |
| Artigos legados e ausência de chave paga | Runtime `test_legacy_get_export_and_default_shadow_do_not_require_paid_key_or_backfill` |

A expressão `external_verified` não representa certificação de verdade; os testes conferem leitura, origem, hashes, literalidade e fluxo de validação. Os textos privados reais não aparecem neste relatório.

## Revisão posterior dos complementos aplicados

Os quinze casos coletáveis de [test_editorial_intelligence_review.py](../tests/test_editorial_intelligence_review.py) verificam a revisão de um artigo já enriquecido. A contagem inclui seis variantes parametrizadas de prova inventada; ela não equivale a quinze avaliações humanas.

| Caso | Testes da suíte de revisão |
|---|---|
| Fonte externa literal e identidade original do vídeo | `test_later_review_reads_external_literal_and_preserves_video_identity` |
| Somente complemento aplicado ainda presente e caminho básico preservado | `test_only_applied_claim_still_in_article_selects_complementary_review`, `test_shadow_base_review_keeps_existing_spoken_contract` |
| Expiração, referência inventada, excerpt/offset incorreto ou prova adulterada | `test_expired_complement_stays_unavailable_and_uncertain`, `test_enriched_review_rejects_fabricated_proof`, `test_corrupt_proof_does_not_revert_to_video_only_approval` |
| Cobertura planejada, pendências e alinhamento com a pauta | `test_enriched_review_preserves_pending_issues_and_editorial_alignment` |
| Dúvida de áudio associada a citação no artigo, mesmo sem apoio selecionado pelo revisor | `test_original_video_audio_issue_survives_unclaimed_review_passage` |
| Pesquisa antiga não promovida e links desconhecidos continuam sinalizados | `test_legacy_notes_are_not_foundations`, `test_generated_link_exemption_does_not_authorize_other_urls` |

Essa revisão recebe o artigo inteiro e foundations independentes para vídeo e complemento externo. Suporte deve ser literal e disponível; aprovação histórica ou presença de hyperlink não substituem a conferência semântica atual. Dúvidas permanecem visíveis sem vetar a exportação.

## Finanças, falhas, cancelamento e retomada

| Caso | Teste |
|---|---|
| Teto incremental e teto total exigidos juntos | Finance `test_incremental_and_article_limits_are_both_enforced` |
| Concorrência financeira com snapshots antigos | Finance `test_concurrent_stale_snapshots_cannot_overspend_incremental_allowance` |
| Namespaces separados sem renovar saldo do artigo | Finance `test_namespace_isolation_still_shares_article_budget_and_context_restores` |
| Zero conhecido e total desconhecido separados, resumo sem mutação | Finance `test_incremental_summary_is_readonly_and_separates_zero_from_unknown` |
| Orçamento/tools inválidos recusados antes do envio | Finance `test_incremental_budget_invalid_limits_do_no_writes`, `test_search_tools_require_explicit_positive_bounded_limit_before_dispatch` |
| Parcela de pesquisa explícita sem retirar teto geral | Finance `test_explicit_incremental_search_budget_replaces_legacy_fraction_but_not_article_cap` |
| Pesquisa incerta conserva reserva no mesmo artigo | Finance `test_iec_search_uncertainty_retains_same_entity_reserve` |
| Cache positivo com TTL e hash; negativo sem corpo privado do erro | Finance `test_positive_page_cache_preserves_hash_and_refetches_after_ttl`, `test_negative_cache_has_short_ttl_and_no_exception_body_or_fake_evidence` |
| Master concluído com fonte vencida exige nova leitura e validação, preservando o saldo financeiro original | Runtime `test_expired_pending_master_refreshes_source_and_reapproves_without_rebuying_detection` |
| Política/domínios fazem parte da identidade | Finance `test_cache_policy_and_trusted_domains_are_dependencies_not_silent_promotions` |
| Busca consolidada só descobre URLs citadas permitidas | Finance `test_discovery_consolidates_questions_filters_annotations_and_records_cost_without_stale_save` |
| Busca vazia e resposta incompleta | Finance `test_discovery_empty_queries_open_no_provider_and_partial_result_preserves_ledger` |
| Cancelamento preserva checkpoints e propaga | Runtime `test_cancellation_propagates_preserves_paid_checkpoints_and_can_resume`; finance `test_page_cancel_propagates_without_negative_cache_and_missing_cost_stays_unknown` |
| Reinício preserva sucesso anterior e saldo inconclusivo | Runtime `test_restart_recovery_preserves_completed_detection_and_uncertain_ledger` |
| Pedido concorrente não dispara segundo request | Runtime `test_same_request_concurrent_execution_returns_running_without_second_paid_dispatch` |
| Falha do shadow não veta exportação ou expõe corpo privado | Runtime `test_shadow_projection_failure_redacts_private_error_and_does_not_block_export` |

Contagem de chamadas e uso dos doubles é mensuração da fixture, não conta de provedor real. O total calculado por tokens não equivale a fatura. Custo de infraestrutura, latência de API real e custo humano continuam não observados.

O caso de renovação conserva cinco reservas simuladas no mesmo namespace: detecção/composição/validação iniciais e composição/validação após nova leitura. A detecção reutilizada não custa um novo despacho. A proposta antiga continua preservada, com apply recusado por expiração; o novo relatório inclui custos acumulados anteriores. Repetir novamente a análise válida não dispara mais requests. Isso verifica o fluxo e os tetos, sem medir preço real ou utilidade editorial.

## API, frontend e regressão

O endpoint ativo é autenticado, pinado ao hash do artigo e exige chave paga; a falta desses requisitos é recusada antes do despacho. Shadow não exige chave. Estado já em execução pode retornar HTTP 202 e conflito de versão HTTP 409. `IECRequest` recusa orçamento não finito, valores negativos, limites inválidos e destinos externos não autorizados antes de processar chamadas pagas.

Âncoras: runtime `test_api_rejects_invalid_opt_in_budget_before_provider`, `test_api_authentication_conflict_and_active_key_checks_are_nonbillable`, `test_authenticated_active_api_uses_explicit_budget_and_reports_nonblocking_suggestions`; contratos puros em `test_editorial_intelligence_core.py`.

`test_pipeline_off_shadow_exact_requests_checkpoints_costs_and_five_exports` compara requests reais preparados, fingerprints, consumo simulado, checkpoints e bytes dos cinco formatos entre IEC `off` e shadow. Esse teste é a evidência de que observação automática não altera as quatro entregas básicas nem introduz custo extra de IA.

A suíte Node em `frontend_consistency.test.cjs` cobre estado da nova interface, versões, orçamento, pesquisa explícita, respostas tardias/concorrentes, escaping e disponibilidade de exportação. Suites Python e Node anteriores continuam necessárias para detectar regressões fora da IEC. O build Docker valida o artefato executável; não comprova publicação efetiva em EasyPanel.

## Critérios de liberação e rollback

Antes de rollout, registrar sucesso de Python integral, Node/checks e Docker no commit entregue. Não aprovar liberação somente pelo número de testes. Manter versão, diff, prova de integridade do pacote e resultado dos casos comportamentais.

O padrão `EDITORIAL_INTELLIGENCE_MODE=shadow` permite observar sinais locais sem pagar ou alterar artigos. Para suspender, usar `off` e reiniciar o worker. Flag não apaga histórico, fontes, artigo aplicado ou ledger. Undo usa a versão exata aplicada; edições posteriores exigem recuperação deliberada, sem sobrescrita silenciosa.

Não há migração destrutiva nem backfill pago obrigatório. Backups de SQLite/WAL, mídia e chaves devem permanecer privados. Recuperação no startup pressupõe um único worker; expansão distribuída precisa ownership/lease e testes específicos. Desfazer código não equivale a desfazer dados ou cobranças.

## O que não foi verificado

- Qualidade editorial superior em artigo real, completude/fidelidade semântica de todos os assuntos ou ausência de falso positivo fora das fixtures.
- Notas humanas cegas, pares A/B reais, taxa editorial de aceitação/rejeição e satisfação dos leitores.
- Fatura, custo real por nova geração ou melhoria, economia real de tokens/API e latência/infrastrutura de produção.
- Deploy no ambiente público, atualização efetiva do serviço ou publicação WordPress real.
- Licenças e credibilidade substantiva de fontes específicas; HTTPS e lista de domínios não comprovam essas propriedades.

Essas pendências não são substituídas por testes sintéticos, opinião de agente ou promessa de tráfego SEO. Uma única fonte é suficiente para uso da plataforma; não existe exigência de doze vídeos para gerar ou analisar um artigo.


## Benchmark técnico pareado

O [script reproduzível](evidencias/p2-01/benchmark-editorial-intelligence.py) e o
[resultado](evidencias/p2-01/benchmark-editorial-intelligence.json) medem quatro pares sintéticos,
com três repetições por variante. O lado A executa off/shadow; o lado B usa a operação IEC,
SDK, ledger, prova e aplicação reais com cliente do provedor e transporte HTTP simulados.
Artigo completo: uma chamada de detecção; lacuna do vídeo: três; fonte externa manual: três
mais leitura HTTPS simulada; duplicação: duas, sem validação ou aplicação desnecessárias.

As respostas controladas declaram tokens simulados, resultando em US$ 0,00053, 0,00159,
0,00159 e 0,00106 respectivamente. Esses valores são cálculos do ledger sobre a fixture,
não custos observados de artigos reais. Tempo, CPU, alocações Python e crescimento do SQLite
estão no JSON; não equivalem a latência ou custo de infraestrutura em produção.

Material cego sintético, referências e formulário com notas nulas foram preparados em
`.local/p2-01-editorial-intelligence/benchmark-human-review`, com chave A/B privada separada.
A avaliação humana permanece pendente. A fonte histórica `cVnRvZ8uMCo` conserva seu hash e três
registros disponíveis, sem artigo real pareado ou comprovação de transcrição completa.
Não se declara melhoria editorial real a partir desses pares artificiais.


## Entrega Git

Implementação enviada na branch `feat/p2-01-editorial-intelligence`,
[PR #5](https://github.com/Vortex-BR/seo-master-premium/pull/5), baseado no P1_02.
O commit de código `c21071d21eec2b719eb895e51b1e0285b9d09f55` passou no [CI do código](https://github.com/Vortex-BR/seo-master-premium/actions/runs/38070988349): 1.242 testes Python,
73 Node, cinco checks JavaScript e Docker. Este recibo foi acrescentado em commit
de documentação; os checks do PR identificam o HEAD final e o manifesto permite
conferir os mesmos 144 arquivos de código. Não houve merge em `main` ou deploy.
