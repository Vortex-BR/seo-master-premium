"""Offline storage/idempotency measurement; temporary SQLite, no dispatch/providers."""
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app import db, spending
from app.strategy import store


def snapshot():
    with db.connect() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted([list(row) for row in conn.execute('SELECT * FROM "' + name + '"')], key=repr)
                for name in names}


def counts_storage():
    with db.connect() as conn:
        result = {}
        for table in ('jobs', 'opportunities', 'strategy_opportunity_keys', 'strategy_production_intents'):
            cols = [r[1] for r in conn.execute('PRAGMA table_info(' + table + ')')]
            rows = list(conn.execute('SELECT * FROM ' + table))
            result[table] = {'rows': len(rows), 'json_data_utf8_bytes':
                            sum(len(str(row[cols.index('data')]).encode('utf-8')) for row in rows)
                            if 'data' in cols else 0}
        result['sqlite_page_count'] = conn.execute('PRAGMA page_count').fetchone()[0]
        result['sqlite_page_size_bytes'] = conn.execute('PRAGMA page_size').fetchone()[0]
        return result


def prepare(identifier):
    store.save_opportunity({'opportunity_id': identifier, 'action': 'create', 'status': 'approved',
                            'main_question': 'Como observar folhas?', 'queries': ['observar folhas'],
                            'selected_videos': [{'url': 'https://www.youtube.com/watch?v=abcdefghijk'}]},
                           {'id': 'measurement-cycle'}, 'measurement-project')


factory_calls = []


def factory(opportunity):
    factory_calls.append(opportunity['opportunity_id'])
    return {'id': store.new_id(), 'status': 'new', 'created_at': db.now(), 'usage': [],
            'brief': {'topic': opportunity['main_question'], 'target_words': None,
                      'urls': [v['url'] for v in opportunity['selected_videos']]},
            'sources': [], 'article': None, 'events': []}


def request(identifier):
    start = time.perf_counter_ns()
    result = store.create_production(identifier, factory, queue_limit=100)
    return result, (time.perf_counter_ns() - start) / 1_000_000


def timing(values):
    return {'samples': len(values), 'median_ms': round(statistics.median(values), 4),
            'minimum_ms': round(min(values), 4), 'maximum_ms': round(max(values), 4)}


report = {'runtime': {'python': platform.python_version(), 'platform': platform.platform()},
          'api_calls': 0, 'dispatch_calls': 0,
          'method': 'Actual SQLite persistence in temporary DATA_DIR; synthetic small opportunity/job JSON; perf_counter_ns wall runtime. No queue/pipeline/provider calls.'}
with tempfile.TemporaryDirectory(prefix='seo-p002-measurement-') as directory:
    os.environ['DATA_DIR'] = directory
    db.init()
    store.init()
    report['empty_database'] = counts_storage()
    ids = ['measurement-' + str(i) for i in range(8)]
    for identifier in ids:
        prepare(identifier)
    report['before_production'] = counts_storage()
    created_times, reused_times = [], []
    for identifier in ids:
        for repeat in range(4):
            result, elapsed = request(identifier)
            (created_times if result['created'] else reused_times).append(elapsed)
    report['sequential_32_requests'] = {'created': len(created_times), 'reused': len(reused_times),
                                     'factory_calls': len(factory_calls), 'creation': timing(created_times),
                                     'reuse': timing(reused_times), 'storage': counts_storage()}
    prepare('concurrent-measurement')
    old_factory_count = len(factory_calls)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, ['concurrent-measurement'] * 32))
    report['concurrent_32_requests_8_workers'] = {
        'created': sum(r['created'] for r, _ in results), 'reused': sum(r['reused'] for r, _ in results),
        'distinct_jobs': len({r['job_id'] for r, _ in results}),
        'distinct_intents': len({r['intent_id'] for r, _ in results}),
        'factory_calls': len(factory_calls) - old_factory_count,
        'request': timing([elapsed for _, elapsed in results]), 'storage': counts_storage()}
    job = db.get_job(results[0][0]['job_id'])
    job['usage'] = [{'model': 'gpt-4.1-mini', 'stage': 'writer', 'input_tokens': 1000,
                     'output_tokens': 100, 'web_search_calls': 0, 'response_id': 'synthetic-history'}]
    db.save_job(job)
    before = snapshot()
    readings = []
    for _ in range(32):
        start = time.perf_counter_ns()
        spending.summary(db.get_job(job['id']), persist=False)
        store.get_opportunity('concurrent-measurement')
        readings.append((time.perf_counter_ns() - start) / 1_000_000)
    after = snapshot()
    report['read_only_finance_opportunity_32_pairs'] = {
        'snapshot_unchanged': before == after, 'ledger_table_absent_before': 'spend_reservations' not in before,
        'ledger_table_absent_after': 'spend_reservations' not in after, 'runtime': timing(readings),
        'before_logical_snapshot_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        'after_logical_snapshot_sha256': hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()}
report['limitations'] = [
    'Synthetic small payload; actual articles/transcripts/media/history can be larger.',
    'JSON byte totals exclude indexes, SQLite row framing, WAL and filesystem allocation.',
    'Page allocation is a temporary database measurement, not a production storage forecast.',
    'Local latency includes connection/transaction costs, not paid generation or production latency.',
    'These bytes and timings are not tokens, invoice amounts or USD savings.']
target = Path(__file__).with_name('measurement-storage-runtime.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
