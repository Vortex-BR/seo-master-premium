from copy import deepcopy
import json

import pytest

from app import db, generation, pipeline
from app.editorial import composition, engine, store, workflow
from app.editorial.contracts import DraftArticle, DraftSection, PassageAudit, VoiceProfile
from conftest import real_structured
from test_evidence_workflow import prepare, set_sources
from test_response_recovery import provider, response


def ready(job, newsroom_ai, monkeypatch):
    saved = prepare(job, newsroom_ai)
    saved['editorial']['composition_version'] = 1
    monkeypatch.setattr(generation, 'structured', real_structured)
    return saved


def wire(job, used=True, long=False):
    text = ('Texto com exemplos do registro e explicação de suas condições. ' * 110 if long else
            'A observação descreve o exemplar e suas condições específicas, preservando o contexto e o método usado no registro.')
    return response(json.dumps({**{k: v for k, v in job['article'].items() if k != 'markdown'},
        'paragraphs': [{'markdown': '## O que observar', 'source_ids': []},
                       {'markdown': text, 'source_ids': ['v1s1']}],
        'usage': {item['id']: used for item in job['apuration']['items']}}))


def test_new_cycles_select_coherent_composition_and_resumes_keep_legacy(job, monkeypatch):
    monkeypatch.setenv('EDITORIAL_COMPOSITION', 'coherent')
    engine.start(job, 'generate')
    assert job['editorial']['composition_version'] == 1
    del job['editorial']['composition_version']
    previous = deepcopy(job['editorial'])
    engine.start(job, 'resume')
    assert 'composition_version' not in job['editorial']
    assert job['editorial']['cycle_id'] == previous['cycle_id']
    monkeypatch.setenv('EDITORIAL_FLOW', 'legacy')
    engine.start(job, 'generate')
    assert 'composition_version' not in job['editorial']


def test_known_insufficient_initial_budget_stops_before_any_paid_work(job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('EDITORIAL_COMPOSITION', 'coherent')
    db.set_setting('editorial_profile', VoiceProfile(max_calls=12).model_dump())
    set_sources(job, count=5)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'budget_exhausted' and saved['editorial']['calls'] == 0
    assert saved['article'] == job['article']
    newsroom_ai.assert_not_called()


def test_coherent_sdk_delivery_includes_citations_metadata_and_reuses_completed_draft(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved)])
    calls = saved['editorial']['calls']
    article = workflow.write(saved)
    assert '[[v1s1]]' in article['markdown']
    assert article['title'] and article['slug']
    assert saved['editorial']['calls'] == calls + 1
    assert workflow.write(saved) == article and len(requests) == 1
    payload = json.loads(requests[0]['input'])
    assert payload['target_words_total'] == 800
    assert payload['article_route']['main_question'] == saved['plan']['data']['main_question']
    assert len(store.artifacts(saved['id'], 'composition_draft')) == 1
    assert not store.artifacts(saved['id'], 'draft_coverage')[0]['data']['missing_item_ids']


def test_paid_draft_saved_before_one_repair_and_worse_coverage_never_replaces_it(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, long=True), wire(saved, used=False)])
    article = workflow.write(saved)
    assert 'Texto com exemplos' in article['markdown']
    assert not store.artifacts(saved['id'], 'composition_repair')[0]['data']['accepted']
    assert workflow.write(saved) == article and len(requests) == 2
    assert json.loads(requests[1]['input'])['repair']['delivery_checks']['word_count'] > 800


def test_single_repair_fixes_overrun_and_no_more_paid_calls_on_resume(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, long=True), wire(saved)])
    result = workflow.write(saved)
    assert 'Texto com exemplos' not in result['markdown']
    assert store.artifacts(saved['id'], 'composition_repair')[0]['data']['accepted']
    assert workflow.write(saved) == result and len(requests) == 2


def test_repair_cannot_trade_missing_coverage_for_new_delivery_defects(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, used=False), wire(saved, long=True)])
    article = workflow.write(saved)
    assert 'Texto com exemplos' not in article['markdown']
    assert not store.artifacts(saved['id'], 'composition_repair')[0]['data']['accepted']
    assert store.artifacts(saved['id'], 'draft_coverage')[0]['data']['missing_item_ids']
    assert workflow.write(saved) == article and len(requests) == 2


def test_no_room_for_repair_keeps_reviewable_draft_instead_of_throwing_it_away(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    saved['editorial']['profile']['profile']['max_calls'] = saved['editorial']['calls'] + 8
    requests = provider(monkeypatch, [wire(saved, used=False)])
    article = workflow.write(saved)
    assert article['markdown'] and len(requests) == 1
    assert store.artifacts(saved['id'], 'draft_coverage')[0]['data']['missing_item_ids']


def test_failed_optional_repair_preserves_paid_draft_and_does_not_loop_on_resume(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    invalid = response('{invalid-json')
    requests = provider(monkeypatch, [wire(saved, long=True), invalid, invalid])
    article = workflow.write(saved)
    assert 'Texto com exemplos' in article['markdown']
    assert store.artifacts(saved['id'], 'composition_repair_failure')[0]['data']['draft_preserved']
    assert len(requests) == 3
    assert workflow.write(saved) == article and len(requests) == 3


def test_oversized_composition_falls_back_before_any_provider_request(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    saved['brief']['instructions'] = 'Material muito extenso. ' * 15000
    requests = provider(monkeypatch, [])
    calls = saved['editorial']['calls']
    assert composition.write(saved, saved['plan']['data'], saved['apuration']['items']) is None
    assert not requests and saved['editorial']['calls'] == calls


def test_section_allocation_never_inflates_total_and_reprises_get_less_space():
    segments = [{'id': 'opening', 'item_ids': []}, {'id': 's1', 'item_ids': ['k1', 'k2', 'k3']},
                {'id': 's2', 'item_ids': ['k1', 'k2', 'k3']}, {'id': 'closing', 'item_ids': []}]
    budgets = composition.section_budgets(segments, 800)
    assert sum(budgets) == 800 and budgets[1] > budgets[2]
    assert budgets[0] < 120 and budgets[-1] < 100


def test_new_composition_passes_full_coordinator_with_independent_factual_review(job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('EDITORIAL_COMPOSITION', 'coherent')
    def respond(current, schema, instruction, stage, extra=None):
        if schema is DraftArticle:
            assert extra['target_words_total'] == current['brief']['target_words']
            return {**job['article'], 'used_item_ids': [i['id'] for i in extra['items']]}
        if schema is PassageAudit:
            assert 'required_qualifications' in extra
            assert all('check' not in item for item in extra['items'])
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['review']['semantic_coverage']['assessed'] > 0
    assert saved['editorial']['composition_version'] == 1
    assert any(call.args[1] is DraftArticle for call in newsroom_ai.call_args_list)
    roles = [call.args[3] for call in newsroom_ai.call_args_list]
    assert not set(roles) & {'reader', 'voice_editor', 'strategist', 'yoast_analyst', 'seo_editor'}
    assert {'fact_reviewer', 'readability_reviewer', 'chief'} <= set(roles)


def test_last_available_call_delivers_visible_draft_and_resume_never_exceeds_cap(job, newsroom_ai, monkeypatch, authed):
    saved = prepare(job, newsroom_ai)
    saved['editorial']['composition_version'] = 1
    cap = saved['editorial']['profile']['profile']['max_calls']
    saved['editorial']['calls'] = cap - 1
    saved.pop('article', None)
    db.save_job(saved)
    def respond(current, schema, instruction, stage, extra=None):
        assert schema is DraftArticle, 'Budget should stop the next request before the provider.'
        return {**job['article'], 'used_item_ids': [i['id'] for i in extra['items']]}
    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'write')
    stopped = db.get_job(job['id'])
    assert stopped['status'] == 'budget_exhausted', stopped.get('error')
    assert stopped['editorial']['calls'] == cap
    assert stopped['draft_delivery']['complete'] and stopped['draft_delivery']['review_pending']
    assert stopped['article'] == job['article']
    assert authed.get(f'/api/jobs/{job["id"]}').json()['article'] == job['article']
    assert authed.get(f'/api/jobs/{job["id"]}/preview').status_code == 200
    exported = authed.get(f'/api/jobs/{job["id"]}/export?format=markdown')
    assert exported.status_code == 200 and job['article']['markdown'] in exported.text
    pipeline.run(job['id'], 'resume')
    assert newsroom_ai.call_count == 1 and db.get_job(job['id'])['editorial']['calls'] == cap


def test_draft_visible_before_optional_repair_even_if_worker_crashes(job, newsroom_ai, monkeypatch, authed):
    saved = prepare(job, newsroom_ai)
    saved['editorial']['composition_version'] = 1
    first = {**job['article'], 'used_item_ids': []}
    def respond(current, schema, instruction, stage, extra=None):
        if not extra.get('repair'):
            return first
        checkpoint = db.get_job(job['id'])
        assert checkpoint['article'] == job['article']
        assert checkpoint['draft_delivery']['review_pending']
        raise RuntimeError('worker interrupted after draft checkpoint')
    newsroom_ai.side_effect = respond
    with pytest.raises(RuntimeError):
        workflow.write(saved)
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200


def test_partial_section_survives_failure_and_can_be_edited_without_paid_calls(job, newsroom_ai, monkeypatch, authed):
    saved = prepare(job, newsroom_ai)
    saved['editorial']['composition_version'] = 1
    monkeypatch.setattr(composition, 'write', lambda *args: None)
    text = 'Primeiro, confira o registro.'
    def respond(current, schema, instruction, stage, extra=None):
        assert schema is DraftSection
        if extra['section']['id'] == 'opening':
            return {'markdown': text + ' Preserve o original.', 'used_item_ids': []}
        assert db.get_job(job['id'])['article']['markdown'].startswith(text)
        raise RuntimeError('next section failed')
    newsroom_ai.side_effect = respond
    with pytest.raises(RuntimeError):
        workflow.write(saved)
    stopped = db.get_job(job['id'])
    assert not stopped['draft_delivery']['complete']
    assert stopped['draft_delivery']['completed_parts'] == 1
    assert not stopped['generation_complete']
    pipeline.step(stopped, 'error', 'Etapa interrompida.')
    assert text in authed.get(f'/api/jobs/{job["id"]}/preview').text
    stopped['article']['markdown'] = 'Texto curto revisado pelo usuário.'
    assert authed.put(f'/api/jobs/{job["id"]}/article', json=stopped['article']).status_code == 200
    edited = db.get_job(job['id'])
    assert edited['editorial']['stale'] and edited['generation_complete']
    assert not edited.get('draft_delivery')


def test_qualification_payload_keeps_material_limits_and_has_no_domain_rules(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    item = saved['apuration']['items'][0]
    item.update(conditions=['Depois de copiar.'], restrictions=['A amostra não garante integridade total.'],
                limitations=['A verificação cobre apenas arquivos abertos.'])
    requests = provider(monkeypatch, [wire(saved)])
    workflow.write(saved)
    payload = json.loads(requests[0]['input'])
    assert payload['required_qualifications'][0] == {'item_id': item['id'],
        'conditions': item['conditions'], 'restrictions': item['restrictions'], 'limitations': item['limitations']}
    assert 'target_words_total' in payload
