"""One-video baselines must identify current costs without borrowing old retries."""
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import premium_baseline as baseline


def sample(tmp_path):
    job = {'id': 'article-1', 'article': {'title': 'Fixture', 'slug': 'fixture', 'markdown': 'Body'},
           'sources': [{'video_id': 'cVnRvZ8uMCo', 'segments': [
               {'id': 'v1s1', 'text': 'Fixture statement', 'start': 0, 'end': 1}]}],
           'usage': [{'run_id': 'old', 'input_tokens': 1000, 'output_tokens': 200,
                      'estimated_usd': 0.4, 'calculated_usd': 0.3},
                     {'run_id': 'new', 'input_tokens': 10, 'output_tokens': 5,
                      'cached_input_tokens': 2, 'estimated_usd': 0.02, 'calculated_usd': 0.01}]}
    source = tmp_path / 'job.json'
    source.write_text(json.dumps(job), encoding='utf-8')
    case = {'id': 'principal', 'url': 'https://www.youtube.com/watch?v=cVnRvZ8uMCo',
            'permission': {'status': 'authorized', 'basis': 'fixture ownership', 'reference': 'fixture'},
            'job_json': str(source), 'exclusive_source': True,
            'essential_excerpts': [{'source_id': 'v1s1', 'text': 'Fixture statement'}],
            'run': {'run_id': 'new', 'app_sha256': 'current', 'model': 'fixture-model',
                    'profile': {'context_chars': 90000}, 'wall_seconds': 1.0}}
    return case, job, source


def capture(case):
    inspection = baseline.inspect_manifest({'schema_version': 1, 'cases': [case]})['cases'][0]
    return baseline.capture_case(case, inspection, None, 'current')


def test_new_run_cost_excludes_old_generation_and_retry_history(tmp_path):
    case, _, _ = sample(tmp_path)
    snapshot, issues = capture(case)
    metrics = snapshot['baseline_metrics']
    assert metrics['run_id'] == 'new'
    assert metrics['usage_scope'] == 'execution_id'
    assert metrics['usage_rows'] == 1
    assert metrics['calculated_usage_usd'] == 0.01
    assert metrics['estimated_usage_usd'] == 0.02
    assert metrics['known_input_tokens'] == 10
    assert metrics['provider_cached_input_tokens'] == 2
    assert metrics['provider_invoice_usd'] is None
    assert not issues


def test_unavailable_requested_execution_never_falls_back_to_old_article_spend(tmp_path):
    case, _, _ = sample(tmp_path)
    case['run']['run_id'] = 'missing'
    snapshot, issues = capture(case)
    metrics = snapshot['baseline_metrics']
    assert metrics['usage_rows'] == 0
    assert metrics['estimated_usage_usd'] is None
    assert metrics['calculated_usage_usd'] is None
    assert metrics['known_input_tokens'] is None
    assert any('identidade' in issue for issue in issues)


def test_partial_unknown_tokens_and_price_do_not_become_free_metrics(tmp_path):
    case, job, source = sample(tmp_path)
    job['usage'][1].update(input_tokens=None, calculated_usd=None, estimated_usd=None,
                           cached_input_tokens=None)
    source.write_text(json.dumps(job), encoding='utf-8')
    snapshot, _ = capture(case)
    metrics = snapshot['baseline_metrics']
    assert metrics['known_input_tokens'] is None
    assert metrics['known_output_tokens'] == 5
    assert metrics['rows_without_token_telemetry'] == 1
    assert metrics['calculated_usage_usd'] is None
    assert metrics['provider_cached_input_tokens'] is None
    assert metrics['application_cache_hits'] is None


def test_execution_alias_is_supported_but_conflicting_identifiers_cannot_certify_cost(tmp_path):
    case, _, _ = sample(tmp_path)
    case['run']['execution_id'] = case['run'].pop('run_id')
    snapshot, _ = capture(case)
    assert snapshot['baseline_metrics']['run_id'] == 'new'
    case['run']['run_id'] = 'other'
    snapshot, issues = capture(case)
    assert snapshot['baseline_metrics']['estimated_usage_usd'] is None
    assert any('conflitantes' in issue for issue in issues)


def test_baseline_reads_committed_live_wal_without_altering_source_files(tmp_path):
    path = tmp_path / 'wal.sqlite3'
    conn = sqlite3.connect(path)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, data TEXT)')
        conn.execute('INSERT INTO jobs VALUES (?,?)', ('fixture', json.dumps({'id': 'fixture'})))
        conn.commit()
        files = [path, Path(str(path) + '-wal'), Path(str(path) + '-shm')]
        before = {p: p.read_bytes() for p in files}
        assert baseline.read_job(path, 'fixture') == {'id': 'fixture'}
        assert {p: p.read_bytes() for p in files} == before
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='cost_runs'").fetchone() is None
    finally:
        conn.close()


def test_pending_baseline_records_checkout_candidate_without_claiming_actual_generation(tmp_path):
    root = tmp_path / 'repo'
    (root / 'app/editorial').mkdir(parents=True)
    (root / 'app/main.py').write_text("app=FastAPI(version='1.5.25')", encoding='utf-8')
    (root / 'app/generation.py').write_text('EDITORIAL_VERSION = 7', encoding='utf-8')
    (root / 'app/editorial/workflow.py').write_text('VERSION = 2', encoding='utf-8')
    manifest = {'schema_version': 1, 'cases': [{
        'id': 'principal', 'url': 'https://www.youtube.com/watch?v=cVnRvZ8uMCo',
        'permission': {'status': 'authorized', 'basis': 'fixture ownership', 'reference': 'fixture'},
        'run': {'run_id': None, 'model': None}, 'essential_excerpts': []}]}
    _, report = baseline.execute(manifest, None, tmp_path / 'out', root)
    candidate = report['candidate_configuration']
    assert candidate['app_version'] == '1.5.25'
    assert candidate['editorial_version'] == 7
    assert candidate['evidence_flow_version'] == 2
    assert candidate['actual_generation_verified'] is False
    assert report['paid_calls'] == 0 and report['status'] == 'incomplete'
    assert report['database_mode'] == 'not_requested'
    assert manifest['cases'][0]['run']['model'] is None


def test_baseline_links_uncertain_ledger_reserve_and_measured_duration_without_calling_provider(tmp_path):
    case, job, _ = sample(tmp_path)
    database = tmp_path / 'costs.sqlite3'
    run = {'id': 'new', 'run_id': 'new', 'entity_id': job['id'], 'scope': 'article',
           'operation': 'generate', 'status': 'failed', 'duration_seconds': 2.5,
           'pipeline_version': 'fixture-1', 'started_at': '2026-10-10T00:00:00Z'}
    reserve = {'id': 'attempt-1', 'run_id': 'new', 'state': 'uncertain',
               'reserved_usd': 0.2, 'stage': 'writer', 'model': 'fixture',
               'calculated_usd': None, 'estimated_usd': None}
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, data TEXT)')
        conn.execute('CREATE TABLE cost_runs(id TEXT, entity_id TEXT, data TEXT)')
        conn.execute('CREATE TABLE spend_reservations(id TEXT, job_id TEXT, data TEXT)')
        conn.execute('INSERT INTO jobs VALUES (?,?)', (job['id'], json.dumps(job)))
        conn.execute('INSERT INTO cost_runs VALUES (?,?,?)', ('new', job['id'], json.dumps(run)))
        conn.execute('INSERT INTO spend_reservations VALUES (?,?,?)',
                     ('attempt-1', job['id'], json.dumps(reserve)))
    case.pop('job_json')
    case['job_id'] = job['id']
    inspection = baseline.inspect_manifest({'schema_version': 1, 'cases': [case]})['cases'][0]
    before = database.read_bytes()
    snapshot, issues = baseline.capture_case(case, inspection, database, 'current')
    assert database.read_bytes() == before
    metrics = snapshot['baseline_metrics']
    assert metrics['wall_seconds'] == 2.5
    assert metrics['reserved_usd'] == 0.2
    assert metrics['inconclusive_reserve_usd'] == 0.2
    assert metrics['current_execution_calculated_usd'] is None
    assert metrics['calculated_usage_usd'] == 0.01  # The old job save is not the ledger's truth.
    assert metrics['provider_invoice_usd'] is None
    assert any('reserva incerta' in issue for issue in issues)


@pytest.mark.parametrize('ident', [True, 12, [], '', ' '])
def test_invalid_execution_identifiers_have_a_diagnostic_before_capture(tmp_path, ident):
    case, _, _ = sample(tmp_path)
    case['run']['run_id'] = ident
    with pytest.raises(ValueError, match='Identidade da execução'):
        baseline.inspect_manifest({'schema_version': 1, 'cases': [case]})
