"""Offline production endpoints: intent dispatch, brief continuity and failures."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app import db, generation, pipeline
from app.editorial.composition import section_budgets
from app.strategy import store


URL = 'https://www.youtube.com/watch?v=abcdefghijk'


def save_opportunity(**updates):
    value = {'opportunity_id': 'produce-test', 'action': 'create', 'status': 'approved',
             'main_question': 'Como observar as folhas?', 'queries': ['observar folhas'],
             'selected_videos': [{'url': URL, 'reason': 'Exemplo pertinente',
                                  'key_contributions': ['Observação diária'], 'transcript_available': None}],
             'justification': 'Pergunta pertinente ao leitor', 'gaps': ['Não identifica a espécie'],
             'evidence': [{'source': 'video', 'detail': 'Curadoria, ainda não transcrição'}], **updates}
    store.save_opportunity(value, {'id': 'production-cycle'}, 'project-test')
    return value


def test_production_brief_retains_curation_and_has_no_forced_length(authed):
    cycle = {'id': 'production-cycle', 'project_id': 'project-test', 'status': 'completed',
             'created_at': db.now(), 'completed': {}, 'plan': {'context_gaps': ['Dados orgânicos indisponíveis']}}
    for role, output in {
        'curation': {'briefing_notes': 'Preservar as ressalvas do criador', 'research_gaps': ['Sem medida de rega']},
        'intent': {'intents': [{'query': 'observar folhas', 'intent_type': 'Aprender a observar'}],
                   'audience_segments': ['Iniciantes']},
        'business': {'restrictions': ['Não alegar experiência própria']},
    }.items():
        cycle['completed'][role] = store.save_run(cycle, role, 'fixture', {'output': output}, status='completed')
    store.save_cycle(cycle)
    opportunity = save_opportunity()
    with patch.object(pipeline.executor, 'submit') as dispatch:
        first = authed.post('/api/strategy/opportunities/produce-test/produce')
        second = authed.post('/api/strategy/opportunities/produce-test/produce')
    assert first.status_code == second.status_code == 200
    assert first.json()['created'] and second.json()['reused']
    assert first.json()['job_id'] == second.json()['job_id']
    assert dispatch.call_count == 1
    job = db.get_job(first.json()['job_id'])
    assert job['brief']['target_words'] is None
    assert job['brief']['intent'] == 'Aprender a observar'
    context = job['brief']['strategy_context']
    assert context['selected_videos'][0]['key_contributions'] == ['Observação diária']
    assert context['limitations'] == opportunity['gaps']
    assert context['evidence'] == opportunity['evidence']
    assert context['curation_notes'] == 'Preservar as ressalvas do criador'
    assert context['business_restrictions'] == ['Não alegar experiência própria']
    assert context['data_only'] and 'nunca' in context['usage_policy']
    assert generation.evidence_map(job) == {}
    assert job['usage'] == []


def test_dispatch_failure_retry_reuses_committed_job_without_implicit_execution(authed):
    save_opportunity()
    with patch.object(pipeline, 'submit', side_effect=ValueError('Falha de fila controlada')) as dispatch:
        first = authed.post('/api/strategy/opportunities/produce-test/produce')
        retry = authed.post('/api/strategy/opportunities/produce-test/produce')
    assert first.status_code == 400
    assert retry.status_code == 200 and retry.json()['reused']
    assert dispatch.call_count == 1 and len(db.list_jobs()) == 1
    assert db.get_job(retry.json()['job_id'])['status'] == 'new'


@pytest.mark.parametrize('action', ['investigate', 'fix_technical', 'consolidate', 'update', 'improve_links'])
def test_incompatible_action_never_dispatches_even_with_explicit_url(authed, action):
    save_opportunity(action=action)
    with patch.object(pipeline, 'submit') as dispatch:
        response = authed.post('/api/strategy/opportunities/produce-test/produce', json={'urls': [URL]})
    assert response.status_code == 409
    assert db.list_jobs() == []
    dispatch.assert_not_called()


def test_invalid_url_does_not_commit_partial_intent_or_change_approval(authed):
    before = deepcopy(save_opportunity())
    response = authed.post('/api/strategy/opportunities/produce-test/produce', json={'urls': ['invalid']})
    assert response.status_code == 400
    assert db.list_jobs() == []
    assert store.get_opportunity(before['opportunity_id'])['status'] == 'approved'
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_production_intents').fetchone()[0] == 0


def test_full_queue_refuses_new_intent_but_reuses_existing_job(authed):
    save_opportunity()
    with patch.object(pipeline, 'submit'):
        existing = authed.post('/api/strategy/opportunities/produce-test/produce').json()
    for index in range(10):
        db.save_job({'id': str(index), 'status': 'queued', 'created_at': db.now()})
    save_opportunity(opportunity_id='new-opportunity')
    assert authed.post('/api/strategy/opportunities/new-opportunity/produce').status_code == 429
    duplicate = authed.post('/api/strategy/opportunities/produce-test/produce')
    assert duplicate.status_code == 200 and duplicate.json()['job_id'] == existing['job_id']


def test_source_driven_section_budgets_remain_null():
    assert section_budgets([{'id': 'opening', 'item_ids': []},
                            {'id': 's1', 'item_ids': ['a']}], None) == [None, None]


def test_source_driven_brief_completes_single_video_pipeline(job, newsroom_ai, monkeypatch):
    job['brief']['target_words'] = None
    db.save_job(job)
    monkeypatch.setattr(pipeline.youtube, 'extract', lambda urls, **kw: job['sources'])
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['article'] and saved['status'] == 'ready'
    assert saved['brief']['target_words'] is None
    assert saved['sources'][0]['segments'] == job['sources'][0]['segments']
