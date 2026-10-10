"""Measure local shadow overhead; output contains counts/hashes, never speech."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app import db, generation
from app.editorial import human_knowledge as hkl, store


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def synthetic(count):
    segments = [{'id': f'v1s{n}', 'start': n * 10, 'end': n * 10 + 8,
                 'text': f'No meu caso, observei o exemplo {n}, somente se a condição estava presente.'}
                for n in range(count)]
    source = {'id': 'v1', 'video_id': 'abcdefghijk', 'title': 'Synthetic benchmark',
              'author': 'Synthetic channel metadata', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
              'segments': segments}
    job = {'id': f'synthetic-{count}', 'brief': {}, 'sources': [source]}
    job['apuration'] = {'valid': True, 'version': f'synthetic-{count}',
                       'dependencies': {'inputs': store.inputs_version(job)},
                       'items': [{'id': f'item-{n}', 'video_id': 'v1', 'topic': 'Exemplo',
                                  'statement': part['text'], 'kind': 'experiência',
                                  'information_type': 'exemplo', 'method': '', 'conditions': [],
                                  'quantities': [], 'restrictions': [], 'limitations': [],
                                  'evidence': [{'source_id': part['id'], 'excerpt': part['text']}]}
                                 for n, part in enumerate(segments)], 'videos': [], 'comparisons': []}
    return job


def measurement(function):
    tracemalloc.start()
    wall = time.perf_counter()
    cpu = time.process_time()
    value = function()
    duration = time.perf_counter() - wall
    cpu_seconds = time.process_time() - cpu
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, {'wall_seconds': duration, 'cpu_seconds': cpu_seconds, 'python_peak_bytes': peak}


def footprint(directory):
    return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())


def summarize(rows):
    return {name + '_median': statistics.median(row[name] for row in rows) for name in rows[0]}


def benchmark(name, job, repetitions):
    before_job = encoded(job)
    project_results = []
    for _ in range(repetitions):
        dossier, result = measurement(lambda: hkl.project(job))
        project_results.append(result)
    assert encoded(job) == before_job, 'Pure projection mutated the source job.'
    compact = hkl.compact(dossier)
    cold_results, repeated_results, growths, repeated_growths = [], [], [], []
    previous_dir = os.environ.get('DATA_DIR')
    previous_mode = os.environ.get('HUMAN_KNOWLEDGE_MODE')
    try:
        os.environ['HUMAN_KNOWLEDGE_MODE'] = 'shadow'
        for _ in range(repetitions):
            with tempfile.TemporaryDirectory(prefix='seo-hkl-benchmark-') as temporary:
                directory = Path(temporary)
                os.environ['DATA_DIR'] = str(directory)
                db.init()
                store.init()
                before = footprint(directory)
                first, result = measurement(lambda: hkl.persist_shadow(job))
                cold_results.append(result)
                after = footprint(directory)
                growths.append(after - before)
                second, result = measurement(lambda: hkl.persist_shadow(job))
                repeated_results.append(result)
                repeated_growths.append(footprint(directory) - after)
                assert first['version'] == second['version']
                assert len(store.artifacts(job['id'], 'human_knowledge')) == 1
                assert len(store.artifacts(job['id'], 'human_knowledge_sources')) == 1
    finally:
        for key, old in (('DATA_DIR', previous_dir), ('HUMAN_KNOWLEDGE_MODE', previous_mode)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    eligible = [unit for unit in dossier['units'] if unit['anchors'] and unit['origin_status'] == 'anchored']
    sampled = ([eligible[index] for index in sorted({0, len(eligible) // 2, len(eligible) - 1})]
               if eligible else [])
    resolved = [hkl.resolve_unit(job, dossier, unit['id'])['status'] for unit in sampled]
    assert encoded(job) == before_job
    return {'case': name, 'repetitions': repetitions,
            'source_count': len(job['sources']),
            'source_segments': sum(len(source.get('segments', [])) for source in job['sources']),
            'source_json_bytes': len(encoded(job['sources'])),
            'job_sha256': hashlib.sha256(before_job).hexdigest(),
            'dossier_json_bytes': len(encoded(dossier)), 'compact_json_bytes': len(encoded(compact)),
            'source_snapshot_version': dossier['source_snapshot_version'],
            'coverage': dossier['coverage'], 'apuration_status': dossier['apuration_status'],
            'sampled_resolutions': len(resolved),
            'sampled_unit_positions': 'first, middle, last among anchored units',
            'sampled_resolutions_succeeded': all(row == 'resolved' for row in resolved),
            'original_job_preserved': True, 'local_projection': summarize(project_results),
            'first_persistence': summarize(cold_results), 'repeat_persistence': summarize(repeated_results),
            'storage_growth_bytes_median': statistics.median(growths),
            'repeat_storage_growth_bytes_median': statistics.median(repeated_growths),
            'artifact_rows': {'human_knowledge': 1, 'human_knowledge_sources': 1},
            'observed_persistence_note': 'DB file allocation delta on this workstation, after isolated DB initialization.',
            'provider_operations_in_benchmark': 0, 'generation_performed': False,
            'infrastructure_cost_usd': None, 'human_semantic_evaluation': 'not_performed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-job', type=Path)
    parser.add_argument('--repetitions', type=int, default=7)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_suffix('.json'))
    args = parser.parse_args()
    if args.repetitions < 7:
        parser.error('Use pelo menos sete repetições.')
    if args.pilot_job and args.output.resolve() == args.pilot_job.resolve():
        parser.error('Não sobrescrever a fonte privada.')
    cases = []
    pilot_hash = None
    if args.pilot_job:
        content = args.pilot_job.read_bytes()
        pilot_hash = hashlib.sha256(content).hexdigest()
        cases.append(('authorized_primary_source_only', json.loads(content.decode('utf-8-sig'))))
    cases.extend((f'synthetic-{count}-segments', synthetic(count)) for count in (100, 1000))
    result = {'observed_at': datetime.now(timezone.utc).isoformat(),
              'runtime': {'python': platform.python_version(), 'platform': platform.platform()},
              'schema_version': hkl.SCHEMA_VERSION, 'projection_version': hkl.PROJECTION_VERSION,
              'source_file_sha256': {str(path.relative_to(ROOT)).replace('\\', '/'):
                                    hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                                    for path in (ROOT / 'app/editorial/human_knowledge.py',
                                                 ROOT / 'app/editorial/human_knowledge_contracts.py',
                                                 ROOT / 'app/editorial/human_knowledge_runtime.py')},
              'pilot_input_sha256': pilot_hash,
              'notice': 'Local deterministic projection/storage overhead only. Not article latency, '
                        'provider invoice, production CPU, fidelity score or demonstrated savings.',
              'cases': [benchmark(name, deepcopy(job), args.repetitions) for name, job in cases]}
    if args.pilot_job:
        assert hashlib.sha256(args.pilot_job.read_bytes()).hexdigest() == pilot_hash
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'cases': len(cases), 'provider_calls': 0}))


if __name__ == '__main__':
    main()
