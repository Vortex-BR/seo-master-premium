"""Measure the existing read-only reporter on isolated synthetic ledgers.

Recorded USD values are fixture inputs, not measured provider prices or invoices.
No provider, production database or application configuration is accessed.
"""
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app import cost_observability


def write_row(conn, table, values):
    conn.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in values)})', values)


def fixture(path, count):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    cost_observability.init(conn)
    conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, data TEXT)')
    conn.execute('CREATE TABLE spend_reservations(id TEXT PRIMARY KEY, job_id TEXT, data TEXT)')
    write_row(conn, 'jobs', ('fixture-article', json.dumps({'id': 'fixture-article', 'usage': []})))
    for index in range(count):
        run_id = f'fixture-run-{index}'
        run = {'id': run_id, 'run_id': run_id, 'entity_id': 'fixture-article', 'scope': 'article',
               'operation': 'fixture', 'status': 'completed', 'pipeline_version': 'fixture-pipeline-v1',
               'started_at': '2026-10-10T00:00:00Z', 'duration_seconds': 1.0}
        write_row(conn, 'cost_runs', (run_id, 'fixture-article', json.dumps(run)))
        stages = [('writer', .003), ('fact_reviewer', .001), ('writer', .002)]
        for attempt, (stage, value) in enumerate(stages):
            ident = f'{run_id}-attempt-{attempt}'
            row = {'id': ident, 'attempt_id': ident, 'run_id': run_id, 'scope': 'article',
                   'stage': stage, 'origin': 'fixture_provider', 'provider': 'fixture',
                   'state': 'completed', 'calculated_usd': value, 'estimated_usd': None,
                   'charged_usd': value * 1.15, 'model': 'fixture-model',
                   'pricing_version': 'synthetic-fixture', 'dependency_fingerprint': stage,
                   'usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 10}}
            write_row(conn, 'spend_reservations', (ident, 'fixture-article', json.dumps(row)))
        cached = {'id': f'{run_id}-cache', 'run_id': run_id, 'entity_id': 'fixture-article',
                  'stage': 'extractor', 'origin': 'application_cache', 'state': 'reused',
                  'calculated_usd': 0.0, 'estimated_usd': None}
        write_row(conn, 'cost_events', (cached['id'], 'fixture-article', run_id, json.dumps(cached)))
        if index % 5 == 0:
            ident = f'{run_id}-uncertain'
            unknown = {'id': ident, 'run_id': run_id, 'stage': 'writer', 'state': 'uncertain',
                       'reserved_usd': .005, 'model': 'fixture-model', 'calculated_usd': None,
                       'estimated_usd': None, 'dependency_fingerprint': 'writer'}
            write_row(conn, 'spend_reservations', (ident, 'fixture-article', json.dumps(unknown)))
    conn.commit()
    return conn


def hashes(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [path, Path(str(path) + '-wal'), Path(str(path) + '-shm')] if p.is_file()}


def measure(path, count):
    conn = fixture(path, count)
    try:
        original = hashes(path)
        wall, cpu, peak = [], [], []
        report = None
        for _ in range(7):
            tracemalloc.start()
            began, cpu_began = time.perf_counter(), time.process_time()
            report = cost_observability.read_report(path, job_id='fixture-article')
            cpu.append(time.process_time() - cpu_began)
            wall.append(time.perf_counter() - began)
            peak.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
        unchanged = hashes(path) == original
        assert unchanged, 'Read-only report altered a source database/WAL/SHM file.'
        assert len(report['executions']) == count
        result = {'execution_count': count, 'repetitions': 7,
                  'wall_seconds_median': statistics.median(wall),
                  'process_cpu_seconds_median': statistics.median(cpu),
                  'python_peak_allocation_bytes_max': max(peak),
                  'source_db_bytes': path.stat().st_size,
                  'source_wal_bytes': Path(str(path) + '-wal').stat().st_size,
                  'source_page_count': conn.execute('PRAGMA page_count').fetchone()[0],
                  'report_json_bytes': len(json.dumps(report, ensure_ascii=False, allow_nan=False).encode('utf-8')),
                  'source_db_wal_shm_unchanged': unchanged,
                  'reported_metrics': report['metrics'],
                  'observed_application_cache_hits': sum(run['costs']['application_cache_hits']
                                                         for run in report['executions']),
                  'reported_uncertain_reservation_runs': sum(run['costs']['inconclusive_reserve_usd'] > 0
                                                            for run in report['executions']),
                  'fixture_retry_attempts_reported': sum(run['retry_attempts'] for run in report['executions'])}
        return result
    finally:
        conn.close()


def main():
    report = {'runtime': {'python': platform.python_version(), 'platform': platform.platform(),
                          'sqlite': sqlite3.sqlite_version},
              'method': 'Existing read_report; live WAL fixture; seven repeats with tracemalloc; isolated temp DB.',
              'pricing_basis': 'Synthetic USD fixture inputs; no provider cost observed.',
              'provider_calls': 0, 'paid_calls': 0, 'production_access': False,
              'notice': 'Local CPU/runtime/allocation only. Shared machine load affects timing. No generation latency, invoice, remote CPU or savings measured.',
              'reporter_sha256': hashlib.sha256((ROOT / 'app/cost_observability.py').read_bytes()).hexdigest(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix='seo-p1-observability-probe-') as folder:
        report['samples'] = [measure(Path(folder) / f'fixture-{count}.sqlite3', count) for count in (20, 100)]
    output = Path(__file__).with_name('benchmark-observability.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'provider_calls': 0,
                      'source_files_unchanged': all(row['source_db_wal_shm_unchanged'] for row in report['samples'])}))


if __name__ == '__main__':
    main()
