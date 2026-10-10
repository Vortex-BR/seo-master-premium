"""Reports preserve available historical evidence without inventing current runs."""
import json
from pathlib import Path

from app import cost_observability as costs, db


def report(**filters):
    return costs.read_report(db.data_dir() / 'seo.sqlite3', **filters)


def test_strategic_legacy_usage_and_recorded_estimate_remain_visible(authed):
    cycle = {'id': 'legacy-cycle', 'usage': [{'response_id': 'legacy-response',
             'stage': 'strategy_business', 'model': 'gpt-4.1-mini', 'estimated_usd': 0.123}]}
    with db.connect() as c:
        c.execute('INSERT INTO strategy_cycles VALUES (?,?,?,?,?,?)',
                  (cycle['id'], 'default', 'ready', db.now(), db.now(), json.dumps(cycle)))
    data = report()
    assert not data['executions']
    row, = data['legacy']
    assert row['entity_id'] == cycle['id'] and row['scope'] == 'strategy_cycle'
    assert row['estimated_usd'] == 0.123 and row['calculated_usd'] is None
    assert row['pricing_basis'] == 'recorded_local_estimate_with_possible_margin'
    assert report(run_id='not-a-current-run')['legacy_costs']['known_estimated_usd'] is None


def test_legacy_ledger_identity_uses_original_sql_column(job):
    from app import spending
    with db.connect() as c:
        spending.init(c)
        c.execute('INSERT INTO spend_reservations VALUES (?,?,?)',
                  ('legacy-reserve', job['id'], json.dumps({'id': 'legacy-reserve',
                    'state': 'completed', 'charged_usd': 0.1, 'stage': 'writer'})))
    row, = report()['legacy']
    assert row['entity_id'] == job['id']
    assert row['calculated_usd'] is None
    assert row['guard_accounted_usd'] == 0.1


def test_generation_and_review_percentiles_use_distinct_operations(job):
    for operation, usd in (('generate', 1.0), ('review', 0.01)):
        for _ in range(20):
            with costs.run(job, operation=operation, pipeline_version=7):
                costs.record_external(job, 'provider', provider='fixture', amount_usd=usd)
    metrics = report()['metrics']
    assert metrics['p50_usd'] is None and metrics['p95_usd'] is None
    assert metrics['cohorts']['article:7:generate']['p50_usd'] == 1.0
    assert metrics['cohorts']['article:7:review']['p50_usd'] == 0.01
    assert metrics['cohorts']['article:7:review']['sample_size'] == 20


def test_recovery_skips_non_object_json_and_preserves_raw_evidence(job):
    with db.connect() as c:
        costs.init(c)
        c.execute('INSERT INTO cost_runs VALUES (?,?,?)', ('incomplete-run', job['id'], '[]'))
    result = costs.recover_inflight()
    assert result['invalid_rows'] == 1
    with db.connect() as c:
        assert c.execute('SELECT data FROM cost_runs WHERE id=?', ('incomplete-run',)).fetchone()[0] == '[]'
    import pytest
    with pytest.raises(ValueError, match='telemetria inválida'):
        report()


def test_measured_provider_duration_cannot_be_undercharged_by_local_estimate(job):
    from app import spending
    ident = spending.reserve(job, 0.01, 'whisper-1', 'audio_transcription')
    receipt = spending.finish(ident, {'estimated_usd': 0.01,
                                    'provider_duration_seconds': 600,
                                    'input_tokens': None, 'output_tokens': None})
    assert receipt['calculated_usd'] == 0.06
    assert receipt['charged_usd'] == 0.069
    assert spending.summary(job, persist=False)['spent_usd'] == 0.069
