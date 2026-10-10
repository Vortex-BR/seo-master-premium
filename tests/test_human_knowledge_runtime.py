"""Shadow HKL observes the real editorial flow without changing paid deliveries.

The existing newsroom fixture replaces the provider boundary only. Requests,
schemas, checkpoints, financial reservations and exports use production code.
All databases and HTTP boundaries in this module are isolated test fixtures.
"""
from copy import deepcopy
from datetime import datetime, timezone
from itertools import count
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from app import db, generation, pipeline, publishing, spending, wordpress
from app.editorial import delivery, store, workflow
from app.editorial.contracts import EditorialPlan, VideoFidelityReview


FORMATS = ('markdown', 'html', 'json', 'wordpress', 'wordpress-html')


def hkl_artifacts(job):
    return store.artifacts(job['id'], 'human_knowledge')


def ledger_rows(job):
    with db.connect() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='spend_reservations'").fetchone():
            return []
        return [json.loads(row['data']) for row in connection.execute(
            'SELECT data FROM spend_reservations WHERE job_id=?', (job['id'],))]


def install_provider_recorder(monkeypatch, newsroom_ai, *, fail=None):
    """Measure actual prepared requests and simulate known, nonzero provider usage."""
    captured = []
    opened = Mock(side_effect=AssertionError('A live provider must not be opened.'))
    monkeypatch.setattr(generation, 'client', opened)

    def respond(current, schema, instruction, stage, extra=None):
        prepared = generation.prepare_structured(current, schema, instruction, stage, extra)
        captured.append({'stage': stage, 'schema': schema.model_json_schema(),
                         'instruction': instruction, 'payload': deepcopy(extra),
                         'request': deepcopy(prepared[0])})
        reservation_id = spending.reserve(current, .02, 'gpt-4.1-mini', stage)
        response = NS(id='mock-response-' + reservation_id, status='completed', output=[],
                      usage=NS(input_tokens=1000, output_tokens=100,
                               input_tokens_details=NS(cached_tokens=100)))
        financial = spending.finish(reservation_id, spending.response_usage(response),
                                    response_id=response.id)
        generation.record_usage(current, response, stage, financial,
                                request_model='gpt-4.1-mini')
        if fail:
            fail(current, schema, instruction, stage, extra)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    return captured, opened


def reset_job_fixture(original):
    """Reset only this test job in the disposable fixture database."""
    with db.connect() as connection:
        for table in ('agent_runs', 'agent_messages', 'change_sets', 'editorial_artifacts',
                      'editorial_issues', 'spend_reservations', 'revisions'):
            connection.execute(f'DELETE FROM {table} WHERE job_id=?', (original['id'],))
        for table in ('cost_runs', 'cost_events'):
            connection.execute(f'DELETE FROM {table} WHERE entity_id=?', (original['id'],))
    db.save_job(deepcopy(original))


def paid_usage(rows):
    keys = ('stage', 'model', 'input_tokens', 'output_tokens', 'cached_input_tokens',
            'web_search_calls', 'calculated_usd', 'estimated_usd', 'financial_state')
    return [{key: row.get(key) for key in keys} for row in rows]


def checkpoint_fingerprints(job):
    return {row['data']['slot']: row['input_hash'] for row in store.report(job)['runs']}


def test_one_video_shadow_preserves_exact_paid_requests_checkpoints_costs_and_exports(
        authed, job, newsroom_ai, monkeypatch):
    monkeypatch.setattr(db, 'now', lambda: '2026-10-10T12:00:00+00:00')
    monkeypatch.setattr(publishing, 'datetime', NS(now=lambda _zone: datetime(
        2026, 10, 10, 12, 0, tzinfo=timezone.utc)))
    original = deepcopy(job)
    captures, live_provider = install_provider_recorder(monkeypatch, newsroom_ai)
    observations = {}
    for mode in ('off', 'shadow'):
        monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', mode)
        identifiers = count(1)
        monkeypatch.setattr(store, 'new_id', lambda: f'controlled-run-{next(identifiers)}')
        if mode == 'shadow':
            reset_job_fixture(original)
        start = len(captures)
        pipeline.run(job['id'])
        saved = db.get_job(job['id'])
        assert saved['generation_complete'] and delivery.describe(saved)['export_available']
        assert len(saved['sources']) == 1
        exports = {format: authed.get(f'/api/jobs/{job["id"]}/export',
                   params={'format': format}) for format in FORMATS}
        assert all(response.status_code == 200 for response in exports.values())
        observations[mode] = {'requests': captures[start:], 'article': saved['article'],
                             'usage': paid_usage(saved['usage']),
                             'spending': spending.summary(saved, persist=False),
                             'checkpoints': checkpoint_fingerprints(saved),
                             'dependencies': workflow.dependencies(saved),
                             'apuration_version': saved['apuration']['version'],
                             'plan_version': saved['plan']['version'],
                             'exports': {format: response.content
                                         for format, response in exports.items()}}
        assert len(ledger_rows(saved)) == 4
        assert bool(hkl_artifacts(saved)) is (mode == 'shadow')
        assert 'human_knowledge' not in saved
    assert observations['shadow'] == observations['off']
    assert [request['stage'] for request in observations['shadow']['requests']] == [
        'extractor', 'planner', 'writer', 'fact_reviewer']
    assert observations['shadow']['spending']['spent_usd'] > 0
    assert observations['shadow']['spending']['reserved_usd'] == 0
    live_provider.assert_not_called()


def test_default_shadow_observes_extract_only_without_paid_generation(
        job, monkeypatch):
    monkeypatch.delenv('HUMAN_KNOWLEDGE_MODE', raising=False)
    opened = Mock(side_effect=AssertionError('Extract-only must not open AI.'))
    monkeypatch.setattr(generation, 'client', opened)
    original = deepcopy(job['sources'])
    pipeline.run(job['id'], 'extract')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'sources_ready'
    assert saved['sources'] == original
    assert hkl_artifacts(saved)
    assert store.artifacts(saved['id'], 'human_knowledge_sources')
    assert saved['usage'] == [] and not ledger_rows(saved)
    assert saved['article'] == job['article']
    with db.connect() as connection:
        events = [json.loads(row['data']) for row in connection.execute(
            'SELECT data FROM cost_events WHERE entity_id=?', (job['id'],))]
    shadows = [event for event in events if event['stage'] == 'human_knowledge_shadow']
    assert len(shadows) == 1
    assert shadows[0]['calculated_usd'] == 0
    assert shadows[0]['infrastructure_usd'] is None
    assert shadows[0]['registered_usd'] is None
    assert shadows[0]['metadata']['incremental_ai_requests'] == 0
    assert shadows[0]['metadata']['incremental_input_tokens'] == 0
    assert shadows[0]['metadata']['incremental_output_tokens'] == 0
    opened.assert_not_called()


def test_shadow_resume_reuses_paid_cache_and_deduplicates_identical_sidecars(
        job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    captured, opened = install_provider_recorder(monkeypatch, newsroom_ai)
    pipeline.run(job['id'])
    first = db.get_job(job['id'])
    versions = {artifact['version'] for artifact in hkl_artifacts(first)}
    checkpoints = checkpoint_fingerprints(first)
    costs = spending.summary(first, persist=False)
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert len(captured) == 4
    assert checkpoint_fingerprints(saved) == checkpoints
    assert saved['article'] == first['article']
    assert spending.summary(saved, persist=False) == costs
    assert {artifact['version'] for artifact in hkl_artifacts(saved)} == versions
    assert len(ledger_rows(saved)) == 4
    assert saved['editorial']['cycle_id'] == first['editorial']['cycle_id']
    opened.assert_not_called()


def test_shadow_can_enable_between_saved_plan_and_write_without_rebuying_plan(
        job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'off')
    captured, opened = install_provider_recorder(monkeypatch, newsroom_ai)
    pipeline.run(job['id'], 'plan')
    planned = db.get_job(job['id'])
    assert planned['status'] == 'plan_ready' and len(captured) == 2
    assert not hkl_artifacts(planned)
    dependencies = workflow.dependencies(planned)
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    pipeline.run(job['id'], 'write')
    saved = db.get_job(job['id'])
    assert [request['stage'] for request in captured] == [
        'extractor', 'planner', 'writer', 'fact_reviewer']
    assert saved['editorial']['cycle_id'] == planned['editorial']['cycle_id']
    assert saved['apuration']['version'] == planned['apuration']['version']
    assert saved['plan']['version'] == planned['plan']['version']
    assert workflow.dependencies(saved) == dependencies
    assert hkl_artifacts(saved) and delivery.describe(saved)['export_available']
    opened.assert_not_called()


@pytest.mark.parametrize('failure', ('error', 'cancel'))
def test_shadow_preserves_available_sources_and_article_after_failed_or_cancelled_attempt(
        authed, job, newsroom_ai, monkeypatch, failure):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')

    def fail(_current, schema, _instruction, _stage, _extra):
        if schema is EditorialPlan:
            if failure == 'cancel':
                raise KeyboardInterrupt('Test cancellation')
            raise RuntimeError('private provider error')

    captured, opened = install_provider_recorder(monkeypatch, newsroom_ai, fail=fail)
    if failure == 'cancel':
        with pytest.raises(KeyboardInterrupt, match='Test cancellation'):
            pipeline.run(job['id'])
    else:
        pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert [request['stage'] for request in captured] == ['extractor', 'planner']
    assert saved['article'] == job['article'] and saved['sources'] == job['sources']
    assert hkl_artifacts(saved) and delivery.describe(saved)['export_available']
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200
    assert spending.summary(saved, persist=False)['spent_usd'] > 0
    assert 'private provider error' not in (saved.get('error') or '')
    opened.assert_not_called()


def test_shadow_retry_after_planner_error_does_not_rebuy_completed_extraction(
        job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    failed = False

    def fail_once(_current, schema, _instruction, _stage, _extra):
        nonlocal failed
        if schema is EditorialPlan and not failed:
            failed = True
            raise RuntimeError('Provider failed after dispatch')

    captured, opened = install_provider_recorder(monkeypatch, newsroom_ai, fail=fail_once)
    pipeline.run(job['id'])
    first = db.get_job(job['id'])
    assert first['status'] == 'error'
    snapshot_versions = {artifact['version'] for artifact in hkl_artifacts(first)}
    cycle = first['editorial']['cycle_id']
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert [request['stage'] for request in captured] == [
        'extractor', 'planner', 'planner', 'writer', 'fact_reviewer']
    assert saved['editorial']['cycle_id'] == cycle
    assert saved['article'] and saved['generation_complete']
    assert snapshot_versions <= {artifact['version'] for artifact in hkl_artifacts(saved)}
    assert len(ledger_rows(saved)) == 5
    opened.assert_not_called()


def test_shadow_projection_failure_is_nonblocking_and_redacts_private_details(
        authed, job, newsroom_ai, monkeypatch, caplog):
    from app.editorial import human_knowledge

    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    captured, opened = install_provider_recorder(monkeypatch, newsroom_ai)
    secret = 'PRIVATE_SOURCE_TOKEN_never_persist_this'
    broken = Mock(side_effect=RuntimeError(secret))
    monkeypatch.setattr(human_knowledge, 'persist_shadow', broken)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['generation_complete'] and saved['error'] is None
    assert len(captured) == 4 and broken.called
    assert delivery.describe(saved)['export_available']
    for format in FORMATS:
        assert authed.get(f'/api/jobs/{job["id"]}/export',
                          params={'format': format}).status_code == 200
    with db.connect() as connection:
        rows = [row['data'] for row in connection.execute(
            'SELECT data FROM cost_events WHERE entity_id=?', (job['id'],))]
    assert any('human_knowledge' in row for row in rows)
    assert secret not in json.dumps(saved) + ''.join(rows) + caplog.text
    opened.assert_not_called()


def test_shadow_observer_must_not_swallow_baseexception(job, monkeypatch):
    from app.editorial import human_knowledge

    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    monkeypatch.setattr(human_knowledge, 'persist_shadow',
                        Mock(side_effect=KeyboardInterrupt('Observer cancellation')))
    with pytest.raises(KeyboardInterrupt, match='Observer cancellation'):
        pipeline.run(job['id'], 'extract')
    assert db.get_job(job['id'])['article'] == job['article']
    assert not ledger_rows(job)


def test_legacy_article_reads_and_exports_never_backfill_or_require_hkl(authed, job):
    before = deepcopy(db.get_job(job['id']))
    for _ in range(2):
        assert authed.get(f'/api/jobs/{job["id"]}').status_code == 200
        assert authed.get(f'/api/jobs/{job["id"]}/artifacts').status_code == 200
        for format in FORMATS:
            response = authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format})
            assert response.status_code == 200
    assert db.get_job(job['id']) == before
    assert not hkl_artifacts(job)
    assert not store.artifacts(job['id'], 'human_knowledge_sources')


def test_shadow_review_on_legacy_article_keeps_saved_text_and_pending_notes(
        authed, job, newsroom_ai, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    previous = deepcopy(job['article'])

    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is VideoFidelityReview:
            output['editorial_alignment'] = {'matches_brief': False,
                'reason': 'Uma observação editorial não exige expandir o artigo.', 'passage': ''}
        return output

    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'review')
    saved = db.get_job(job['id'])
    assert saved['article'] == previous and hkl_artifacts(saved)
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['fact_reviewer']
    assert saved['review']['findings']
    assert all(finding['export_blocking'] is False for finding in saved['review']['findings'])
    assert delivery.describe(saved)['export_available']
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200


def test_shadow_unknown_locutor_and_missing_timing_do_not_block_pending_wordpress(
        authed, job, monkeypatch):
    from app.editorial import human_knowledge

    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    segment = job['sources'][0]['segments'][0]
    segment.update(start=None, end=None)
    original = deepcopy(job['article'])
    db.save_job(job)
    human_knowledge.persist_shadow(job)
    assert job['article'] == original
    monkeypatch.setattr(wordpress, 'connection', lambda: (
        'https://blog.example', httpx.BasicAuth('test-user', 'test-password')))
    writes = []

    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[])
        payload = json.loads(request.content)
        writes.append(payload)
        return httpx.Response(201, json={'id': 42, 'status': 'pending',
            'title': {'raw': payload['title']}, 'content': {'raw': payload['content']},
            'excerpt': {'raw': payload['excerpt']}, 'slug': payload['slug'],
            'featured_media': payload['featured_media']})

    http_client = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kwargs: http_client(
        transport=httpx.MockTransport(handle), **kwargs))
    expected_content = publishing.render(job, gutenberg=True)
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 200, response.text
    assert writes[0]['status'] == 'pending'
    assert expected_content in writes[0]['content']
    assert 'human_knowledge' not in writes[0]['content']
    assert db.get_job(job['id'])['article'] == original


def test_incomplete_source_attempt_keeps_legacy_article_and_shadow_source_snapshot(
        authed, job, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    source = job['sources'][0]
    source.update(status='unavailable', segments=[])
    db.save_job(job)
    monkeypatch.setattr(pipeline.youtube, 'extract', Mock(side_effect=ValueError('No transcript available.')))
    monkeypatch.setattr(pipeline.youtube, 'metadata', lambda _id: {
        'title': source['title'], 'video_id': source['video_id'], 'url': source['url']})
    writer = Mock(side_effect=AssertionError('No source must not generate content.'))
    monkeypatch.setattr(generation, 'structured', writer)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'sources_unavailable'
    assert saved['article'] == job['article'] and hkl_artifacts(saved)
    assert store.artifacts(saved['id'], 'human_knowledge_sources')
    assert saved['usage'] == [] and not ledger_rows(saved)
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200
    writer.assert_not_called()
