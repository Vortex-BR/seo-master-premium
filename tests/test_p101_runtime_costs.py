"""Financial telemetry through real runtime entry points, mocked providers only."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app import cost_observability as costs, db, generation, pipeline, spending


def provider(*, usage=True):
    api = Mock()
    api.__enter__ = Mock(return_value=api)
    api.__exit__ = Mock(return_value=False)
    api.responses.input_tokens.count.return_value = NS(input_tokens=8)
    api.responses.create.return_value = NS(id='resp_test', status='completed', output=[],
        usage=NS(input_tokens=8, output_tokens=1, input_tokens_details=NS(cached_tokens=0)) if usage else None)
    return api


def report():
    return costs.read_report(Path(os.environ['DATA_DIR']) / 'seo.sqlite3')


def test_connection_test_is_bounded_observed_and_outside_article(authed, job, monkeypatch):
    api = provider()
    monkeypatch.setattr(generation, 'client', lambda: api)
    first = authed.post('/api/settings/test-openai').json()
    second = authed.post('/api/settings/test-openai').json()
    assert first['ok'] and second['ok']
    assert first['run_id'] != second['run_id']
    assert first['financial_state'] == 'completed'
    data = report()
    runs = [r for r in data['executions'] if r['scope'] == 'connection_test']
    assert len(runs) == 2
    assert spending.summary(job, persist=False)['spent_usd'] == 0
    assert db.get_job(job['id'])['usage'] == []
    assert api.responses.create.call_count == 2


def test_connection_guard_refuses_before_dispatch_and_unknown_usage_holds_reserve(authed, monkeypatch):
    api = provider(usage=False)
    monkeypatch.setattr(generation, 'client', lambda: api)
    db.set_setting('connection_test_budget_usd', 0.000001)
    assert authed.post('/api/settings/test-openai').status_code == 400
    api.responses.create.assert_not_called()
    db.set_setting('connection_test_budget_usd', 0.01)
    response = authed.post('/api/settings/test-openai').json()
    assert response['financial_state'] == 'uncertain'
    assert response['calculated_usd'] is None
    with db.connect() as c:
        import json
        row = json.loads(c.execute('SELECT data FROM spend_reservations WHERE id=?',
                                   (response['reservation_id'],)).fetchone()[0])
    assert row['reserved_usd'] > 0
    assert row.get('charged_usd') is None
    assert spending.response_usage(api.responses.create.return_value)['input_tokens'] is None


def test_two_pipeline_invocations_separate_runs_and_preserve_lifetime_spend(job, monkeypatch):
    monkeypatch.setattr(pipeline.local_audio, 'clean_cache', lambda: None)
    monkeypatch.setattr(pipeline, 'get_secret', lambda key: 'mock-key')
    monkeypatch.setattr(pipeline.transcripts, 'configuration', lambda: {'provider': 'youtube'})

    def fake_editorial(current, mode):
        ident = spending.reserve(current, 0.02, 'gpt-4.1-mini', 'writer')
        spending.finish(ident, {'input_tokens': 1000, 'output_tokens': 100,
                               'cached_input_tokens': 0, 'web_search_calls': 0})
        return current['review']

    monkeypatch.setattr(pipeline.engine, 'run', fake_editorial)
    pipeline.run(job['id'])
    first_total = spending.summary(db.get_job(job['id']), persist=False)['spent_usd']
    pipeline.run(job['id'], 'resume')
    data = report()
    runs = [r for r in data['executions'] if r['entity_id'] == job['id']]
    assert len(runs) == 2
    assert len({r['id'] for r in runs}) == 2
    assert spending.summary(db.get_job(job['id']), persist=False)['spent_usd'] == pytest.approx(2 * first_total)
    assert {r['operation'] for r in runs} == {'generate', 'resume'}
    assert all(len(r['attempts']) >= 1 for r in runs)


def test_authenticated_reports_preserve_database_wal_and_shm(authed, job):
    spending.reserve(job, 0.1, 'gpt-4.1-mini', 'writer')
    database = Path(os.environ['DATA_DIR']) / 'seo.sqlite3'
    import sqlite3
    keeper = sqlite3.connect(database)
    try:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('CREATE TABLE p101_probe (value)')
        keeper.commit()
        paths = [database, Path(str(database) + '-wal'), Path(str(database) + '-shm')]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
        assert authed.get('/api/costs/report').status_code == 200
        assert authed.get(f'/api/jobs/{job["id"]}/cost-report').status_code == 200
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
        assert after == before
    finally:
        keeper.close()


def test_report_routes_keep_auth_and_can_disable_without_budget_loss(authed, client, job, monkeypatch):
    # An expired/missing cookie cannot gain access to telemetry.
    saved = client.cookies.get('seo_session')
    client.cookies.clear()
    assert client.get('/api/costs/report').status_code == 401
    client.cookies.set('seo_session', saved)
    assert client.get('/api/jobs/unknown/cost-report').status_code == 404
    ident = spending.reserve(job, 0.1, 'gpt-4.1-mini', 'writer')
    before = spending.summary(job, persist=False)
    monkeypatch.setenv('COST_REPORTS_ENABLED', '0')
    assert authed.get('/api/costs/report').status_code == 503
    assert spending.summary(job, persist=False) == before
    assert ident
