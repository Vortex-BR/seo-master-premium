from copy import deepcopy
import json

import httpx
import pytest
from openai import APIConnectionError

from app import db, generation, pipeline
from app.editorial import engine, source_processing, store, video_first, workflow
from app.editorial.contracts import (BlockKnowledge, DraftArticle, EditorialPlan, KnowledgeAudit,
                                    PassageAudit, SpokenExtraction, VideoFidelityReview, VoiceProfile)


def set_sources(job, count=5, segments=1, width=120):
    original = deepcopy(job['sources'][0])
    sources = []
    for v in range(1, count + 1):
        source = deepcopy(original)
        source.update(id=f'v{v}', video_id=f'video{v:06d}', title=f'Fonte {v}', author=f'Autor {v}',
                      url=f'https://www.youtube.com/watch?v=video{v:06d}')
        source['segments'] = [{'id': f'v{v}s{s}', 'start': s * 10, 'end': s * 10 + 9,
            'text': f'Na fonte {v}, a observação {s} descreve as folhas com atenção ao método local. ' +
                    ('Detalhes da observação e do contexto. ' * (width // 36))[:max(0, width - 80)]}
                              for s in range(1, segments + 1)]
        sources.append(source)
    sources[-1]['segments'][-1]['text'] += ' O detalhe final exige observar também a face inferior da folha.'
    job['sources'] = sources
    job['brief']['urls'] = [s['url'] for s in sources]
    db.save_job(job)


def prepare(job, newsroom_ai):
    pipeline.run(job['id'], 'plan')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'plan_ready', saved.get('error')
    return saved


def test_partition_preserves_every_character_including_tail_and_unicode():
    text = ('Primeira explicação com condição. Outra frase com ressalva distante.\n' * 1200) + 'ÚLTIMO DETALHE: apenas neste método.'
    source = {'id': 'v5', 'segments': [{'id': 'v5s1', 'text': text, 'start': None, 'end': None}]}
    groups = source_processing.blocks(source, 3000)
    owned = [part for group in groups for part in group['owned']]
    assert ''.join(part['text'] for part in owned) == text
    assert owned[0]['offset_start'] == 0 and owned[-1]['offset_end'] == len(text)
    assert all(a['offset_end'] == b['offset_start'] for a, b in zip(owned, owned[1:]))
    assert len(groups) > 20 and groups[-1]['owned'][-1]['text'].endswith('apenas neste método.')
    assert all(group['surroundings'] for group in groups)


def test_quality_detects_duplicate_disorder_gap_and_corruption_without_repair():
    source = {'provider': 'Legendas', 'generated_captions': True, 'segments': [
        {'text': 'Fala repetida.', 'start': 100, 'end': 105}, {'text': 'Fala repetida.', 'start': 20, 'end': 25},
        {'text': 'Termo ??? \ufffd', 'start': 180, 'end': 190}]}
    original = deepcopy(source)
    quality = source_processing.quality(source)
    assert len(quality['warnings']) == 4
    assert quality['generated_captions'] is True and quality['completeness'] == 'unverified'
    assert source == original
    assert source_processing.quality({'segments': []})['generated_captions'] is None


def test_explicit_plan_preserves_article_and_generate_automatically_writes(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    assert saved['article'] == job['article'] and db.revisions(job['id']) == []
    assert saved['plan']['valid'] and len(saved['plan']['data']['dispositions']) == 1
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['extractor', 'planner']
    db.set_setting('editorial_profile', VoiceProfile(auto_write=False).model_dump())
    pipeline.run(job['id'])
    assert db.get_job(job['id'])['status'] == 'ready'
    assert db.get_job(job['id'])['generation_complete'] is True


def test_five_complementary_videos_all_participate_in_plan_and_review(job, newsroom_ai):
    set_sources(job)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert {i['video_id'] for i in saved['apuration']['items']} == {f'v{i}' for i in range(1, 6)}
    assert all(b['status'] == 'extracted' for b in saved['apuration']['inventory']['blocks'])
    assert all(d['status'] == 'used' for d in saved['coverage']['items'])
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert saved['review']['semantic_coverage']['batches'] == 1
    assert 'face inferior' in saved['article']['markdown']
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['extractor', 'planner', 'writer', 'fact_reviewer']
    assert saved['editorial']['calls'] == 4


def test_long_extraction_keeps_every_segment_and_final_detail_in_one_delivery(job, newsroom_ai):
    set_sources(job, segments=30, width=1000)
    db.set_setting('editorial_profile', VoiceProfile(context_chars=240000).model_dump())
    originals = {s['id']: s['text'] for source in job['sources'] for s in source['segments']}
    engine.start(job, 'plan')
    extracted = workflow.extract(job)
    assert extracted['inventory']['characters'] > 140000
    assert len(extracted['inventory']['blocks']) >= 20
    assert len(extracted['items']) == 150
    assert any('face inferior' in i['statement'] and i['video_id'] == 'v5' for i in extracted['items'])
    assert job['editorial']['calls'] == 1
    payload = newsroom_ai.call_args.args[4]
    assert {s['id'] for video in payload['videos'] for s in video['segments']} == set(originals)
    restored = {}
    for block in extracted['inventory']['blocks']:
        for part in block['owned']:
            restored[part['source_id']] = restored.get(part['source_id'], '') + part['text']
    assert restored == originals


def test_large_draft_uses_one_composition_and_preserves_original_evidence(job, newsroom_ai):
    set_sources(job, count=1, segments=40, width=400)
    db.set_setting('editorial_profile', VoiceProfile(context_chars=240000).model_dump())
    seen = []
    def respond(current, schema, instruction, stage, extra=None):
        if schema is DraftArticle:
            originals = workflow.item_index(current)
            for item in extra['items']:
                original = originals[item['id']]
                assert item['source_spoken_insight'] == original['source_spoken_insight']
                assert item['source_ids'] == [e['source_id'] for e in original['evidence']]
                for evidence in original['evidence']:
                    assert evidence['excerpt'] in extra['_context_sources'][evidence['source_id']]['text']
            seen.append({i['id'] for i in extra['items']})
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert not store.artifacts(job['id'], 'draft_section')
    assert store.artifacts(job['id'], 'composition_draft')
    assert len(seen) == 1 and len(seen[0]) == 40
    assert all(item['status'] == 'used' for item in saved['coverage']['items'])
    assert saved['review']['article_hash'] == generation.article_hash(saved['article'])


def test_oversized_composition_preserves_plan_and_refuses_section_fallback(job, newsroom_ai):
    set_sources(job, count=1, segments=40, width=1000)
    db.set_setting('editorial_profile', VoiceProfile(context_chars=240000).model_dump())
    saved = prepare(job, newsroom_ai)
    saved['editorial']['profile']['profile']['context_chars'] = 30000
    assert workflow.compatible(saved)
    with pytest.raises(generation.ContextLimitExceeded):
        workflow.write(saved)
    assert saved['article'] == job['article'] and saved['plan']['valid']
    assert saved['editorial']['calls'] == 2
    assert [c.args[3] for c in newsroom_ai.call_args_list] == ['extractor', 'planner']
    assert not store.artifacts(job['id'], 'draft_section')


def test_oversized_transcript_is_refused_before_extraction_or_paid_work(job, newsroom_ai):
    set_sources(job, count=1, segments=40, width=1000)
    db.set_setting('editorial_profile', VoiceProfile(context_chars=30000).model_dump())
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'error' and 'contexto' in saved['error']
    assert saved['article'] == job['article'] and not saved.get('plan')
    assert saved['editorial']['calls'] == 0
    assert not store.report(saved)['runs']
    newsroom_ai.assert_not_called()


def test_literal_reference_does_not_override_semantic_rejection(job, newsroom_ai):
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is SpokenExtraction:
            output['videos'][0]['insights'][0]['spoken_explanation'] = 'Todas as hortas crescem sempre sem risco.'
        if schema is VideoFidelityReview:
            for assessment in output['assessments']:
                if assessment['used_item_ids']:
                    assessment.update(status='unsupported', used_item_ids=[],
                                      reason='A observação pessoal não sustenta uma regra universal.')
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review', saved.get('error')
    assert any(f['origin'] == 'semantic_review' for f in saved['review']['findings'])
    assert any(f['origin'] == 'coverage' for f in saved['review']['findings'])
    assert saved['editorial']['calls'] == 4
    assert not any(c.args[3] in ('source_checker', 'voice_editor') for c in newsroom_ai.call_args_list)


def test_distant_caveat_reaches_planner_and_original_transcript_reviewer(job, newsroom_ai):
    set_sources(job, count=1, segments=10, width=300)
    caveat = 'A recomendação inicial vale apenas para folhas novas.'
    job['sources'][0]['segments'][-1]['text'] += ' ' + caveat
    db.save_job(job)
    seen = {}
    def respond(current, schema, instruction, stage, extra=None):
        if schema is EditorialPlan:
            seen['planner'] = json.dumps(extra, ensure_ascii=False)
        if schema is VideoFidelityReview:
            seen['reviewer'] = extra['_context_sources']['v1s10']['text']
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert caveat in seen['planner'] and caveat in seen['reviewer']
    assert saved['apuration']['comparisons'] == []


def test_different_units_and_methods_remain_as_creator_alternatives(job, newsroom_ai):
    set_sources(job, count=2)
    job['sources'][0]['segments'][0]['text'] = 'No método A, observe a folha durante 3 horas, apenas em ambiente seco.'
    job['sources'][1]['segments'][0]['text'] = 'No método B, observe a folha durante 3 minutos, apenas em ambiente úmido.'
    db.save_job(job)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert '3 horas' in saved['article']['markdown'] and '3 minutos' in saved['article']['markdown']
    assert 'ambiente seco' in saved['article']['markdown'] and 'ambiente úmido' in saved['article']['markdown']
    assert saved['apuration']['comparisons'] == []
    assert len([c for c in newsroom_ai.call_args_list if c.args[3] == 'planner']) == 1


def test_visual_only_assertion_remains_uncertain_and_blocks_delivery(job, newsroom_ai):
    gap = 'A medida aparece apenas no gráfico, não analisado.'
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is SpokenExtraction:
            output['videos'][0]['gaps'] = [gap]
        if schema is EditorialPlan:
            assert extra['video_gaps'][0]['gaps'] == [gap]
            assert extra['items'][0]['limitations'] == [gap]
        if schema is DraftArticle:
            assert extra['required_qualifications'][0]['limitations'] == [gap]
        if schema is VideoFidelityReview:
            for assessment in output['assessments']:
                if assessment['used_item_ids']:
                    assessment.update(status='uncertain', used_item_ids=[],
                                      reason=gap)
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review', saved.get('error')
    assert any('gráfico' in f['reason'] for f in saved['review']['findings'])
    assert saved['editorial']['calls'] == 4


def test_plan_edit_is_versioned_invalidates_draft_and_rejects_stale_save(authed, job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    plan = deepcopy(saved['plan']['data'])
    plan['sections'][0]['title'] = 'Uma nova organização das observações'
    payload = {'base_version': saved['plan']['version'], 'plan': plan}
    assert authed.put(f'/api/jobs/{job["id"]}/plan', json=payload).status_code == 200
    changed = db.get_job(job['id'])
    assert changed['article'] == job['article'] and changed['article_needs_generation']
    assert changed['plan']['version'] != saved['plan']['version'] and changed['review'] is None
    assert authed.put(f'/api/jobs/{job["id"]}/plan', json=payload).status_code == 409
    history = authed.get(f'/api/jobs/{job["id"]}/artifacts?kind=plan').json()
    assert {a['version'] for a in history} == {changed['plan']['version'], saved['plan']['version']}


@pytest.mark.parametrize('invalid', ['unknown', 'missing', 'unsupported'])
def test_plan_rejects_unknown_omitted_or_unverified_information(authed, job, newsroom_ai, invalid):
    saved = prepare(job, newsroom_ai)
    plan = deepcopy(saved['plan']['data'])
    if invalid == 'unknown':
        plan['sections'][0]['item_ids'].append('invented')
    elif invalid == 'missing':
        plan['dispositions'] = []
    else:
        saved['apuration']['items'][0]['check']['status'] = 'uncertain'
        db.save_job(saved)
    response = authed.put(f'/api/jobs/{job["id"]}/plan', json={'base_version': saved['plan']['version'], 'plan': plan})
    assert response.status_code == 400
    assert db.get_job(job['id'])['plan'] == saved['plan']


def test_plan_edit_blocked_while_busy_and_source_change_invalidates(authed, job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    saved['status'] = 'writing'
    db.save_job(saved)
    payload = {'base_version': saved['plan']['version'], 'plan': saved['plan']['data']}
    assert authed.put(f'/api/jobs/{job["id"]}/plan', json=payload).status_code == 409
    saved['status'] = 'plan_ready'
    db.save_job(saved)
    assert authed.post(f'/api/jobs/{job["id"]}/source', json={
        'video_id': job['sources'][0]['video_id'], 'text': 'Uma nova fala sobre a horta, com outras condições. ' * 5
    }).status_code == 200
    changed = db.get_job(job['id'])
    assert not changed['plan']['valid'] and not changed['apuration']['valid']
    assert authed.post(f'/api/jobs/{job["id"]}/write').status_code == 400


def test_safety_cap_preserves_work_and_resume_keeps_calls(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    cycle = saved['editorial']['cycle_id']
    saved['editorial']['calls'] = 24
    db.save_job(saved)
    paid = newsroom_ai.call_count
    pipeline.run(job['id'], 'write')
    stopped = db.get_job(job['id'])
    assert stopped['status'] == 'budget_exhausted'
    assert stopped['article'] == job['article'] and stopped['plan']['valid']
    assert newsroom_ai.call_count == paid
    # Stored profiles from previous versions may contain a larger historical cap.
    db.set_setting('editorial_profile', {**VoiceProfile().model_dump(), 'max_calls': 120})
    pipeline.run(job['id'], 'resume')
    resumed = db.get_job(job['id'])
    assert resumed['status'] == 'budget_exhausted'
    assert resumed['editorial']['cycle_id'] == cycle and resumed['editorial']['calls'] == 24
    assert newsroom_ai.call_count == paid


def test_restart_reuses_extraction_and_changed_payload_gets_new_cache_entry(job, newsroom_ai):
    failed = False
    def respond(current, schema, instruction, stage, extra=None):
        nonlocal failed
        if schema is EditorialPlan and not failed:
            failed = True
            raise RuntimeError('interruption')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'plan')
    assert db.get_job(job['id'])['status'] == 'error'
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'plan_ready', saved.get('error')
    assert sum(c.args[1] is SpokenExtraction for c in newsroom_ai.call_args_list) == 1
    assert sum(c.args[1] is EditorialPlan for c in newsroom_ai.call_args_list) == 2
    first = engine.invoke(saved, 'extractor', {'query': 'original'}, callback=lambda current: {'summary': 'Original'}, slot='cache-probe')[1]
    same = engine.invoke(saved, 'extractor', {'query': 'original'}, callback=lambda current: pytest.fail('cache missed'), slot='cache-probe')[1]
    changed = engine.invoke(saved, 'extractor', {'query': 'alterada'}, callback=lambda current: {'summary': 'Alterada'}, slot='cache-probe')[1]
    assert first == same and first != changed


def test_missing_semantic_assessment_is_retried_and_never_approved(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is VideoFidelityReview:
            output['assessments'] = output['assessments'][:-1]
        return output
    newsroom_ai.side_effect = respond
    with pytest.raises(generation.GenerationResponseError):
        workflow.factual_review(saved, 0)
    assert saved['editorial']['calls'] == 2 and saved['review'] is None


def test_single_review_covers_over_eighty_original_segments_and_final_paragraph(job, newsroom_ai):
    # Direct review also supports manual/legacy articles without extracting >80 insights per video.
    set_sources(job, count=1, segments=90, width=100)
    job['article']['markdown'] = 'No vídeo, Autor 1 explica a observação.\n\n## Observações\n\n' + '\n\n'.join(
        s['text'] + f' [[{s["id"]}]]' for s in job['sources'][0]['segments'])
    seen = []
    def respond(current, schema, instruction, stage, extra=None):
        if schema is VideoFidelityReview:
            seen.append({'sources': set(extra['_context_sources']),
                         'last_original': extra['_context_sources']['v1s90']['text'],
                         'last_passage': extra['passages'][-1]})
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    engine.start(job, 'review')
    result = workflow.factual_review(job, 0)
    assert result['semantic_coverage']['batches'] == 1
    assert result['semantic_coverage']['assessed'] == len(workflow.passages(job['article']))
    assert seen[0]['sources'] == {f'v1s{n}' for n in range(1, 91)}
    assert 'face inferior' in seen[0]['last_original'] and 'face inferior' in seen[0]['last_passage']['text']
    assert result['semantic_coverage']['assessments'][-1]['passage_id'] == seen[0]['last_passage']['id']
    assert job['editorial']['calls'] == 1


def test_issues_survive_new_summary_and_resolution_has_history(authed, job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    ident = store.issue(saved, 'comparison', 'critical', 'Falta uma condição indispensável para comparar métodos.', essential=True)
    before = store.issues(saved)
    engine.invoke(saved, 'extractor', callback=lambda current: {'summary': 'Resumo que não resolve a pendência.'})
    assert store.issues(saved) == before
    saved['status'] = 'plan_ready'
    db.save_job(saved)
    assert authed.post(f'/api/jobs/{job["id"]}/issues/{ident}/resolve', json={
        'reason': 'Conferi o trecho original e preservei os métodos em alternativas separadas.', 'source_ids': ['v1s1']
    }).status_code == 200
    resolved = store.issues(saved)[0]
    assert resolved['status'] == 'resolved' and resolved['reason'] == before[0]['reason']
    assert resolved['resolution']['source_ids'] == ['v1s1']


@pytest.mark.parametrize('essential', [False, True])
def test_saved_issue_priority_stays_scoped_to_plan_and_keeps_issue_open(job, newsroom_ai, essential):
    saved = prepare(job, newsroom_ai)
    ident = store.issue(saved, 'comparison', 'optional', 'Não é possível comparar observações de métodos diferentes.', essential=True)
    saved['apuration']['pending'] = store.issues(saved)
    data = deepcopy(saved['plan']['data'])
    data['issue_priorities'] = [{'issue_id': ident, 'essential': essential,
        'reason': 'A relevância da comparação foi avaliada em relação à pergunta central do plano.'}]
    workflow.save_plan(saved, data)
    assert saved['plan']['data']['ready_to_write'] is (not essential)
    issue = store.issues(saved)[0]
    assert workflow.essential_issue(saved, issue) is essential
    assert issue['status'] == 'open' and issue['essential'] is True and issue['resolution'] is None
    review = workflow.factual_review(saved, 0)
    assert any(f.get('origin') == 'pending_issue' for f in review['findings']) is essential


def test_writing_continues_saved_plan_budget_and_keeps_completed_deliveries(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    before = deepcopy(saved['editorial'])
    workflow.save_plan(saved, deepcopy(saved['plan']['data']), manual=True)
    store.invalidate(saved, 'O planejamento foi editado. Redija a partir da nova versão.')
    assert engine.start(saved, 'write') == 'write'
    assert saved['editorial']['cycle_id'] == before['cycle_id']
    assert saved['editorial']['calls'] == before['calls']
    assert saved['editorial']['completed'] == before['completed']
    assert saved['editorial']['stale'] is False
    db.set_setting('editorial_profile', VoiceProfile(tone='Outra voz editorial.').model_dump())
    engine.start(saved, 'write')
    assert saved['editorial']['cycle_id'] != before['cycle_id'] and saved['editorial']['calls'] == 0


def test_estimates_and_legacy_switch_cannot_restore_the_old_pipeline(authed, job, newsroom_ai, monkeypatch):
    estimate = authed.get(f'/api/jobs/{job["id"]}/estimate').json()
    assert estimate['blocks'] == 1 and estimate['estimated_calls_min'] == 4
    assert estimate['estimated_calls_max'] == 8
    monkeypatch.setenv('EDITORIAL_FLOW', 'legacy')
    monkeypatch.setenv('EDITORIAL_COMPOSITION', 'legacy')
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready' and saved['editorial']['calls'] == 4
    assert saved['editorial']['video_first'] is True


def test_connection_retry_is_counted_and_does_not_save_provider_content(job, newsroom_ai):
    engine.start(job, 'plan')
    attempt = 0
    def respond(current, schema, instruction, stage, extra=None):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise APIConnectionError(request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    workflow.extract(job)
    assert attempt == 2 and job['editorial']['calls'] == 2
    assert next(iter(job['editorial']['response_recoveries'].values()))['reason'] == 'connection'
    assert {r['status'] for r in store.report(job)['runs']} == {'failed', 'completed'}


def test_editorial_endpoints_require_authentication(client):
    for path in ['/api/jobs/nope/estimate', '/api/jobs/nope/artifacts']:
        assert client.get(path).status_code == 401
    for path in ['/api/jobs/nope/plan', '/api/jobs/nope/write']:
        assert client.post(path).status_code == 401


def test_budget_and_automation_changes_preserve_knowledge_but_voice_changes_do_not(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    before = deepcopy(saved['editorial'])
    db.set_setting('editorial_profile', {**VoiceProfile(context_chars=160000, auto_write=False).model_dump(),
                                        'max_calls': 200})
    engine.start(saved, 'resume')
    assert saved['editorial']['profile']['profile']['context_chars'] == 160000
    assert saved['editorial']['profile']['profile']['max_calls'] == 24
    assert saved['editorial']['calls'] == before['calls'] and saved['editorial']['completed'] == before['completed']
    engine.start(saved, 'write')
    assert workflow.compatible(saved)
    db.set_setting('editorial_profile', VoiceProfile(tone='Outra voz editorial.').model_dump())
    engine.start(saved, 'plan')
    assert not workflow.compatible(saved)


def test_spoken_extraction_uses_sdk_strict_schema_and_records_usage(job, newsroom_ai, monkeypatch):
    from test_response_recovery import provider, response, structured
    engine.start(job, 'plan')
    payload = {'videos': [{'id': job['sources'][0]['id'], 'segments': job['sources'][0]['segments']}], '_context_sources': {}}
    expected = newsroom_ai.respond(job, SpokenExtraction, video_first.EXTRACT, 'extractor', payload)
    requests = provider(monkeypatch, [response(json.dumps(expected))])
    monkeypatch.setattr(generation, 'structured', structured)
    actual = workflow.call(job, 'extractor', SpokenExtraction, video_first.EXTRACT, payload, 'sdk:spoken')
    assert actual == expected and len(requests) == 1
    fmt = requests[0]['text']['format']
    assert fmt['strict'] and fmt['schema']['additionalProperties'] is False
    insight_schema = next(value for value in fmt['schema']['$defs'].values()
                          if 'spoken_explanation' in value.get('properties', {}))
    assert insight_schema['additionalProperties'] is False
    allowed = insight_schema['properties']['source_segment_ids']['items']
    assert allowed.get('enum', [allowed.get('const')]) == ['v1s1']
    material = json.loads(requests[0]['input'])
    assert material['videos'][0]['segments'][0]['id'] == 'v1s1'
    assert material['fontes_para_conferencia'] == {}
    assert 'max_calls' not in material['equipe_editorial']['profile']['profile']
    assert job['usage'][0]['input_tokens'] == 123 and job['editorial']['calls'] == 1


@pytest.mark.parametrize('reference', ['private-invented-id', 'v2s1'])
def test_invalid_spoken_reference_is_retried_and_never_saved_as_knowledge(job, newsroom_ai, reference):
    if reference == 'v2s1':
        set_sources(job, count=2)
    original = deepcopy(job['article'])
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is SpokenExtraction:
            output['videos'][0]['insights'][0]['source_segment_ids'] = [reference]
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'plan')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'error' and not saved.get('apuration')
    assert saved['article'] == original and saved['editorial']['calls'] == 2
    assert 'private-invented-id' not in json.dumps(store.report(saved))


def test_selected_evidence_preserves_all_spans_unicode_and_block_ownership():
    from app.editorial import evidence_selection
    text = ('Contexto original: ação e observação das folhas. ' * 180) + 'ÚLTIMA RESSALVA.'
    original = {'v1s1': {'text': text}}
    owned = {'v1s1': {'text': text[3000:6000]}}
    schema, options = evidence_selection.prepare(BlockKnowledge, owned, original)
    assert ''.join(option['excerpt'] for option in options.values()) == text[3000:6000]
    assert all(option['excerpt'] in owned['v1s1']['text'] for option in options.values())
    assert all(option['source_id'] == 'v1s1' for option in options.values())
    assert schema is not BlockKnowledge


def test_review_evidence_never_quotes_window_markers_or_unseen_spans():
    from app.editorial import evidence_selection
    original = {'v1s1': {'text': 'Primeira fala. Fora do contexto. Última ressalva.'}}
    spans = [(0, 14), (31, len(original['v1s1']['text']))]
    sources = {'v1s1': {'text': 'Primeira fala.\n[... intervalo entre trechos ...]\nÚltima ressalva.', 'original_spans': spans}}
    schema, options = evidence_selection.prepare(PassageAudit, sources, original)
    assert all(option['excerpt'] in original['v1s1']['text'] for option in options.values())
    assert not any('Fora do contexto' in option['excerpt'] or '[...' in option['excerpt'] for option in options.values())
    selected = {'summary': 'Conferência', 'assessments': [{'passage_id': 'p1', 'status': 'supported',
        'reason': 'Conferido', 'evidence': [{'reference': 'e2'}], 'used_item_ids': ['k1']}]}
    result = evidence_selection.resolve(schema.model_validate(selected).model_dump(), PassageAudit, options)
    workflow.validate_evidence(result['assessments'], original)
    assert result['assessments'][0]['evidence'] == [options['e2']]


def test_evidence_mismatch_has_specific_safe_diagnostic_and_recovery(job):
    engine.start(job, 'plan')
    calls = []
    def deliver(current):
        calls.append(deepcopy(generation.agent_scope.get()))
        workflow.validate_evidence([{'evidence': [{'source_id': 'missing-private-id', 'excerpt': 'private quote'}]}], {})
    with pytest.raises(generation.GenerationResponseError) as error:
        engine.invoke(job, 'extractor', callback=deliver)
    assert error.value.reason == 'evidence_mismatch'
    assert len(calls) == 2 and calls[1]['response_recovery']['reason'] == 'evidence_mismatch'
    assert 'private' not in str(error.value)
    assert {r['data']['error_reason'] for r in store.report(job)['runs']} == {'evidence_mismatch'}


@pytest.mark.parametrize('checks', [{}, {'r1': {'status': 'supported', 'reason': 'Conferido'}}])
def test_saved_audit_contract_never_fills_missing_item_automatically(checks):
    from pydantic import ValidationError
    from app.editorial import evidence_selection
    schema, ids = evidence_selection.prepare_audit(KnowledgeAudit, {'items': [{'id': 'r1'}, {'id': 'r2'}]})
    with pytest.raises(ValidationError):
        schema.model_validate({'summary': 'Incomplete', 'checks': checks})
    assert ids == ('r1', 'r2')
