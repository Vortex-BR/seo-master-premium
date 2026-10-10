"""Repeatable offline probe: runtime SDK schemas, baseline comparison and CPU."""
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import openai
import pydantic
from app import db, generation
from app.strategy import agents
from app.strategy.contracts import StrategyPlan


def objects(node, path='$'):
    if isinstance(node, dict):
        if node.get('type') == 'object':
            yield path, node
        for key, value in node.items():
            yield from objects(value, f'{path}.{key}')
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from objects(value, f'{path}[{index}]')


baseline_source = subprocess.run(
    ['git', 'show', 'aedd740:app/strategy/contracts.py'], cwd=ROOT,
    capture_output=True, check=True, encoding='utf-8').stdout
baseline = {'__name__': 'offline_baseline'}
exec(compile(baseline_source, '<baseline-contracts>', 'exec'), baseline)
report = {
    'runtime': {'python': platform.python_version(), 'openai': openai.__version__,
                'pydantic': pydantic.__version__},
    'baseline': 'aedd740', 'api_calls': 0, 'method': 'actual runtime SDK conversion; synthetic context',
    'roles': {},
    'official_reference': 'https://developers.openai.com/api/docs/guides/structured-outputs',
}
with tempfile.TemporaryDirectory(prefix='seo-p002-contract-probe-') as directory:
    os.environ['DATA_DIR'] = directory
    db.init()
    db.set_setting('model', 'offline-model')
    cycle = {'id': 'synthetic-cycle', 'project_id': 'synthetic-project', 'focus': 'Horta'}
    context = {'project': {'name': 'Horta', 'project_id': 'synthetic-project'}, 'focus': 'Horta'}
    for role in [*agents.ROLES, 'coordinator']:
        schema = agents.ROLES[role]['schema'] if role != 'coordinator' else StrategyPlan
        old_schema = baseline[schema.__name__]
        old_format = generation.type_to_text_format_param(old_schema)
        current_format = generation.type_to_text_format_param(schema)
        old_objects = list(objects(old_format['schema']))
        current_objects = list(objects(current_format['schema']))
        incompatible = [path for path, obj in current_objects if
                        obj.get('additionalProperties') is not False or
                        set(obj.get('required', [])) != set(obj.get('properties', {}))]
        times = []
        for _ in range(20):
            started = time.perf_counter()
            identity = agents.execution_identity(cycle, role, context)
            times.append((time.perf_counter() - started) * 1000)
        report['roles'][role] = {
            'old_open_objects': [path for path, obj in old_objects
                                 if obj.get('additionalProperties') is not False],
            'current_objects': len(current_objects), 'current_incompatible_objects': incompatible,
            'schema_json_utf8_bytes_baseline': len(json.dumps(old_format, ensure_ascii=False).encode()),
            'schema_json_utf8_bytes_current': len(json.dumps(current_format, ensure_ascii=False).encode()),
            'identity_json_utf8_bytes': len(json.dumps(identity, ensure_ascii=False).encode()),
            'identity_cpu_ms_median': round(statistics.median(times), 4),
            'identity_cpu_ms_max': round(max(times), 4),
        }
report['schema_size_note'] = (
    'Closed models increase schema bytes in strategic requests; this is not a token count, '
    'provider charge or measurement of production latency. No extra agent calls are added.')
target = Path(__file__).with_name('strict-cache-probe.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
