import hashlib
import asyncio
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app import cost_observability as costs, db, spending


def measured(input_tokens=1000, output_tokens=100, cached_input_tokens=0):
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'cached_input_tokens': cached_input_tokens, 'web_search_calls': 0}


def database_path():
    return db.data_dir() / 'seo.sqlite3'


def file_state(path):
    return {str(candidate): (hashlib.sha256(candidate.read_bytes()).hexdigest(),
                            candidate.stat().st_mtime_ns, candidate.stat().st_size)
            for candidate in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm'))
            if candidate.exists()}


def test_two_generations_same_article_and_retries_have_distinct_costs(job):
    with costs.run(job, pipeline_version='editorial-7') as first:
        for _ in range(2):
            ident = spending.reserve(job, .01, 'gpt-4.1-mini', 'writer',
                                     metadata={'dependency_fingerprint': 'same-request'})
            spending.finish(ident, measured())
    with costs.run(job, operation='resume', pipeline_version='editorial-7') as second:
        ident = spending.reserve(job, .01, 'gpt-4.1-mini', 'writer',
                                 metadata={'dependency_fingerprint': 'different-request'})
        spending.finish(ident, measured())
    report = costs.read_report(database_path(), job_id=job['id'])
    runs = {row['id']: row for row in report['executions']}
    assert runs[first['id']]['costs']['calculated_usd'] == pytest.approx(.00112)
    assert runs[second['id']]['costs']['calculated_usd'] == pytest.approx(.00056)
    assert runs[first['id']]['retry_attempts'] == 1
    assert runs[first['id']]['retry_costs']['calculated_usd'] == pytest.approx(.00056)
    assert runs[second['id']]['retry_attempts'] == 0
    assert spending.summary(job)['spent_usd'] == pytest.approx(.001932)
    assert report['metrics']['p50_usd'] is None


def test_missing_usage_and_timeout_stay_inconclusive_not_zero(job):
    with costs.run(job) as execution:
        ident = spending.reserve(job, .12, 'gpt-4.1-mini', 'writer')
        row = spending.finish(ident)
    assert row['state'] == 'uncertain'
    run = costs.read_report(database_path(), run_id=execution['id'])['executions'][0]
    assert run['costs']['calculated_usd'] is None
    assert run['costs']['known_calculated_usd'] is None
    assert run['costs']['inconclusive_reserve_usd'] == .12
    assert run['costs']['invoice_usd'] is None
    assert spending.response_usage(NS(usage=None, output=[])) == {
        'input_tokens': None, 'output_tokens': None, 'cached_input_tokens': None, 'web_search_calls': 0}
    partial = spending.finish(spending.reserve(job, .12, 'gpt-4.1-mini', 'writer'),
                              {'input_tokens': 100, 'output_tokens': None})
    assert partial['state'] == 'uncertain' and partial['charged_usd'] is None


def test_uncertain_response_keeps_provider_identifier_and_partial_usage(job):
    api = Mock()
    api.responses.input_tokens.count.return_value = NS(input_tokens=100)
    api.responses.create.return_value = NS(id='response-partial', output=[],
                                          usage=NS(input_tokens=12, output_tokens=None))
    _, row = spending.create_response(job, api,
                                     {'model': 'gpt-4.1-mini', 'input': 'test', 'max_output_tokens': 100},
                                     'writer')
    assert row['state'] == 'uncertain'
    assert row['response_id'] == 'response-partial'
    assert row['usage']['input_tokens'] == 12
    assert row['usage']['output_tokens'] is None


@pytest.mark.parametrize('error', [KeyboardInterrupt(), asyncio.CancelledError()])
def test_cancelled_provider_attempt_keeps_uncertain_reservation(job, error):
    api = Mock()
    api.responses.input_tokens.count.return_value = NS(input_tokens=100)
    api.responses.create.side_effect = error
    with pytest.raises(type(error)):
        spending.create_response(job, api,
                                 {'model': 'gpt-4.1-mini', 'input': 'test', 'max_output_tokens': 100},
                                 'writer')
    report = costs.read_report(database_path())
    assert report['legacy'][0]['state'] == 'uncertain'
    assert spending.summary(job)['reserved_usd'] > 0


def test_audio_duration_cost_excludes_safety_and_does_not_invent_tokens(job):
    with costs.run(job) as execution:
        ident = spending.reserve(job, .0069, 'whisper-1', 'audio_transcription')
        row = spending.finish(ident, {'estimated_usd': .0069, 'duration_seconds': 60,
                                     'provider_duration_seconds': 60,
                                     'input_tokens': None, 'output_tokens': None})
    assert row['calculated_usd'] == .006
    assert row['charged_usd'] == .0069
    report = costs.read_report(database_path(), run_id=execution['id'])
    assert report['executions'][0]['costs']['calculated_usd'] == .006
    assert report['executions'][0]['attempts'][0]['usage']['input_tokens'] is None


def test_tariff_snapshot_calculation_ignores_later_rate_changes(job, monkeypatch):
    ident = spending.reserve(job, .02, 'gpt-4.1-mini', 'writer')
    monkeypatch.setitem(spending.TEXT_RATES, 'gpt-4.1-mini', (40, 10, 160, 1047576))
    row = spending.finish(ident, measured())
    assert row['calculated_usd'] == pytest.approx(.00056)
    assert row['charged_usd'] == pytest.approx(.000644)
    assert row['pricing_snapshot']['rates'][:3] == [.4, .1, 1.6]


def test_application_cache_provider_cache_and_external_cost_unknown_are_distinct(job):
    with costs.run(job) as execution:
        costs.record_cache(job, 'source', dependency_fingerprint='source-cache')
        ident = spending.reserve(job, .01, 'gpt-4.1-mini', 'writer')
        spending.finish(ident, measured(cached_input_tokens=500))
        costs.record_external(job, 'audio_transcription', provider='local-whisper')
    run = costs.read_report(database_path(), run_id=execution['id'])['executions'][0]
    assert run['costs']['application_cache_hits'] == 1
    assert run['costs']['provider_cached_input_tokens'] == 500
    assert run['costs']['calculated_usd'] is None
    assert run['costs']['known_calculated_usd'] == pytest.approx(.00041)
    assert run['costs']['unmeasured_events'] == 1
    assert run['costs']['infrastructure_usd'] is None


def test_partial_provider_cache_coverage_does_not_turn_known_subtotal_into_total(job):
    with costs.run(job) as execution:
        one = spending.reserve(job, .01, 'gpt-4.1-mini', 'writer')
        spending.finish(one, measured(cached_input_tokens=500))
        two = spending.reserve(job, .01, 'gpt-4.1-mini', 'review')
        spending.finish(two, measured(cached_input_tokens=None))
    row = costs.read_report(database_path(), run_id=execution['id'])['executions'][0]
    assert row['costs']['provider_cached_input_tokens'] is None
    assert row['costs']['known_provider_cached_input_tokens'] == 500
    assert row['costs']['provider_cache_coverage'] == {'applicable_calls': 2, 'measured_calls': 1}


def test_optional_provider_event_is_durable_before_dispatch_and_retains_timeout(job):
    with costs.run(job) as execution:
        with pytest.raises(TimeoutError):
            with costs.external_attempt(job, 'transcription', provider='third-party') as event:
                with db.connect() as connection:
                    before = json.loads(connection.execute('SELECT data FROM cost_events WHERE id=?',
                                                           (event['id'],)).fetchone()['data'])
                assert before['state'] == 'unmeasured_pending'
                raise TimeoutError('credential must never enter the ledger')
    run = costs.read_report(database_path(), run_id=execution['id'])['executions'][0]
    event = run['attempts'][0]
    assert event['state'] == 'uncertain' and event['duration_seconds'] >= 0
    assert event['error_type'] == 'TimeoutError'
    assert 'credential' not in json.dumps(event)
    assert run['costs']['calculated_usd'] is None


def test_readonly_report_preserves_database_wal_shm_and_does_not_initialize_tables(job):
    path = database_path()
    # Keep an active WAL writer so committed source data remains in its sidecar.
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO settings VALUES (?,?)', ('audit-wal-test', '1'))
        writer.commit()
        before = file_state(path)
        report = costs.read_report(path, job_id=job['id'])
        assert file_state(path) == before
        assert report['executions'] == []
        assert Path(str(path) + '-wal').name in report['source_sha256']
        assert writer.execute("SELECT 1 FROM sqlite_master WHERE name='cost_runs'").fetchone() is None
        assert writer.execute("SELECT 1 FROM sqlite_master WHERE name='spend_reservations'").fetchone() is None
    finally:
        writer.close()


def test_missing_database_report_does_not_create_database_or_parent(tmp_path):
    path = tmp_path / 'absent' / 'db.sqlite3'
    with pytest.raises(ValueError, match='inexistente'):
        costs.read_report(path)
    assert not path.parent.exists()


def test_legacy_usage_remains_unassigned_and_unmeasured_does_not_receive_free_budget(job):
    job['usage'] = [{'stage': 'writer', 'model': 'unknown-historical-model', 'response_id': 'old'}]
    db.save_job(job)
    report = costs.read_report(database_path())
    assert report['executions'] == []
    assert report['legacy'][0]['run_id'] is None
    assert report['legacy'][0]['estimated_usd'] is None
    assert report['legacy_costs']['calculated_usd'] is None
    initial = spending.summary(job)
    assert initial['reserved_usd'] == 1
    db.set_setting('editorial_profile', {'max_spend_usd': 10})
    assert spending.summary(job)['remaining_usd'] == 0
    with pytest.raises(spending.SpendLimitExceeded, match='histórico'):
        spending.reserve(job, .01, 'gpt-4.1-mini', 'writer')


def test_reconcile_timeout_requires_evidence_and_records_actor_and_history(job):
    ident = spending.reserve(job, .1, 'gpt-4.1-mini', 'writer')
    spending.finish(ident)
    with pytest.raises(ValueError, match='evidência'):
        spending.reconcile(ident, actor='operator', evidence={}, unbilled=True)
    with pytest.raises(ValueError, match='ausente'):
        spending.reconcile(ident, actor='operator',
                           evidence={'kind': 'provider_usage', 'reference': 'timeout'}, unbilled=True)
    assert spending.summary(job)['reserved_usd'] == .1
    reconciled = spending.reconcile(ident, actor='operator', registered_usd=.007,
                                   evidence={'kind': 'provider_billing_record', 'reference': 'receipt-test'})
    assert reconciled['registered_usd'] == .007
    assert reconciled['reconciliations'][0]['before']['state'] == 'uncertain'
    assert reconciled['reconciliations'][0]['actor'] == 'operator'
    assert spending.summary(job)['reserved_usd'] == 0
    assert spending.summary(job)['spent_usd'] == .007


def test_connection_scope_uses_own_operator_budget_and_does_not_charge_article(job):
    connection = {'id': 'connection-test:one', 'financial_budget_usd': .01}
    with costs.run(connection, scope='connection_test', operation='openai') as execution:
        ident = spending.reserve(connection, .001, 'gpt-4.1-mini', 'connection_test')
        spending.finish(ident, measured())
    report = costs.read_report(database_path(), job_id=connection['id'])
    assert report['executions'][0]['id'] == execution['id']
    assert report['executions'][0]['scope'] == 'connection_test'
    assert spending.summary(job)['spent_usd'] == 0
    assert spending.summary(connection)['spent_usd'] > 0


def test_cancelled_run_and_caller_absorbed_error_are_not_success_samples(job):
    with pytest.raises(KeyboardInterrupt):
        with costs.run(job):
            raise KeyboardInterrupt()
    with costs.run(job) as execution:
        execution['outcome'] = 'error'
    report = costs.read_report(database_path())
    assert {row['status'] for row in report['executions']} == {'cancelled', 'failed'}
    assert report['metrics']['sample_size'] == 0


def test_percentiles_require_twenty_complete_samples_and_report_window_and_version(job):
    for _ in range(20):
        with costs.run(job, pipeline_version='editorial-7'):
            ident = spending.reserve(job, .001, 'gpt-4.1-mini', 'writer')
            spending.finish(ident, measured())
    report = costs.read_report(database_path())
    assert report['metrics']['sample_size'] == 20
    assert report['metrics']['p50_usd'] == pytest.approx(.00056)
    assert report['metrics']['p95_usd'] == pytest.approx(.00056)
    assert report['metrics']['window']['from'] and report['metrics']['window']['to']
    assert report['metrics']['pipeline_versions'] == ['editorial-7']


def test_different_scopes_and_versions_are_not_mixed_in_cost_percentiles(job):
    with costs.run(job, scope='article', pipeline_version='editorial-7'):
        ident = spending.reserve(job, .001, 'gpt-4.1-mini', 'writer')
        spending.finish(ident, measured())
    with costs.run({'id': 'test-connection'}, scope='connection_test', pipeline_version='api-1'):
        ident = spending.reserve({'id': 'test-connection'}, .001, 'gpt-4.1-mini', 'connection')
        spending.finish(ident, measured())
    report = costs.read_report(database_path())
    assert report['metrics']['aggregation'] == 'cross_scope_version_or_operation_no_percentiles'
    assert set(report['metrics']['cohorts']) == {'article:editorial-7:generation', 'connection_test:api-1:generation'}
    assert report['metrics']['p50_usd'] is None
    assert report['metrics']['cohorts']['article:editorial-7:generation']['sample_size'] == 1


def test_unassigned_optional_work_remains_visible_without_fabricating_generation(job):
    event = costs.record_external(None, 'stock_lookup', provider='stock-service')
    report = costs.read_report(database_path())
    assert report['executions'] == []
    assert report['unassigned_events'][0]['id'] == event['id']
    assert report['unassigned_costs']['unmeasured_events'] == 1
    assert report['unassigned_costs']['calculated_usd'] is None


def test_legacy_incomplete_null_model_and_recorded_estimate_are_readable(job):
    job['usage'] = [{'model': None, 'input_tokens': None}, {'estimated_usd': .1}]
    db.save_job(job)
    report = costs.read_report(database_path())
    assert len(report['legacy']) == 2
    assert report['legacy_costs']['known_estimated_usd'] == .1
    assert report['legacy_costs']['calculated_usd'] is None


def test_reuse_from_existing_job_source_counts_application_hit(job):
    with costs.run(job) as execution:
        costs.record_cache(job, 'transcript', origin='job_source_cache')
    report = costs.read_report(database_path(), run_id=execution['id'])
    assert report['executions'][0]['costs']['application_cache_hits'] == 1
    assert report['executions'][0]['costs']['calculated_usd'] == 0
    assert report['executions'][0]['costs']['infrastructure_usd'] is None


def test_restart_recovery_keeps_all_holds_and_marks_inflight_unknown_atomically(job):
    ident = spending.reserve(job, .1, 'gpt-4.1-mini', 'writer')
    with db.connect() as connection:
        costs.init(connection)
        connection.execute('INSERT INTO cost_runs VALUES (?,?,?)',
                           ('crashed-run', job['id'], json.dumps({'id': 'crashed-run', 'status': 'running',
                                                               'duration_seconds': None})))
        connection.execute('INSERT INTO cost_events VALUES (?,?,?,?)',
                           ('crashed-event', job['id'], 'crashed-run',
                            json.dumps({'id': 'crashed-event', 'state': 'unmeasured_pending',
                                        'registered_usd': None})))
    result = costs.recover_inflight()
    assert result == {'reservations': 1, 'runs': 1, 'external_events': 1, 'invalid_rows': 0}
    assert costs.recover_inflight() == {'reservations': 0, 'runs': 0, 'external_events': 0, 'invalid_rows': 0}
    with db.connect() as connection:
        reservation = json.loads(connection.execute('SELECT data FROM spend_reservations WHERE id=?',
                                                     (ident,)).fetchone()['data'])
        run = json.loads(connection.execute('SELECT data FROM cost_runs').fetchone()['data'])
        event = json.loads(connection.execute('SELECT data FROM cost_events').fetchone()['data'])
    assert reservation['state'] == 'uncertain' and reservation['reserved_usd'] == .1
    assert run['status'] == 'interrupted' and run['duration_seconds'] is None
    assert event['state'] == 'uncertain' and event['registered_usd'] is None
    assert spending.summary(job)['reserved_usd'] == .1


def test_recovery_on_legacy_database_creates_no_telemetry_tables(job):
    result = costs.recover_inflight()
    assert result == {'reservations': 0, 'runs': 0, 'external_events': 0, 'invalid_rows': 0}
    with db.connect() as connection:
        for table in ('spend_reservations', 'cost_runs', 'cost_events'):
            assert connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() is None
