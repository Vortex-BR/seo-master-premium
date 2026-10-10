# P0_02 — Aceite e evidências

## Método e runtime

Testes offline sobre código e persistência reais, com transporte de provedor/WordPress isolado. Python 3.12.10, OpenAI SDK 3.24.0, Pydantic 2.13.5; Node local 24.11.1. CI usa Python 3.12 e Node 22, como Docker. Nenhuma chamada paga nem escrita em produção. As sondas usam `TemporaryDirectory` como DATA_DIR; não usam o banco da implantação.

Todos os comandos abaixo partem da raiz do checkout. Em Linux/CI, substitua `.venv/Scripts/python.exe` por `python`. Os resultados finais resumidos são publicados em `docs/evidencias/p0-02/validation.json`; logs completos e JUnit locais ficam em `.local/p0-02-consistencia/`.

## Cenários vinculados às correções

| Aceite | Teste/arquivo | Evidência comportamental |
| --- | --- | --- |
| Strict do SDK | `test_strategy_contract_integrity.py`: requests estratégicos e schemas editoriais dinâmicos | Conversão real do SDK; objetos fechados/required; nenhum cliente de provedor aberto. |
| Dados ausentes | Mesmo arquivo: métricas, zero, aliases e valores inválidos | Ausência permanece null; zero preservado; inválidos recusados. |
| Duplo clique/retry | `test_strategy_idempotency.py::test_double_click_production_reuses_one_job` | Mesmo job, um enqueue simulado. |
| Concorrência | `test_concurrent_commands_reserve_exactly_one_intent_and_job` | Oito threads com barreira, uma factory/job/intenção. |
| Estados/ações | Testes parametrizados no mesmo arquivo e `test_strategy_production_api.py` | Rejeitada/cancelada/proposta e ações incompatíveis não despacham geração. |
| Decisão humana | `test_resynthesis_preserves_human_rejection`, `test_ai_plan_cannot_approve_its_own_new_opportunity`, decisão/produção concorrentes | IA não aprova; rejeição e conteúdo/histórico aprovados não se perdem. |
| IDs | IDs entre projetos/ciclos, estabilidade na ressíntese, batch repetido, colisão de job | Não há overwrite entre escopos nem persistência parcial de lote ambíguo. |
| Fila/recuperação | Fila concorrente, fábrica falha, legacy link ausente, retry em estados diversos | Capacidade respeitada; mesma intenção reaproveitada sem execução implícita. |
| Edição em abas | `test_architecture_api.py`: abas sequenciais e threads simultâneas | Um save aceito, outro 409; versão salva e revisão anterior preservadas. |
| Atomicidade | `test_edit_rolls_back_revision_and_review_if_save_fails` | Falha de save desfaz job, revisão e artefato. |
| Versão base | ETag/If-Match, ausência, hash divergente | 200/409/428/400 coerentes, sem alteração silenciosa. |
| Polling e rascunho | `frontend_consistency.test.cjs` | Handlers reais com respostas invertidas; save/reload e falha de geração preservam digitação local. |
| Formulários | Mesmo harness: briefing, fontes, perfil, planejamento e imagens | Digitação posterior e alterações em outro formulário não são descartadas. |
| Cache | `test_strategy_cache_identity.py` | Modelo/prompt/schema/config/contexto alteram identidade; legado/incompleto recusa retomada antes de chamadas. |
| Modelo durante envio | `test_wire_cache_identity_and_usage_share_the_model_frozen_before_send` | Modelo muda na entrada do transporte; wire, identidade e recibo conservam o modelo preparado. |
| Orçamento de síntese | `test_synthesis_attempt_budget_is_durable_before_dispatch_and_after_failure` | Contagem persistida antes do envio; retry após falha não ultrapassa tentativas. |
| GET puro | Snapshot completo antes/depois de detalhe, estimativa, equipe e perfil | Nenhuma tabela/row nova; custo histórico continua visível. |
| Briefing completo | `test_production_brief_retains_curation_and_has_no_forced_length` | Curadoria/limites transportados como dados; nenhuma evidência factual inventada. |
| Um vídeo | `test_source_driven_brief_completes_single_video_pipeline` | Fluxo inteiro com fonte única e meta null, sem chamadas reais. |
| Legado/rollback | `test_additive_legacy_migration_preserves_payloads_and_can_be_repeated` | JSON legado idêntico, sete colunas, INSERT antigo funciona; SQLite backup/restore conferido. |
| WordPress | `test_export_availability.py`, `test_publishing.py` | Pending, reconciliação após falha, colisão de slug, hashes e proteção de edição externa. |
| Exportação | `test_saved_concise_article_exports_with_any_editorial_state` e regressões de rascunho | Advertência/revisão pendente não bloqueia artigo tecnicamente válido. |
| P0_01 | `test_source_integrity.py`, fontes/transcrição e `frontend_sources.test.cjs` | Proveniência, tempos desconhecidos e conteúdo técnico preservados. |

## Comandos reproduzíveis

```powershell
.venv/Scripts/python.exe -m pytest -q
node --test tests/frontend_export.test.cjs tests/frontend_sources.test.cjs tests/frontend_consistency.test.cjs
node --check app/static/app.js
node --check app/static/transcription.js
node --check app/static/editorial.js
node --check app/static/planning.js
node --check app/static/publishing.js
.venv/Scripts/python.exe -m compileall -q app
git diff --check
```

Foco de aceite, se houver alteração posterior que justifique nova execução:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_architecture_api.py tests/test_strategy_idempotency.py tests/test_strategy_production_api.py tests/test_strategy_cache_identity.py tests/test_strategy_contract_integrity.py tests/test_strategy.py tests/test_spending.py tests/test_export_availability.py tests/test_publishing.py
```

Sondas opcionais, sem API/dispatch, em bancos temporários; escrevem apenas o respectivo JSON de evidência:

```powershell
.venv/Scripts/python.exe docs/evidencias/p0-02/strict-cache-probe.py
.venv/Scripts/python.exe docs/evidencias/p0-02/measurement-storage-runtime.py
```

A sonda de baseline usa `git show aedd740:app/strategy/contracts.py`; necessita desse commit no histórico local. Mede bytes e runtime local, sem inferir tokens, custos do provedor ou latência de produção. A sonda SQLite registra crescimento de payload e páginas separadamente.

## Resultados e limitações

O diagnóstico RED inicial está preservado nos JUnit/logs locais: seis falhas de API, quatro de estratégia e sete cenários iniciais de frontend. Casos adicionais de cache/modelo, GET equipe e digitação durante POST foram reproduzidos na revisão independente e receberam testes de regressão.

As validações finais abrangem os casos de sucesso, falha, interrupção, retry, conteúdo incompleto, legado e concorrência. O JSON de validação registra as contagens e tempos realmente observados. Duas reproduções redundantes de colisão foram consolidadas nos testes existentes antes da entrega; a contagem final do CI corresponde aos arquivos entregues.

Docker não está disponível neste Windows. O build completo `docker build -t seo-master-premium:ci .` é executado pelo workflow Quality checks no PR, junto com a suíte Python, JS e sintaxe. O estado autoritativo do build e seu SHA são os checks do PR; não foi feito build/deploy no servidor de produção.

Não foram executados testes reais de OpenAI/Supadata/YouTube/WordPress nem navegador visual. Essas limitações não são apresentadas como validação de produção. Migração/restauração e custo medido referem-se somente aos bancos temporários. O relatório de arquitetura lista riscos restantes e compatibilidade do rollback.
