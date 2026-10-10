# P1_01 — aceite e evidências

Checkout de partida: `b366b4da2ef063371ea6391459208cefc57da22f`. Runtime local: Windows, Python 3.12.10, SDK OpenAI 3.24.0, SQLite 3.49.1, Node 24.11.1. CI: Python 3.12, Node 22 e build Docker. Resultado final, hashes dos arquivos e referência de CI serão registrados em `evidencias/p1-01/validation.json` antes da entrega.

| Cenário do plano | Evidência reproduzível | Resultado e limite |
|---|---|---|
| Execução limpa separada do histórico | `test_cost_observability.py::test_two_generations_same_article_and_retries_have_distinct_costs`; `test_p101_runtime_costs.py::test_two_pipeline_invocations_separate_runs_and_preserve_lifetime_spend` | Runs distintos, retry identificado e guarda USD acumulada. Provedor simulado. |
| Timeout e falha | `test_spending.py`, `test_cost_observability.py`, `test_spending_images.py`, `test_spending_transcription.py` | Reserva conservada como incerta; nenhuma certificação de fatura. |
| Cancelamento e restart | `test_cost_observability.py`; testes `test_cancelled_*` de imagem/áudio; `test_p101_reporting_integrity.py::test_recovery_skips_non_object_json_and_preserves_raw_evidence` | Interrupção propagada, saldo retido, recuperação idempotente; JSON incompleto não derruba startup. |
| Uso ausente ou parcial | `test_cost_observability.py`, `test_p1_baseline_costs.py`, `test_p101_runtime_costs.py` | Tokens desconhecidos nulos; custo completo não é inferido de subtotal. |
| Teste OpenAI fora do artigo | `test_p101_runtime_costs.py::test_connection_test_is_bounded_observed_and_outside_article`; `...::test_connection_guard_refuses_before_dispatch_and_unknown_usage_holds_reserve` | Escopo próprio, orçamento antes de envio e reserva incerta quando falta uso. Nenhuma chamada real. |
| Limites estratégicos | `test_p101_strategy_budget.py` | Guardas de calls/tokens/USD antes do despacho; concorrência, falha, resume, cache, save obsoleto e legado sem counters. Pesquisa/lookups explicitamente indisponíveis. |
| Readonly DB/WAL/SHM | `test_cost_observability.py`, `test_p1_baseline_costs.py`; `test_p101_runtime_costs.py::test_authenticated_reports_preserve_database_wal_and_shm` | Bytes preservados inclusive pela rota autenticada; ausência de ledger não dispara criação. |
| Cache | `test_cost_observability.py` e testes de fonte/cache da suíte geral | Reutilização de aplicação separada de tokens em cache do fornecedor; cobertura parcial não vira total. |
| UI áudio/imagem/parcial | `frontend_costs.test.cjs` | Renderização real das abas Histórico/Equipe e controles; ausência, zero conhecido e subtotal distinguíveis, sem NaN. |
| Baseline com um vídeo | `test_p1_baseline_costs.py`; `baseline-{manifest,result,human-review}.json` | Piloto autorizado com um vídeo; geração/modelo efetivo/custo/tempo/avaliação humana pendentes. |
| Histórico e percentis | `test_p101_reporting_integrity.py`; `test_cost_observability.py` | Estratégia e ledger legado preservam vínculo SQL. Geração/revisão não misturadas. N pequeno produz percentis nulos. |
| Preço congelado e estimativa inferior | `test_cost_observability.py`; `test_p101_reporting_integrity.py::test_measured_provider_duration_cannot_be_undercharged_by_local_estimate` | Repricing posterior não muda tarifa da tentativa. Guarda nunca fica abaixo do cálculo conhecido com margem. |
| Reconciliação | `test_cost_observability.py` | Referência de evidência e responsável obrigatórios; não baixar reserva por timeout ou ausência de tokens. |
| Relatório desativado/autenticação | `test_p101_runtime_costs.py::test_report_routes_keep_auth_and_can_disable_without_budget_loss` | Sem sessão não há acesso; desativação do relatório mantém o ledger e saldo. |
| Exportação e WordPress | Suíte geral: testes de exportação, política de revisão, publishing e WordPress; `frontend_export.test.cjs` | Observações editoriais não passam a bloquear artigo válido; `pending`, HTTPS e reconciliação preservados. Validação simulada. |

Comandos na raiz:

```powershell
.venv/Scripts/python.exe -m pytest -q
node --check app/static/app.js
node --check app/static/editorial.js
node --check app/static/planning.js
node --check app/static/publishing.js
node --check app/static/transcription.js
node --test tests/frontend_export.test.cjs tests/frontend_sources.test.cjs tests/frontend_consistency.test.cjs tests/frontend_costs.test.cjs
```

Docker não está instalado neste workstation; o build é executado pelo workflow GitHub Actions. As evidências finais distinguem validação local de CI e registram o SHA testado. Não tratar um teste passado em revisão intermediária como teste da árvore final.

## Medição do trabalho novo

`benchmark-observability.py` e seu JSON medem somente a instrumentação/relatório local, usando SQLite WAL isolado, 20 e 100 execuções sintéticas, sete repetições, tempo de parede, CPU, alocação Python, bytes de DB/WAL/JSON e hashes. Custos sintéticos exercitam o contrato; não são valores pagos ou custo por artigo. Os arquivos originais da fixture permanecem byte a byte iguais.

`package-integrity.json` identifica os seis documentos e seus SHA256. `baseline-result.json` identifica os bytes do checkout e as pendências atuais. Conteúdo da transcrição, snapshots completos de jobs, credenciais, banco de produção e logs privados não integram estes artefatos públicos.

## Classificação das afirmações

- **Confirmado pelo código:** reservas duráveis, snapshots de tarifa, controles configuráveis, contexto por worker, instrumentos offline e hooks dos escopos descritos.
- **Reproduzido:** guardas, concorrência, retomada, cancelamento, leitura sem escrita, renderização de uso incompleto e cálculos com fixtures.
- **Inferido:** capacidade de reutilizar estes componentes na Inteligência Editorial Crítica; depende da implementação e avaliação futuras.
- **Não verificado:** deploy desta versão, operação real da produção, cobranças efetivas, contratos externos, fidelidade integral da fonte histórica, artigo atual, avaliação humana, benefício SEO e economia por geração.

O aceite de engenharia não equivale ao aceite editorial do piloto nem à aprovação de deploy. Não foi executada geração paga ou publicação real.
