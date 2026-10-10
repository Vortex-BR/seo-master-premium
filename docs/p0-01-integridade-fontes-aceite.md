# P0_01 — Evidências de aceite

Python 3.12.10 / pytest 9.1.1: **788 testes passaram**, sem falhas, erros ou skips; 127 casos novos. Execução local completa em quatro processos isolados: 254.225 segundos. JavaScript: **17 testes passaram** e cinco arquivos tiveram sintaxe validada.

O comando padrão é `.venv/Scripts/python.exe -m pytest -q`. Nesta máquina, a suíte final foi particionada por arquivo em quatro processos, com bancos temporários separados, pelo script local `.local/p0-01-integridade/run_full_suite.py`. Os quatro JUnit e logs estão em `.local/p0-01-integridade/full-final/`. A versão inicial da execução sequencial foi interrompida durante correções reais; ela não compõe este resultado.

A regressão RED anterior às mudanças reproduziu quatro falhas no commit `2ae42d821087631b2c6e2218c4f6b970d69bb737`. A suíte específica final de 61 casos passou em 18,82 s de pytest. O registro sanitizado inclui cada caso, a versão e as partições: [aceite.json](evidencias/p0-01/aceite.json).

## Construção e publicação do código

Docker não está disponível nesta estação. A imagem é validada pelo workflow de PR `.github/workflows/ci.yml`, que repete Python/JavaScript e executa `docker build -t seo-master-premium:ci .` em runner isolado. O resultado do build e o commit correspondente são apresentados nos checks do PR da branch `feat/p0-01-source-integrity`. Produção não foi implantada nesta execução.

## Medição local

Amostra sintética: 700 cues, 93.800 caracteres de fala, 100 segmentos nas duas versões. Sete repetições; comparação com a função de normalização isolada do baseline auditado.

| Métrica | Baseline | P0_01 |
| --- | ---: | ---: |
| Mediana de tempo local (ms) | 1.145 | 10.677 |
| CPU acumulada em 100 normalizações (ms) | 125.0 | 1203.125 |
| JSON da fonte compactado em UTF-8 (bytes) | 103523 | 458477 |
| JSON projetado dos vídeos para extração (bytes) | 103448 | 122940 |
| Pico de alocação Python, tracemalloc (bytes) | 112312 | 602335 |

A projeção evita enviar o JSON bruto repetido, mas acrescenta 18,8% de bytes nesta amostra em relação ao contrato anterior por transportar metadados e referências. O armazenamento cresce para preservar as cues originais. Esta medição não representa tokens faturáveis, fatura, volume total do SQLite, RSS completo ou desempenho em produção. O timer de CPU do Windows tem granularidade limitada; por isso também foi medido um lote de 100 chamadas.

[Dados e limitações do benchmark](evidencias/p0-01/benchmark.json). [Script reproduzível](evidencias/p0-01/benchmark_integrity.py). No checkout com o commit baseline disponível, execute `.venv/Scripts/python.exe docs/evidencias/p0-01/benchmark_integrity.py`.

## Casos novos individualizados

Todos os casos abaixo integram a regressão completa. Para repetir um arquivo, use `.venv/Scripts/python.exe -m pytest tests/arquivo.py -q --tb=short`. O nome de cada caso corresponde ao node ID do pytest; os parâmetros são preservados. Os tempos são os registrados no JUnit da execução paralela, com concorrência local.

| Arquivo | Caso | Resultado | Tempo (s) |
| --- | --- | --- | ---: |
| `tests/test_manual_source_integrity.py` | <code>test_malformed_caption_cannot_archive_or_mutate_saved_work</code> | PASS | 1.645 |
| `tests/test_manual_source_integrity.py` | <code>test_manual_replacement_preserves_old_article_and_original_source_history</code> | PASS | 1.669 |
| `tests/test_source_integrity.py` | <code>test_aggregation_flag_only_disables_grouping_and_keeps_integrity_fixes</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_ai_context_keeps_resolvable_ids_without_duplicating_raw_cue_payload</code> | PASS | 2.243 |
| `tests/test_source_integrity.py` | <code>test_ambiguous_or_internal_references_do_not_resolve_as_article_evidence</code> | PASS | 1.971 |
| `tests/test_source_integrity.py` | <code>test_cache_selection_respects_normalizer_option_without_mutating_saved_data</code> | PASS | 2.141 |
| `tests/test_source_integrity.py` | <code>test_current_source_cache_rebases_every_cue_reference_and_preserves_original_job</code> | PASS | 1.984 |
| `tests/test_source_integrity.py` | <code>test_distant_cues_do_not_become_one_continuous_evidence_interval</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_editorial_cleaning_keeps_raw_originals_and_resolvable_times</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_explicit_vtt_voice_is_preserved_without_inferring_channel_authorship[&lt;v Ana&gt;{body}&lt;/v&gt;]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_explicit_vtt_voice_is_preserved_without_inferring_channel_authorship[&lt;v.person Ana&gt;&lt;c.red&gt;{body}&lt;/c&gt;&lt;/v&gt;]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_incomplete_raw_text_markup_does_not_discard_remaining_spoken_text[script]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_incomplete_raw_text_markup_does_not_discard_remaining_spoken_text[style]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[00:00:10,000-]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[00:00:10,000-broken]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[00:00:25,000-00:00:10,000]</code> | PASS | 0.009 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[00:00:70,000-00:01:20,000]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[00:60:10,000-01:01:10,000]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_invalid_manual_cue_is_explicitly_rejected[broken-00:00:25,000]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_legacy_cache_remains_readable_but_is_not_reused_as_current_integrity</code> | PASS | 1.751 |
| `tests/test_source_integrity.py` | <code>test_legacy_source_and_editorial_warning_still_export_without_regeneration[html]</code> | PASS | 1.571 |
| `tests/test_source_integrity.py` | <code>test_legacy_source_and_editorial_warning_still_export_without_regeneration[json]</code> | PASS | 2.112 |
| `tests/test_source_integrity.py` | <code>test_legacy_source_and_editorial_warning_still_export_without_regeneration[markdown]</code> | PASS | 1.649 |
| `tests/test_source_integrity.py` | <code>test_legacy_source_and_editorial_warning_still_export_without_regeneration[wordpress-html]</code> | PASS | 1.312 |
| `tests/test_source_integrity.py` | <code>test_legacy_source_and_editorial_warning_still_export_without_regeneration[wordpress]</code> | PASS | 1.976 |
| `tests/test_source_integrity.py` | <code>test_manual_caption_variants_keep_valid_times[1\n01:02:03,004 --&gt; 01:02:07,500\n{body}\n-3723.004-3727.5]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_manual_caption_variants_keep_valid_times[WEBVTT - teste\r\n\r\n00:00:10.250 --&gt; 00:00:25.750\r\n{body}\r\n-10.25-25.75]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_manual_caption_variants_keep_valid_times[WEBVTT\n\nintro\n00:10.250 --&gt; 00:25.750 align:start position:10%\n{body}\n-10.25-25.75]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_manual_caption_variants_keep_valid_times[\ufeff1\r\n00:00:10,250 --&gt; 00:00:25,750\r\n{body}\r\n-10.25-25.75]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_manual_srt_retains_cue_end_and_duration</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_mathematical_comparison_survives_source_normalization</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_mixed_valid_and_invalid_cues_do_not_silently_drop_evidence</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_original_cue_references_resolve_to_their_own_interval</code> | PASS | 1.661 |
| `tests/test_source_integrity.py` | <code>test_orphan_spoken_text_in_caption_document_is_preserved_as_untimed_evidence</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_overlapping_warning_is_bound_without_asserting_factual_error</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_overlaps_and_zero_duration_stay_observable[15-30-overlapping_cues]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_overlaps_and_zero_duration_stay_observable[30-30-zero_duration]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_partial_and_multiple_voice_annotations_do_not_fabricate_a_single_speaker[&lt;v Ana&gt;{body}-Ana-None]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_partial_and_multiple_voice_annotations_do_not_fabricate_a_single_speaker[&lt;v Ana&gt;{body}&lt;/v&gt; &lt;v Bruno&gt;Outra contribui\xe7\xe3o.&lt;/v&gt;-None-multiple_speakers]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_partial_and_multiple_voice_annotations_do_not_fabricate_a_single_speaker[&lt;v Ana&gt;{body}&lt;/v&gt; A pr\xf3xima instru\xe7\xe3o n\xe3o identifica quem falou.-None-partial_speaker_attribution]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_plain_engineering_diagram_arrow_is_not_misclassified_as_bad_caption</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_plain_manual_text_never_receives_invented_timestamps</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_retry_reuses_current_integrity_source_without_network_or_paid_generation</code> | PASS | 2.387 |
| `tests/test_source_integrity.py` | <code>test_source_math_stays_visible_and_markup_cannot_execute_in_exports[False]</code> | PASS | 1.538 |
| `tests/test_source_integrity.py` | <code>test_source_math_stays_visible_and_markup_cannot_execute_in_exports[True]</code> | PASS | 1.876 |
| `tests/test_source_integrity.py` | <code>test_speaker_and_confidence_changes_are_not_erased_by_aggregation</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_supadata_metadata_survives_millisecond_conversion_and_normalization</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_supadata_unknown_duration_does_not_become_a_confirmed_zero_length_interval</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_unknown_speaker_and_confidence_are_never_inferred_from_creator</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_unknown_technical_markup_retains_case_and_remains_plain_text</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_unknown_time_warning_remains_global_without_inventing_intervals</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_untimed_legacy_reference_resolves_without_fabricating_provenance</code> | PASS | 1.797 |
| `tests/test_source_integrity.py` | <code>test_vtt_metadata_blocks_do_not_become_spoken_evidence</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_in_internal_gap_does_not_claim_support_from_outer_interval</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning0-segments0-expected0]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning1-segments1-expected1]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning2-segments2-expected2]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning3-segments3-expected3]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning4-segments4-expected4]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning5-segments5-expected5]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning6-segments6-expected6]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning7-segments7-expected7]</code> | PASS | 0.001 |
| `tests/test_source_integrity.py` | <code>test_warning_interval_boundaries_are_deterministic[warning8-segments8-expected8]</code> | PASS | 0.001 |
| `tests/test_source_legacy_cycle.py` | <code>test_legacy_plan_continues_same_cycle_and_frozen_dependencies</code> | PASS | 1.234 |
| `tests/test_source_legacy_cycle.py` | <code>test_other_catalog_changes_do_not_bypass_existing_invalidation[existing_rule]</code> | PASS | 1.382 |
| `tests/test_source_legacy_cycle.py` | <code>test_other_catalog_changes_do_not_bypass_existing_invalidation[extra_rule]</code> | PASS | 1.176 |
| `tests/test_source_legacy_cycle.py` | <code>test_other_catalog_changes_do_not_bypass_existing_invalidation[metadata]</code> | PASS | 1.169 |
| `tests/test_source_legacy_cycle.py` | <code>test_other_catalog_changes_do_not_bypass_existing_invalidation[missing_catalog]</code> | PASS | 1.279 |
| `tests/test_source_provenance.py` | <code>test_cache_references_rebase_but_provider_and_origin_records_stay_literal</code> | PASS | 1.244 |
| `tests/test_source_provenance.py` | <code>test_cache_same_prefix_and_skipped_cue_indices_do_not_remap_ownership_twice</code> | PASS | 1.292 |
| `tests/test_source_provenance.py` | <code>test_every_nested_context_path_omits_raw_cue_copies</code> | PASS | 1.464 |
| `tests/test_source_provenance.py` | <code>test_incomplete_provenance_never_claims_original_cues_available[None]</code> | PASS | 1.572 |
| `tests/test_source_provenance.py` | <code>test_incomplete_provenance_never_claims_original_cues_available[originals1]</code> | PASS | 1.201 |
| `tests/test_source_provenance.py` | <code>test_incomplete_provenance_never_claims_original_cues_available[originals2]</code> | PASS | 1.420 |
| `tests/test_source_provenance.py` | <code>test_inspecting_original_evidence_does_not_mutate_source</code> | PASS | 1.778 |
| `tests/test_source_provenance.py` | <code>test_legacy_evidence_map_keeps_exact_fingerprint_shape</code> | PASS | 1.789 |
| `tests/test_source_provenance.py` | <code>test_legacy_provider_markers_survive_new_normalization_and_reference_resolution</code> | PASS | 1.860 |
| `tests/test_source_provenance.py` | <code>test_legacy_research_provider_request_does_not_repeat_original_cues</code> | PASS | 1.430 |
| `tests/test_source_provenance.py` | <code>test_resolver_rejects_ambiguous_and_internal_ids</code> | PASS | 1.740 |
| `tests/test_source_provenance.py` | <code>test_strategy_provider_request_uses_the_same_compact_provenance_boundary</code> | PASS | 1.594 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning0]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning1]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning2]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning3]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning4]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_unlocalizable_warning_is_preserved_globally[warning5]</code> | PASS | 0.001 |
| `tests/test_source_provenance.py` | <code>test_warning_cannot_land_in_gap_of_explicit_original_intervals</code> | PASS | 0.001 |
| `tests/test_source_timestamps.py` | <code>test_absent_or_other_paragraph_citation_is_unresolved[\n\n[[v1s1]]]</code> | PASS | 0.006 |
| `tests/test_source_timestamps.py` | <code>test_absent_or_other_paragraph_citation_is_unresolved[]</code> | PASS | 0.003 |
| `tests/test_source_timestamps.py` | <code>test_alignment_does_not_claim_semantic_or_visual_verification</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_ambiguous_segment_id_cannot_certify_article_time</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_inline_code_is_not_temporal_evidence[[03:45]-`[[v1s1]]`]</code> | PASS | 0.004 |
| `tests/test_source_timestamps.py` | <code>test_inline_code_is_not_temporal_evidence[`[03:45]`-[[v1s1]]]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_internal_only_material_cannot_certify_article_time[segment]</code> | PASS | 0.003 |
| `tests/test_source_timestamps.py` | <code>test_internal_only_material_cannot_certify_article_time[source]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_invalid_legacy_interval_metadata_is_uncertain[intervals0]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_invalid_legacy_interval_metadata_is_uncertain[intervals1]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_invalid_legacy_interval_metadata_is_uncertain[intervals2]</code> | PASS | 0.001 |
| `tests/test_source_timestamps.py` | <code>test_legacy_non_video_first_mode_is_unchanged</code> | PASS | 0.001 |
| `tests/test_source_timestamps.py` | <code>test_original_intervals_prevent_certifying_a_gap</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_positive_interval_end_is_exclusive</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_supported_time_accepts_integer_locators_and_points[[03:45]-225-225]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_supported_time_accepts_integer_locators_and_points[[03:45]-225-230]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_supported_time_accepts_integer_locators_and_points[[03:45]-225.95-230]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_supported_time_accepts_integer_locators_and_points[[1:03:45]-3825-3830]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_timestamp_must_match_the_cited_video</code> | PASS | 0.004 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[225-None]</code> | PASS | 0.003 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[230-225]</code> | PASS | 0.004 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[None-230]</code> | PASS | 0.002 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[None-None]</code> | PASS | 0.010 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[True-230]</code> | PASS | 0.004 |
| `tests/test_source_timestamps.py` | <code>test_unknown_or_invalid_legacy_times_warn_without_false_certainty[nan-230]</code> | PASS | 0.003 |
| `tests/test_source_timestamps.py` | <code>test_valid_syntax_does_not_certify_time_and_never_blocks_export</code> | PASS | 0.018 |
| `tests/test_supadata_cache_integrity.py` | <code>test_current_paid_cache_retains_true_zero_duration_and_provider_metadata</code> | PASS | 0.630 |
| `tests/test_supadata_cache_integrity.py` | <code>test_legacy_cache_provenance_reaches_new_source_without_inference_or_network</code> | PASS | 0.626 |
| `tests/test_supadata_cache_integrity.py` | <code>test_legacy_paid_cache_keeps_text_and_positive_times_but_exposes_ambiguous_zero</code> | PASS | 0.621 |
| `tests/test_supadata_cache_integrity.py` | <code>test_pending_old_ticket_is_polled_once_without_a_new_paid_submission</code> | PASS | 0.736 |
| `tests/test_transcript_math_markup.py` | <code>test_ambiguous_known_tag_with_invalid_attributes_keeps_its_literal_closing</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_standard_html_markup_remains_discriminated_and_does_not_join_words[&lt;b class=&quot;example&quot; title=&quot;passo&quot;&gt;O procedimento&lt;/b&gt; mant\xe9m a condi\xe7\xe3o.-O procedimento mant\xe9m a condi\xe7\xe3o.]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_standard_html_markup_remains_discriminated_and_does_not_join_words[&lt;b&gt;O procedimento&lt;/b&gt; mant\xe9m a condi\xe7\xe3o.-O procedimento mant\xe9m a condi\xe7\xe3o.]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_standard_html_markup_remains_discriminated_and_does_not_join_words[&lt;img src=&quot;x&quot; onerror=&quot;alert(1)&quot;&gt;A condi\xe7\xe3o continua dispon\xedvel.-A condi\xe7\xe3o continua dispon\xedvel.]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_standard_html_markup_remains_discriminated_and_does_not_join_words[&lt;input disabled&gt;A condi\xe7\xe3o continua dispon\xedvel.-A condi\xe7\xe3o continua dispon\xedvel.]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_standard_html_markup_remains_discriminated_and_does_not_join_words[&lt;p&gt;Primeira etapa.&lt;/p&gt;&lt;p&gt;Segunda etapa.&lt;/p&gt;-Primeira etapa. Segunda etapa.]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_unspaced_math_using_an_html_tag_name_remains_literal[2 &lt;b &amp;&amp; b&gt;1]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_unspaced_math_using_an_html_tag_name_remains_literal[2 &lt;b and b&gt;1]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_unspaced_math_using_an_html_tag_name_remains_literal[2 &lt;b e b &gt; 1]</code> | PASS | 0.001 |
| `tests/test_transcript_math_markup.py` | <code>test_unspaced_math_using_an_html_tag_name_remains_literal[2 &lt;b e b&gt;1]</code> | PASS | 0.001 |
