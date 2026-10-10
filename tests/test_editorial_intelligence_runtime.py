"""Exercise the opt-in IEC runtime with real persistence and financial controls.

Provider and HTTP boundaries are replaced. No test opens a paid provider or
publishes outside its isolated temporary SQLite database and MockTransport.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import count
import json
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from app import db, generation, pipeline, publishing, spending, wordpress
from app.editorial import changes, delivery, intelligence, intelligence_runtime as runtime, store, workflow
from app.editorial.intelligence_contracts import IECDiagnosis, IECProposalBatch, IECRequest, IECValidation


FORMATS = ('markdown', 'html', 'json', 'wordpress', 'wordpress-html')


def ledger(job):
    with db.connect() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='spend_reservations'").fetchone():
            return []
        return [json.loads(row['data']) for row in connection.execute(
            'SELECT data FROM spend_reservations WHERE job_id=?', (job['id'],))]


def master_rows(job):
    with db.connect() as connection:
        return [{'status': row['status'], **json.loads(row['data'])} for row in connection.execute(
            "SELECT status,data FROM agent_runs WHERE job_id=? AND role='editorial_intelligence'",
            (job['id'],))]


def request(job, **options):
    return {'article_hash': generation.article_hash(job['article']), 'mode': 'suggest',
            'budget_usd': '0.20', **options}


@pytest.fixture
def source_article(job, monkeypatch):
    monkeypatch.setenv('EDITORIAL_INTELLIGENCE_MODE', 'shadow')
    job['sources'][0]['segments'][0]['text'] = (
        'Use um recipiente com furos. Os furos permitem que o excesso de água saia do recipiente. '
        'Este método ajuda quando o recipiente recebe água em excesso.')
    job['article']['markdown'] = (
        '## Preparação do recipiente\n\n'
        'Use um recipiente com furos antes de colocar o substrato. [[v1s1]]\n\n'
        'Observe o desenvolvimento das folhas para acompanhar o cultivo.')
    job['article_needs_generation'] = False
    db.save_job(job)
    return job


@pytest.fixture
def iec_provider(source_article, monkeypatch):
    """Capture actual prepared requests and simulate known, nonzero provider usage."""
    state = {'calls': [], 'scenario': 'video', 'hook': None, 'fail_stage': None,
             'failure': None, 'first_failure': False}
    opened = Mock(side_effect=AssertionError('Live providers are forbidden in IEC tests.'))
    monkeypatch.setattr(generation, 'client', opened)
    monkeypatch.setattr(intelligence_runtime_module := runtime.intelligence_research, 'discover',
                        Mock(side_effect=AssertionError('Unrequested research is forbidden.')))
    monkeypatch.setattr(intelligence_runtime_module.research, 'page_text',
                        Mock(side_effect=AssertionError('Unrequested HTTP is forbidden.')))

    def respond(current, schema, instruction, stage, extra=None, *, prepared=None):
        assert prepared is not None, 'IEC must dispatch the request it fingerprinted.'
        state['calls'].append({'stage': stage, 'schema': schema.model_json_schema(),
            'instruction': instruction, 'payload': deepcopy(extra), 'request': deepcopy(prepared[0])})
        should_fail = state['fail_stage'] == stage and not state['first_failure']
        if should_fail and state['failure'] == 'timeout':
            state['first_failure'] = True
            api = NS(responses=NS(create=Mock(side_effect=httpx.ReadTimeout('secret private provider body'))))
            return spending.create_response(current, api, prepared[0], stage)
        reserved = spending.reserve(current, .02, 'gpt-4.1-mini', stage)
        response = NS(id='fixture-response-' + reserved, status='completed', output=[],
            usage=NS(input_tokens=1000, output_tokens=100,
                     input_tokens_details=NS(cached_tokens=100)))
        receipt = spending.finish(reserved, spending.response_usage(response), response_id=response.id)
        generation.record_usage(current, response, stage, receipt, request_model='gpt-4.1-mini')
        if should_fail:
            state['first_failure'] = True
            raise state['failure']
        if state['hook']:
            state['hook'](current, schema, stage, extra)
        block = next(value for value in extra['blocks'] if value['text'].startswith('Use um recipiente'))
        if schema is IECDiagnosis:
            if state['scenario'] == 'clear':
                return {'status': 'no_change', 'summary': 'A explicação já está completa.', 'opportunities': []}
            return {'status': 'opportunities', 'summary': 'Uma dúvida prática pode ser esclarecida.',
                'opportunities': [{'id': 'why-holes', 'block_id': 'invented-block' if state['scenario'] == 'bad_block' else block['id'], 'kind': 'missing_reason',
                    'question': 'Por que o recipiente precisa de furos?', 'benefit': 'Entender a etapa antes de executá-la.',
                    'evidence_ids': ['invented-source'] if state['scenario'] == 'bad_evidence' else ['v1s1'],
                    'external_query': 'Escoamento de água no recipiente' if state['scenario'] in ('external', 'bad_block', 'bad_evidence') else '',
                    'already_explained': state['scenario'] == 'later'}]}
        if schema is IECProposalBatch:
            foundation = next(value for value in extra['foundations'].values()
                if value['origin'] == ('external_verified' if state['scenario'] == 'external' else 'video'))
            excerpt = 'Os furos permitem que o excesso de água saia do recipiente.'
            offset = foundation['text'].index(excerpt)
            addition = excerpt if state['scenario'] == 'external' else 'O motivo é permitir que o excesso de água saia do recipiente.'
            if state['scenario'] == 'duplicate':
                addition = 'Observe o desenvolvimento das folhas para acompanhar o cultivo.'
            proposal = {'opportunity_id': 'why-holes', 'block_id': block['id'], 'addition': addition,
                'origin': foundation['origin'], 'claim_nature': 'hypothesis' if state['scenario'] == 'hypothesis' else 'assertion',
                'supports': [{'reference_id': foundation.get('id') or foundation['reference_id'],
                    'excerpt': excerpt, 'offset_start': offset, 'offset_end': offset + len(excerpt)}],
                'limitations': ''}
            return {'summary': 'Uma explicação localizada.', 'proposals': [proposal]}
        assert schema is IECValidation
        accepted = state['scenario'] != 'uncertain'
        return {'summary': 'O complemento foi comparado com o artigo integral.', 'assessments': [
            {'opportunity_id': candidate['opportunity_id'], 'status': 'accept' if accepted else 'uncertain',
             'precision': accepted, 'attribution': True, 'noncontradiction': True,
             'redundancy': True, 'cohesion': True, 'usefulness': True,
             'reason': 'O trecho original sustenta a explicação.' if accepted else 'A evidência não resolve a hipótese.'}
            for candidate in extra['proposals']]}

    provider = Mock(side_effect=respond)
    monkeypatch.setattr(generation, 'structured', provider)
    state.update(provider=provider, opened=opened)
    return state


def test_pipeline_off_shadow_exact_requests_checkpoints_costs_and_five_exports(
        authed, job, newsroom_ai, monkeypatch):
    monkeypatch.setattr(db, 'now', lambda: '2026-10-10T12:00:00+00:00')
    monkeypatch.setattr(publishing, 'datetime', NS(now=lambda _zone: datetime(
        2026, 10, 10, 12, 0, tzinfo=timezone.utc)))
    original, requests, seen = deepcopy(job), [], {}
    opened = Mock(side_effect=AssertionError('No live provider.'))
    monkeypatch.setattr(generation, 'client', opened)

    def respond(current, schema, instruction, stage, extra=None):
        prepared = generation.prepare_structured(current, schema, instruction, stage, extra)
        requests.append({'stage': stage, 'schema': schema.model_json_schema(), 'instruction': instruction,
                         'payload': deepcopy(extra), 'request': deepcopy(prepared[0])})
        reservation = spending.reserve(current, .02, 'gpt-4.1-mini', stage)
        response = NS(id='response-' + reservation, status='completed', output=[], usage=NS(
            input_tokens=1000, output_tokens=100, input_tokens_details=NS(cached_tokens=100)))
        receipt = spending.finish(reservation, spending.response_usage(response), response_id=response.id)
        generation.record_usage(current, response, stage, receipt, request_model='gpt-4.1-mini')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    for mode in ('off', 'shadow'):
        monkeypatch.setenv('EDITORIAL_INTELLIGENCE_MODE', mode)
        if mode == 'shadow':
            with db.connect() as connection:
                for table in ('agent_runs', 'agent_messages', 'change_sets', 'editorial_artifacts',
                              'editorial_issues', 'spend_reservations', 'revisions'):
                    connection.execute(f'DELETE FROM {table} WHERE job_id=?', (job['id'],))
                for table in ('cost_runs', 'cost_events'):
                    connection.execute(f'DELETE FROM {table} WHERE entity_id=?', (job['id'],))
            db.save_job(deepcopy(original))
        identities = count(1)
        monkeypatch.setattr(store, 'new_id', lambda: f'controlled-run-{next(identities)}')
        start = len(requests)
        pipeline.run(job['id'])
        saved = db.get_job(job['id'])
        exports = {format: authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format})
                   for format in FORMATS}
        assert all(response.status_code == 200 for response in exports.values())
        seen[mode] = {'requests': requests[start:], 'article': saved['article'],
            'checkpoints': {row['data']['slot']: row['input_hash'] for row in store.report(saved)['runs']},
            'dependencies': workflow.dependencies(saved), 'plan_version': saved['plan']['version'],
            'apuration_version': saved['apuration']['version'],
            'spending': spending.summary(saved, persist=False),
            'exports': {format: response.content for format, response in exports.items()}}
        assert len(ledger(saved)) == 4
        assert bool(store.artifacts(job['id'], 'editorial_intelligence')) is (mode == 'shadow')
    assert seen['off'] == seen['shadow']
    assert seen['off']['spending']['spent_usd'] > 0
    assert [value['stage'] for value in seen['off']['requests']] == ['extractor', 'planner', 'writer', 'fact_reviewer']
    opened.assert_not_called()


def test_clear_article_uses_one_bounded_detection_without_search_or_rewrite(source_article, iec_provider):
    iec_provider['scenario'] = 'clear'
    before = deepcopy(db.get_job(source_article['id']))
    result = runtime.execute(source_article['id'], request(source_article))
    assert result['report']['status'] == 'no_change' and result['changes'] == []
    assert [value['stage'] for value in iec_provider['calls']] == ['iec_detect']
    saved = db.get_job(source_article['id'])
    assert saved['article'] == before['article'] and saved['review'] == before['review']
    assert len(ledger(saved)) == 1 and result['report']['costs']['calculated_usd'] > 0
    assert result['report']['costs']['invoice_usd'] is None
    assert delivery.describe(saved)['export_available']
    again = runtime.execute(source_article['id'], request(source_article))
    assert again['execution_id'] == result['execution_id']
    assert again['report']['status'] == 'no_change'
    assert again['report']['costs'] == result['report']['costs']
    assert len(iec_provider['calls']) == len(ledger(saved)) == 1
    iec_provider['opened'].assert_not_called()


def test_video_suggestion_three_calls_manual_apply_and_undo_retain_original_trace(
        authed, source_article, iec_provider):
    before = deepcopy(source_article['article'])
    result = runtime.execute(source_article['id'], request(source_article))
    assert result['status'] == 'suggested', result
    assert result['report']['accepted_count'] == 1 and result['report']['applied_count'] == 0
    assert result['report']['costs']['cost_per_accepted_improvement_usd'] > 0
    assert result['report']['costs']['cost_per_applied_improvement_usd'] is None
    assert [value['stage'] for value in iec_provider['calls']] == ['iec_detect', 'iec_compose', 'iec_validate']
    assert db.get_job(source_article['id'])['article'] == before
    item = result['changes'][0]
    assert '[00:10] [[v1s1]]' in item['changes'][0]['after']
    proof = next(value for value in store.artifacts(source_article['id'], 'iec_proof')
                 if value['version'] == item['iec_proof_version'])
    reference = proof['data']['candidates'][0]['references'][0]
    assert reference['origin'] == 'video' and reference['timing']['start'] == 10
    assert reference['excerpt'] in source_article['sources'][0]['segments'][0]['text']
    assert reference['record_hash'] == proof['data']['foundations']['v1s1']['record_hash']
    assert 'before_article' not in item and 'after_article' not in item
    response = authed.post(f'/api/jobs/{source_article["id"]}/changes/{item["id"]}',
        json={'article_hash': item['base_hash'], 'action': 'apply'})
    assert response.status_code == 200, response.text
    saved = db.get_job(source_article['id'])
    assert saved['article']['markdown'] == item['changes'][0]['after'].join(before['markdown'].split(item['changes'][0]['before']))
    assert db.revisions(saved['id'])[0]['data'] == before
    reported = runtime.report(saved['id'])['last_result']
    assert reported['applied_count'] == 1
    assert reported['costs']['cost_per_applied_improvement_usd'] > 0
    undo = authed.post(f'/api/jobs/{saved["id"]}/changes/{item["id"]}',
        json={'article_hash': item['result_hash'], 'action': 'undo'})
    assert undo.status_code == 200, undo.text
    assert db.get_job(saved['id'])['article'] == before
    assert store.get_changes(saved['id'], item['id'])['status'] == 'undone'
    assert len(ledger(saved)) == 3


def test_opt_in_apply_requires_server_proof_and_old_hash_cannot_repeat_application(
        source_article, iec_provider):
    options = request(source_article, mode='apply')
    result = runtime.execute(source_article['id'], options)
    assert result['report']['applied_count'] == 1 and result['changes'][0]['status'] == 'applied'
    assert 'O motivo é permitir' in db.get_job(source_article['id'])['article']['markdown']
    assert len(ledger(source_article)) == 3
    with pytest.raises(changes.EditConflict):
        runtime.execute(source_article['id'], options)
    assert len(iec_provider['calls']) == 3


def test_restart_recovery_preserves_completed_detection_and_uncertain_ledger(
        source_article, iec_provider):
    iec_provider['scenario'] = 'clear'
    result = runtime.execute(source_article['id'], request(source_article))
    master = master_rows(source_article)[0]
    assert len(ledger(source_article)) == 1
    # Crash after the successful paid checkpoint but before the master receipt.
    master.pop('report', None)
    master.pop('finished_at', None)
    master.pop('status', None)
    with db.connect() as connection:
        connection.execute('UPDATE agent_runs SET status=?,data=? WHERE id=?',
            ('running', json.dumps(master), result['execution_id']))
    reserved = spending.reserve(source_article, .03, 'gpt-4.1-mini', 'older_uncertain_attempt')
    spending.finish(reserved)
    before = deepcopy(ledger(source_article))
    runtime.recover()
    assert master_rows(source_article)[0]['status'] == 'interrupted'
    assert ledger(source_article) == before
    resumed = runtime.execute(source_article['id'], request(source_article))
    assert resumed['execution_id'] == result['execution_id'] and resumed['status'] == 'no_change'
    assert len(iec_provider['calls']) == 1 and ledger(source_article) == before


def test_manual_https_sources_are_read_once_distinguished_and_never_promote_internal_notes(
        authed, source_article, iec_provider, monkeypatch):
    iec_provider['scenario'] = 'external'
    source_article['research'] = {'internal_context_only': True, 'sources': [
        {'id': 'rn1', 'text': 'Texto web antigo sem prova.', 'verified': True, 'kind': 'web_excerpt',
         'internal_context_only': True, 'url': 'https://docs.example.org/old'}]}
    db.save_job(source_article)
    text = ('Orientações institucionais sobre recipientes. '
            'Os furos permitem que o excesso de água saia do recipiente. '
            'Consulte as condições de cada material para aplicar o procedimento.')
    page = Mock(return_value=text)
    monkeypatch.setattr(runtime.intelligence_research.research, 'page_text', page)
    result = runtime.execute(source_article['id'], request(source_article,
        allow_external=True, external_urls=['https://docs.example.org/reference'],
        trusted_domains=['docs.example.org']))
    assert result['status'] == 'suggested', result
    assert page.call_count == 1 and len(iec_provider['calls']) == 3
    change = result['changes'][0]
    addition = change['changes'][0]['after'].removeprefix(change['changes'][0]['before'])
    assert '[Fonte complementar](https://docs.example.org/reference)' in addition
    assert '[[v1s1]]' not in addition and '[00:10]' not in addition and 'rn1' not in addition
    saved = db.get_job(source_article['id'])
    assert saved['research'] == source_article['research']
    assert saved['sources'] == source_article['sources']
    assert saved['article'] == source_article['article']
    proof = store.artifacts(saved['id'], 'iec_proof')[0]['data']
    reference = proof['candidates'][0]['references'][0]
    assert reference['origin'] == 'external_verified' and reference['publisher'] == 'docs.example.org'
    assert reference['fetched_at'] and reference['expires_at'] and reference['record_hash']
    assert 'rn1' not in proof['foundations']
    response = authed.post(f'/api/jobs/{saved["id"]}/changes/{change["id"]}',
        json={'article_hash': change['base_hash'], 'action': 'apply'})
    assert response.status_code == 200, response.text
    assert authed.get(f'/api/jobs/{saved["id"]}/export?format=html').status_code == 200


def test_expired_pending_master_refreshes_source_and_reapproves_without_rebuying_detection(
        source_article, iec_provider, monkeypatch):
    iec_provider['scenario'] = 'external'
    clock = [datetime(2026, 10, 10, 12, 30, tzinfo=timezone.utc)]
    fixed_epoch = clock[0].timestamp()
    monkeypatch.setattr(runtime.intelligence_research, '_now', lambda: clock[0])
    monkeypatch.setattr(runtime, 'datetime', NS(
        now=lambda _zone: clock[0], fromisoformat=datetime.fromisoformat))
    # Prove expiry itself invalidates the master, even inside a reused epoch key.
    monkeypatch.setattr(runtime.time, 'time', lambda: fixed_epoch)
    page_text = ('Orientações institucionais sobre recipientes. '
                 'Os furos permitem que o excesso de água saia do recipiente. '
                 'Consulte as condições de cada material para aplicar o procedimento.')
    page = Mock(return_value=page_text)
    monkeypatch.setattr(runtime.intelligence_research.research, 'page_text', page)
    options = request(source_article, allow_external=True,
        external_urls=['https://docs.example.org/reference'],
        trusted_domains=['docs.example.org'], freshness_hours=1)
    before = deepcopy(source_article['article'])
    first = runtime.execute(source_article['id'], options)
    assert first['status'] == 'suggested', first
    old_item = store.get_changes(source_article['id'], first['changes'][0]['id'])
    old_proof = next(value for value in store.artifacts(source_article['id'], 'iec_proof')
                     if value['version'] == old_item['iec_proof_version'])
    old_external = next(value for value in old_proof['data']['foundations'].values()
                        if value['origin'] == 'external_verified')
    clock[0] += timedelta(hours=2)
    with pytest.raises(changes.EditConflict, match='venceu|expir|fonte'):
        changes.decide(db.get_job(source_article['id']), deepcopy(old_item), 'apply', old_item['base_hash'])
    assert db.get_job(source_article['id'])['article'] == before
    second = runtime.execute(source_article['id'], options)
    assert second['status'] == 'suggested', second
    assert second['execution_id'] != first['execution_id']
    assert page.call_count == 2
    stages = [value['stage'] for value in iec_provider['calls']]
    assert stages == ['iec_detect', 'iec_compose', 'iec_validate', 'iec_compose', 'iec_validate']
    rows = ledger(source_article)
    assert len(rows) == 5
    assert {value['incremental_budget_namespace'] for value in rows} == {first['execution_id']}
    assert second['report']['costs']['namespace'] == first['report']['costs']['namespace']
    assert second['report']['costs']['calculated_usd'] > first['report']['costs']['calculated_usd']
    assert second['report']['costs']['calculated_usd'] == pytest.approx(sum(value['calculated_usd'] for value in rows))
    assert db.get_job(source_article['id'])['article'] == before
    assert store.get_changes(source_article['id'], old_item['id'])['status'] == 'pending'
    refreshed = store.artifacts(source_article['id'], 'iec_proof')
    new_proof = next(value for value in refreshed if value['version'] == second['changes'][0]['iec_proof_version'])
    new_external = next(value for value in new_proof['data']['foundations'].values()
                        if value['origin'] == 'external_verified')
    assert new_external['record_hash'] != old_external['record_hash']
    assert new_external['fetched_at'] == clock[0].isoformat()
    assert datetime.fromisoformat(new_external['expires_at']) > clock[0]
    repeated = runtime.execute(source_article['id'], options)
    assert repeated['execution_id'] == second['execution_id']
    assert repeated['report']['costs'] == second['report']['costs']
    assert len(iec_provider['calls']) == 5 and page.call_count == 2


@pytest.mark.parametrize('scenario,expected_calls,rejection', [
    ('later', 1, None), ('hypothesis', 2, 'unverified_hypothesis'),
    ('duplicate', 2, 'already_explained'), ('uncertain', 3, 'uncertain')])
def test_redundancy_unverified_hypothesis_and_semantic_uncertainty_keep_article(
        authed, source_article, iec_provider, scenario, expected_calls, rejection):
    iec_provider['scenario'] = scenario
    before = deepcopy(source_article['article'])
    result = runtime.execute(source_article['id'], request(source_article))
    assert result['status'] == 'no_change', result
    assert len(iec_provider['calls']) == expected_calls
    assert result['changes'] == [] and db.get_job(source_article['id'])['article'] == before
    if rejection:
        assert any(value['code'] == rejection for value in result['report']['rejections'])
    for format in FORMATS:
        assert authed.get(f'/api/jobs/{source_article["id"]}/export', params={'format': format}).status_code == 200


@pytest.mark.parametrize('scenario', ['bad_block', 'bad_evidence'])
def test_invalid_detection_ids_cannot_start_research_or_composition(
        source_article, iec_provider, scenario):
    iec_provider['scenario'] = scenario
    result = runtime.execute(source_article['id'], request(source_article,
        allow_external=True, external_urls=['https://docs.example.org/reference'],
        trusted_domains=['docs.example.org']))
    assert result['status'] == 'no_change', result
    assert [value['stage'] for value in iec_provider['calls']] == ['iec_detect']
    assert len(ledger(source_article)) == 1
    assert result['changes'] == [] and result['report']['rejections']
    assert db.get_job(source_article['id'])['article'] == source_article['article']


def test_discovery_budget_reserves_composition_and_validation_before_any_search(
        source_article, iec_provider):
    iec_provider['scenario'] = 'external'
    result = runtime.execute(source_article['id'], request(source_article,
        allow_external=True, trusted_domains=['docs.example.org'], max_calls=3))
    assert result['status'] == 'budget', result
    assert [value['stage'] for value in iec_provider['calls']] == ['iec_detect']
    assert len(ledger(source_article)) == 1 and result['changes'] == []
    assert db.get_job(source_article['id'])['article'] == source_article['article']


@pytest.mark.parametrize('field', ['article', 'brief', 'sources'])
def test_concurrent_manual_article_or_context_edit_survives_paid_usage_and_old_proposal(
        source_article, iec_provider, field):
    newer = {}

    def edit(_current, _schema, stage, _extra):
        if stage != 'iec_compose':
            return
        saved = db.get_job(source_article['id'])
        if field == 'article':
            saved['article']['markdown'] += '\n\nUma observação salva manualmente durante a análise.'
        elif field == 'brief':
            saved['brief']['instructions'] = 'Uma nova direção salva pelo usuário.'
        else:
            saved['sources'][0]['segments'][0]['speaker'] = {'label': 'Locutor revisado'}
        newer['value'] = deepcopy(saved[field])
        db.save_job(saved)

    iec_provider['hook'] = edit
    result = runtime.execute(source_article['id'], request(source_article))
    assert result['status'] == 'conflict', result
    saved = db.get_job(source_article['id'])
    assert saved[field] == newer['value']
    assert len(saved['usage']) == len(ledger(saved)) == 3
    assert not result['changes'] and not store.report(saved)['changes']
    assert delivery.describe(saved)['export_available']


@pytest.mark.parametrize('field', ['brief', 'sources'])
def test_apply_reloads_context_in_transaction_even_if_caller_article_is_identical(
        source_article, iec_provider, field):
    result = runtime.execute(source_article['id'], request(source_article))
    item = store.get_changes(source_article['id'], result['changes'][0]['id'])
    stale = deepcopy(db.get_job(source_article['id']))
    current = db.get_job(source_article['id'])
    if field == 'brief':
        current['brief']['instructions'] = 'Uma direção revisada em outra janela.'
    else:
        current['sources'][0]['segments'][0]['speaker'] = {'label': 'Uma revisão de autoria'}
    db.save_job(current)
    with pytest.raises(ValueError, match='fontes|fonte|direção'):
        changes.decide(stale, item, 'apply', item['base_hash'])
    assert db.get_job(current['id']) == current
    assert store.get_changes(current['id'], item['id'])['status'] == 'pending'
    assert not db.revisions(current['id'])


def test_stale_proposal_status_cannot_resurrect_rejected_change(source_article, iec_provider):
    result = runtime.execute(source_article['id'], request(source_article))
    item = store.get_changes(source_article['id'], result['changes'][0]['id'])
    current = db.get_job(source_article['id'])
    changes.decide(current, deepcopy(item), 'reject', item['base_hash'])
    with pytest.raises(ValueError, match='resolvida|versão'):
        changes.decide(source_article, item, 'apply', item['base_hash'])
    assert store.get_changes(current['id'], item['id'])['status'] == 'rejected'
    assert db.get_job(current['id'])['article'] == source_article['article']


def test_timeout_reservation_stays_uncertain_retry_reuses_completed_detection_and_redacts_body(
        authed, source_article, iec_provider, caplog):
    iec_provider.update(fail_stage='iec_compose', failure='timeout')
    options = request(source_article, max_calls=4)
    first = runtime.execute(source_article['id'], options)
    assert first['status'] == 'unavailable' and db.get_job(source_article['id'])['article'] == source_article['article']
    rows = ledger(source_article)
    assert len(rows) == 2 and sum(value['state'] == 'uncertain' for value in rows) == 1
    held = sum(value['reserved_usd'] for value in rows if value['state'] == 'uncertain')
    assert held > 0
    second = runtime.execute(source_article['id'], options)
    assert second['status'] == 'suggested', second
    assert [value['stage'] for value in iec_provider['calls']].count('iec_detect') == 1
    assert [value['stage'] for value in iec_provider['calls']].count('iec_compose') == 2
    assert len(ledger(source_article)) == 4
    assert sum(value['reserved_usd'] for value in ledger(source_article) if value['state'] == 'uncertain') == held
    assert 'secret private provider body' not in json.dumps(master_rows(source_article)) + caplog.text
    assert authed.get(f'/api/jobs/{source_article["id"]}/export?format=markdown').status_code == 200


def test_cancellation_propagates_preserves_paid_checkpoints_and_can_resume(source_article, iec_provider):
    iec_provider.update(fail_stage='iec_compose', failure=KeyboardInterrupt('cancelled by fixture'))
    options = request(source_article, max_calls=4)
    with pytest.raises(KeyboardInterrupt, match='cancelled by fixture'):
        runtime.execute(source_article['id'], options)
    assert master_rows(source_article)[0]['status'] == 'interrupted'
    assert len(ledger(source_article)) == 2
    assert db.get_job(source_article['id'])['article'] == source_article['article']
    runtime.recover()
    result = runtime.execute(source_article['id'], options)
    assert result['status'] == 'suggested', result
    assert [value['stage'] for value in iec_provider['calls']].count('iec_detect') == 1
    assert len(ledger(source_article)) == 4


@pytest.mark.parametrize('options,expected_calls', [({'max_calls': 1}, 1), ({'budget_usd': '0.000001'}, 0)])
def test_call_or_financial_limit_stops_new_requests_and_keeps_saved_article(
        authed, source_article, iec_provider, options, expected_calls):
    result = runtime.execute(source_article['id'], request(source_article, **options))
    assert result['status'] == 'budget', result
    assert len(ledger(source_article)) == expected_calls
    assert db.get_job(source_article['id'])['article'] == source_article['article']
    assert result['changes'] == []
    assert authed.get(f'/api/jobs/{source_article["id"]}/export?format=markdown').status_code == 200


def test_article_financial_limit_remains_enforced_with_a_larger_iec_allowance(
        source_article, iec_provider):
    source_article['financial_budget_usd'] = '0.000001'
    db.save_job(source_article)
    result = runtime.execute(source_article['id'], request(source_article, budget_usd='0.20'))
    assert result['status'] == 'budget', result
    assert len(ledger(source_article)) == 0
    assert spending.summary(db.get_job(source_article['id']), persist=False)['limit_usd'] == .000001
    assert db.get_job(source_article['id'])['article'] == source_article['article']


def test_same_request_concurrent_execution_returns_running_without_second_paid_dispatch(
        source_article, iec_provider):
    entered, finish = Event(), Event()

    def wait(_current, _schema, stage, _extra):
        if stage == 'iec_detect':
            entered.set()
            assert finish.wait(10)

    iec_provider['hook'] = wait
    iec_provider['scenario'] = 'clear'
    options = request(source_article)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.execute, source_article['id'], options)
        try:
            assert entered.wait(10)
            duplicate = runtime.execute(source_article['id'], options)
            assert duplicate['status'] == 'running' and duplicate['changes'] == []
            assert len(iec_provider['calls']) == 1
        finally:
            finish.set()
        assert first.result(10)['status'] == 'no_change'
    assert len(ledger(source_article)) == 1


def test_legacy_get_export_and_default_shadow_do_not_require_paid_key_or_backfill(
        authed, job, monkeypatch):
    opened = Mock(side_effect=AssertionError('Legacy reads must never call providers.'))
    monkeypatch.setattr(generation, 'client', opened)
    monkeypatch.delenv('EDITORIAL_INTELLIGENCE_MODE', raising=False)
    before = deepcopy(db.get_job(job['id']))
    endpoint = f'/api/jobs/{job["id"]}/editorial-intelligence'
    assert authed.get(endpoint).status_code == 200
    assert db.get_job(job['id']) == before and not store.artifacts(job['id'], 'editorial_intelligence')
    response = authed.post(endpoint, json={'article_hash': generation.article_hash(job['article'])})
    assert response.status_code == 200, response.text
    assert response.json()['report']['provider_calls'] == 0
    assert db.get_job(job['id']) == before and not ledger(job)
    assert len(store.artifacts(job['id'], 'editorial_intelligence')) == 1
    for format in FORMATS:
        assert authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format}).status_code == 200
    opened.assert_not_called()


@pytest.mark.parametrize('budget', ['0', '-1', 'NaN', 'Infinity', True])
def test_api_rejects_invalid_opt_in_budget_before_provider(authed, job, monkeypatch, budget):
    provider = Mock(side_effect=AssertionError('Validation must precede provider dispatch.'))
    monkeypatch.setattr(generation, 'structured', provider)
    response = authed.post(f'/api/jobs/{job["id"]}/editorial-intelligence', json=request(job, budget_usd=budget))
    assert response.status_code == 422, response.text
    provider.assert_not_called()


def test_api_authentication_conflict_and_active_key_checks_are_nonbillable(client, job, monkeypatch):
    provider = Mock(side_effect=AssertionError('Invalid request must not dispatch.'))
    monkeypatch.setattr(generation, 'structured', provider)
    endpoint = f'/api/jobs/{job["id"]}/editorial-intelligence'
    assert client.post(endpoint, json=request(job, article_hash='0' * 64)).status_code == 409
    assert client.post(endpoint, json=request(job)).status_code == 400
    client.cookies.clear()
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint, json=request(job, mode='shadow')).status_code == 401
    provider.assert_not_called()


def test_authenticated_active_api_uses_explicit_budget_and_reports_nonblocking_suggestions(
        authed, source_article, iec_provider, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'get_secret', lambda name: 'fixture-only-key' if name == 'openai_api_key' else '')
    endpoint = f'/api/jobs/{source_article["id"]}/editorial-intelligence'
    response = authed.post(endpoint, json=request(source_article))
    assert response.status_code == 200, response.text
    output = response.json()
    assert output['status'] == 'suggested' and output['changes'][0]['status'] == 'pending'
    assert output['report']['costs']['limit_usd'] == .20
    assert output['report']['costs']['calculated_usd'] > 0
    saved = authed.get(endpoint)
    assert saved.status_code == 200 and saved.json()['last_result']['accepted_count'] == 1
    assert db.get_job(source_article['id'])['article'] == source_article['article']
    assert authed.get(f'/api/jobs/{source_article["id"]}/export?format=markdown').status_code == 200


def test_shadow_projection_failure_redacts_private_error_and_does_not_block_export(
        authed, job, monkeypatch, caplog):
    monkeypatch.setenv('EDITORIAL_INTELLIGENCE_MODE', 'shadow')
    before = deepcopy(db.get_job(job['id']))
    private = 'secret-key private-transcript content'
    monkeypatch.setattr(intelligence, 'shadow', Mock(side_effect=RuntimeError(private)))
    response = authed.post(f'/api/jobs/{job["id"]}/editorial-intelligence', json={
        'article_hash': generation.article_hash(job['article']), 'mode': 'shadow'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable'
    assert private not in response.text + caplog.text
    assert db.get_job(job['id']) == before
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=html').status_code == 200


def test_corrupt_proof_is_rejected_before_any_saved_article_change(source_article, iec_provider):
    result = runtime.execute(source_article['id'], request(source_article))
    item = store.get_changes(source_article['id'], result['changes'][0]['id'])
    with db.connect() as connection:
        row = connection.execute("SELECT id,data FROM editorial_artifacts WHERE job_id=? AND kind='iec_proof'",
                                 (source_article['id'],)).fetchone()
        payload = json.loads(row['data'])
        payload['data']['changes'][0]['after'] += ' Um conteúdo inserido sem fundamento.'
        connection.execute('UPDATE editorial_artifacts SET data=? WHERE id=?', (json.dumps(payload), row['id']))
    with pytest.raises(changes.EditConflict, match='prova'):
        changes.decide(source_article, item, 'apply', item['base_hash'])
    assert db.get_job(source_article['id'])['article'] == source_article['article']
    assert not db.revisions(source_article['id'])


def test_pending_wordpress_reconciliation_and_external_edit_protection_remain_after_iec(
        authed, source_article, iec_provider, monkeypatch):
    result = runtime.execute(source_article['id'], request(source_article))
    before = deepcopy(source_article['article'])
    monkeypatch.setattr(wordpress, 'connection', lambda: (
        'https://blog.example', httpx.BasicAuth('fixture', 'fixture-password')))
    state = {'post': None, 'writes': 0, 'timeout': True}

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json=state['post'] if request.url.path.endswith('/42') else
                                  [state['post']] if state['post'] else [])
        state['writes'] += 1
        payload = json.loads(request.content)
        assert payload['status'] == 'pending'
        state['post'] = {'id': 42, 'status': 'pending', 'content': {'raw': payload['content']},
            'title': {'raw': payload['title']}, 'excerpt': {'raw': payload['excerpt']},
            'slug': payload['slug'], 'featured_media': payload['featured_media']}
        if state['timeout']:
            state['timeout'] = False
            raise httpx.ReadTimeout('Remote receipt not confirmed')
        return httpx.Response(201, json=state['post'])

    original_client = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs))
    endpoint = f'/api/jobs/{source_article["id"]}/wordpress'
    assert authed.post(endpoint, json={}).status_code == 400
    assert db.get_job(source_article['id'])['wordpress']['uncertain']
    assert authed.post(endpoint, json={}).status_code == 200
    assert state['post']['status'] == 'pending' and state['writes'] == 2
    assert db.get_job(source_article['id'])['article'] == before
    assert result['changes'][0]['status'] == 'pending'
    state['post']['content']['raw'] += '\n<p>Uma edição feita no WordPress.</p>'
    assert authed.post(endpoint, json={}).status_code == 400
    assert state['writes'] == 2
    assert authed.get(f'/api/jobs/{source_article["id"]}/export?format=html').status_code == 200
