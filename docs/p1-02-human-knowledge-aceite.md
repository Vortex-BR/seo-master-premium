# P1_02 — aceite e evidências

Entrega implementada no checkout `feat/p1-02-human-knowledge-layer`, app **1.5.26**, a partir de `c8831d18af4c16121b070fe0a0479641cb218092` (P1_01, [PR #3](https://github.com/Vortex-BR/seo-master-premium/pull/3)). Editorial/agentes **7**, evidence flow **2**, modelos e schemas pagos preservados. O [documento técnico](p1-02-human-knowledge-layer.md) contém arquitetura, contratos, limitações e rollback. Não houve deploy.

O [recibo de validação](evidencias/p1-02/validation.json) identifica arquivos/hash do código, comando, resultado, versão, duração e cada caso do JUnit local. O [recibo do pacote](evidencias/p1-02/package-integrity.json) confirma os seis SHA256 contra o manifesto. Nenhum raw privado, banco ou credencial entra nas evidências públicas.

## Matriz comportamental

| Caso | Arquivo em `tests/` | Testes efetivamente executados | Resultado/observação |
|---|---|---|---|
| Experiência/opinião/hipótese | `test_human_knowledge.py` | `test_declared_legacy_kind_is_not_independent_truth; test_video_first_synthetic_fact_is_unknown_preserves_richer_structure` | Aprovado no comando direcionado. Natureza legada declarada; Video-First desconhecido; nenhuma prova independente inferida. |
| Condição, exceção e quantidade | `test_human_knowledge.py` | `test_conditions_exceptions_quantities_and_distant_relationship_survive` | Aprovado no comando direcionado. Campos originais conservados, sem universalizar condição ou atribuir experiência à marca. |
| Trechos distantes | `test_human_knowledge.py` | `test_cue_offsets_and_disjoint_intervals_are_resolved_without_envelope; test_no_relation_inferred_from_timestamp_or_order` | Aprovado no comando direcionado. Intervalos separados; relações existentes sem fabricar continuidade ou causa. |
| Autor, speaker e redator | `test_human_knowledge.py` | `test_source_author_does_not_become_speaker_identity` | Aprovado no comando direcionado. Canal/metadado não certifica locutor ou credencial; ausência explícita. |
| Um vídeo e invariância shadow | `test_human_knowledge_runtime.py` | `test_one_video_shadow_preserves_exact_paid_requests_checkpoints_costs_and_exports` | Aprovado no comando direcionado. Requests/schema/instruções/payloads, checkpoints, dependências, texto, ledger e cinco exports iguais em off/shadow. |
| Cache, versão e legado | `test_human_knowledge.py; test_human_knowledge_runtime.py` | `test_persistence_is_immutable_deduplicated_and_keeps_old_source_snapshot; test_shadow_resume_reuses_paid_cache_and_deduplicates_identical_sidecars` | Aprovado no comando direcionado. Identidade reutilizada; nova fonte produz novo snapshot, versões antigas permanecem recuperáveis. |
| Fonte histórica e ambiguidade | `test_human_knowledge.py; test_human_knowledge_cli.py` | `test_resolver_rejects_mismatched_snapshot_tampered_excerpt_and_unknown_version; test_historical_resolution_requires_matching_original_snapshot` | Aprovado no comando direcionado. Hash/version/job/offsets conferidos; snapshot explícito resgata fonte antiga; input original preservado. |
| Apuração stale e parcial | `test_human_knowledge.py` | `test_stale_apuration_not_marked_current_even_when_valid_flag_stays_true; test_partial_and_legacy_unknown_identity_are_explicit` | Aprovado no comando direcionado. Não confiar só em valid=True; ausência de dependências/extração registrada. |
| Cobertura e candidatos | `test_human_knowledge.py` | `test_literal_candidates_refer_to_source_offsets_not_summarized_text; test_missing_invalid_or_partial_timing_is_explicit` | Aprovado no comando direcionado. Contagens de ausência/tipo/candidatos; sinal lexical não promove classificação ou score de verdade. |
| Pesquisa e inferência | `test_human_knowledge.py` | `test_background_research_never_become_external_verified` | Aprovado no comando direcionado. Contexto interno não vira evidência externa verificada; nenhuma fonte criada. |
| Falha de diagnóstico | `test_human_knowledge_runtime.py` | `test_shadow_projection_failure_is_nonblocking_and_redacts_private_details` | Aprovado no comando direcionado. Texto/artigo/fluxo preservados; log contém só tipo de erro, sem dados privados. |
| Cancelamento e retry | `test_human_knowledge_runtime.py` | `test_shadow_observer_must_not_swallow_baseexception; test_shadow_retry_after_planner_error_does_not_rebuy_completed_extraction` | Aprovado no comando direcionado. Cancelamento propaga; retomada reaproveita extração paga concluída. |
| Conteúdo incompleto | `test_human_knowledge_runtime.py` | `test_incomplete_source_attempt_keeps_legacy_article_and_shadow_source_snapshot; test_default_shadow_observes_extract_only_without_paid_generation` | Aprovado no comando direcionado. Fonte disponível conservada e ausência explícita; nenhum artigo antigo descartado. |
| Export e WordPress | `test_human_knowledge_runtime.py; test_export_availability.py` | `test_shadow_unknown_locutor_and_missing_timing_do_not_block_pending_wordpress; test_shadow_review_on_legacy_article_keeps_saved_text_and_pending_notes` | Aprovado no comando direcionado. Notas/speaker desconhecido não bloqueiam; envio pending simulado em MockTransport; nenhuma publicação real. |
| Off, leitura e rollback | `test_human_knowledge.py; test_human_knowledge_runtime.py` | `test_off_does_not_project_or_write; test_legacy_article_reads_and_exports_never_backfill_or_require_hkl` | Aprovado no comando direcionado. Off desativa novas gravações sem apagar artifacts; GET/export de legado sem backfill. |
| CLI e preservação de input | `test_human_knowledge_cli.py` | `test_cli_refuses_to_overwrite_original_job_before_reading_it; test_cli_requires_unit_for_saved_dossier` | Aprovado no comando direcionado. Operação offline, banco/provedores vedados nos testes e overwrite dos inputs recusado. |

Todos os casos acima usam Python 3.12.10/Windows e o mesmo comando direcionado abaixo. São 70 casos HKL novos (52 núcleo, 13 integração, 5 CLI), mais regressões de fontes, contratos, contexto, exportação e custos, totalizando 186. Parametrizações e tempos individuais constam de `local_python_targeted.cases` no recibo. Os testes reutilizam códigos de request, ledger, checkpoint, API e exportação reais; somente os provedores, transporte HTTP e dados são fixtures isoladas.

O cenário principal compara requests montados por `generation.prepare_structured`, incluindo schema/instrução/payload completo, quatro etapas pagas simuladas (extractor/planner/writer/fact_reviewer), ledger não nulo e cinco formatos byte a byte (Markdown, HTML, JSON, WordPress e WordPress HTML). Ele confirma nenhum acesso ao cliente pago. Isso comprova invariância no cenário controlado; não é fatura ou avaliação humana de artigo real.

## Comandos e resultados

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_human_knowledge.py tests/test_human_knowledge_runtime.py tests/test_human_knowledge_cli.py tests/test_source_provenance.py tests/test_source_legacy_cycle.py tests/test_context_packing.py tests/test_reference_contracts.py tests/test_export_availability.py tests/test_p101_runtime_costs.py --junitxml=.local/p1-02-human-knowledge/pytest-targeted.xml
node --check app/static/app.js
node --check app/static/editorial.js
node --check app/static/planning.js
node --check app/static/publishing.js
node --check app/static/transcription.js
node --test tests/frontend_export.test.cjs tests/frontend_sources.test.cjs tests/frontend_consistency.test.cjs tests/frontend_costs.test.cjs
.venv/Scripts/python.exe docs/evidencias/p1-02/benchmark-human-knowledge.py --pilot-job .local/premium-inputs/cVnRvZ8uMCo-sources.json
.venv/Scripts/python.exe scripts/human_knowledge.py --job-json .local/premium-inputs/cVnRvZ8uMCo-sources.json --output .local/p1-02-human-knowledge/pilot-cli-projection.json
```

| Evidência | Resultado confirmado |
|---|---|
| Pacote | Seis hashes correspondem ao manifesto |
| Diagnóstico de contratos/payloads/consumidores | Concluído antes do schema novo, documentado no mapa técnico |
| Python direcionado | 186 aprovados, zero falhas/erros/skips; 70 HKL novos |
| Node local | 58 aprovados e cinco verificações de sintaxe; Node 24.11.1 |
| Suíte Python completa | Pendente de CI do código desta entrega; 1.041 coletados localmente |
| Docker build | Pendente de CI; Docker indisponível localmente |
| Piloto de uma fonte | Três segmentos originais recuperados, input/hash preservados; sem geração de artigo |
| Benchmark | Três casos, sete repetições por caso; CPU/tempo/alocações/storage no recibo |
| SHA/PR/CI final | Pendente de envio e validação do HEAD, sem reutilizar aceite de P1_01 |
| Produção e WordPress real | Não acessados nem modificados |

O workflow `.github/workflows/ci.yml` executa `python -m pytest -q`, cinco checks de sintaxe, os 58 testes Node e `docker build -t seo-master-premium:ci .` em Python 3.12/Node 22/Linux. O recibo final deve vincular os resultados ao SHA efetivamente testado, e os checks do PR precisam passar no HEAD final.

## Custo e piloto disponível

O [benchmark reproduzível](evidencias/p1-02/benchmark-human-knowledge.py) e suas [medidas](evidencias/p1-02/benchmark-human-knowledge.json) registram somente projeção/persistência local: fonte real histórica de três segmentos e fixtures sintéticas de 100/1.000 segmentos. A persistência usa SQLite temporário; DB de aplicação/prod não é aberto. A repetição mantém exatamente uma linha por artefato/identidade e não acrescenta alocação ao DB no ensaio, embora recalcule a projeção e use CPU. Não há preço de infraestrutura disponível.

O [recibo de recuperação](evidencias/p1-02/pilot-source-recovery.json) registra o vídeo próprio `cVnRvZ8uMCo`: três segmentos, 2.651 caracteres, hashes do input antes/depois iguais e os três originais recuperados. A ferramenta offline foi executada com esse input sem banco/provedor. Esses dados locais não possuem apuração/artigo atuais: tipos `unknown`, locutor não identificado, sem completude de transcrição verificada. Nenhum vídeo adicional foi exigido. A prova é recuperação de bytes/referências disponíveis, não conformidade semântica de um artigo com o vídeo completo.

Custo incremental automático de API no shadow: **zero novas operações/tokens**, ver comparação efetiva de requests/ledger e eventos locais `human_knowledge_shadow`. CPU/storage continuam com custo monetário desconhecido. Custo por artigo real, qualidade humana, benefício SEO e economia real não foram observados. Não houve geração paga, pesquisa, imagem ou transcrição nova.

## Limites de aceite e operação

Aceite de engenharia offline é sustentado pelos casos e medidas acima; a regressão completa/build aguardam CI. Aceite humano editorial e liberação de produção permanecem não verificados. Rollback funcional é `HUMAN_KNOWLEDGE_MODE=off`: desativa novos sidecars, conserva dados/artigos e não muda fingerprints pagos. Não há migração ou backfill obrigatório. Backups consistentes são requisito de um deploy futuro autorizado, não foram executados contra produção nesta etapa.

Os prompts pagos continuam intactos. O `kind='fato'` sintético do Video-First/supported_claims e a expressão “especialista” na composição são riscos residuais do fluxo anterior: a HKL não os usa como comprovação semântica/credencial. As unidades estruturadas têm origem/classificação e ausência explícitas, mas classificação avançada permanece futura. Não existe novo agente obrigatório, gate de palavra/score, seção extra ou bloqueio de exportação.
