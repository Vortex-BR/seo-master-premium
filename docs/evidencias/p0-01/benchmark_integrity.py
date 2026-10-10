"""Local deterministic P0_01 comparison. No DB, network, provider or paid call."""
import ast
from copy import deepcopy
import html
import json
import math
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time
import tracemalloc

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'app/youtube.py').is_file())
sys.path.insert(0, str(ROOT))
from app import generation, youtube
from app.transcripts import SourceError
BASELINE_COMMIT = '2ae42d821087631b2c6e2218c4f6b970d69bb737'


def legacy_function():
    raw = subprocess.check_output(['git', 'show', BASELINE_COMMIT + ':app/youtube.py'], cwd=ROOT).decode('utf-8')
    node = next(node for node in ast.parse(raw).body
                if isinstance(node, ast.FunctionDef) and node.name == 'segment_rows')
    environment = {'html': html, 're': re, 'math': math, 'SourceError': SourceError}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'HEAD:app/youtube.py:segment_rows', 'exec'), environment)
    return environment['segment_rows']


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def benchmark(function, rows):
    function(rows, 'v1')  # Warmup excluded from timings.
    wall, cpu = [], []
    for _ in range(7):
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        segments = function(rows, 'v1')
        wall.append(time.perf_counter() - started_wall)
        cpu.append(time.process_time() - started_cpu)
    started_cpu = time.process_time()
    for _ in range(100):
        function(rows, 'v1')
    batch_cpu = time.process_time() - started_cpu
    tracemalloc.start()
    function(rows, 'v1')
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return segments, {'wall_ms_median': round(statistics.median(wall) * 1000, 3),
                      'cpu_ms_median': round(statistics.median(cpu) * 1000, 3),
                      'cpu_100_calls_ms': round(batch_cpu * 1000, 3),
                      'cpu_per_call_ms_over_100_calls': round(batch_cpu * 1000 / 100, 3),
                      'python_tracemalloc_peak_bytes': peak,
                      'timed_repetitions': len(wall)}


def main():
    text = ('A explicação técnica descreve as condições do procedimento e os limites '
            'que precisam ser conferidos no conteúdo original.')
    rows = [{'id': f'provider-{number}', 'text': text + f' Trecho {number:04d}.',
             'start': number * 2, 'duration': 2} for number in range(700)]
    old_segments, old_stats = benchmark(legacy_function(), rows)
    new_segments, new_stats = benchmark(youtube.segment_rows, rows)
    base = {'id': 'v1', 'video_id': 'abcdefghijk', 'url': 'https://youtu.be/abcdefghijk',
            'provider': 'Legendas do YouTube', 'title': 'Fixture sintética', 'author': '',
            'status': 'ok', 'language': 'pt'}
    old_source = {**base, 'segments': old_segments}
    new_source = {**base, 'segments': new_segments,
                  'normalization_version': 'source-integrity-v1',
                  'normalization_options': {'merge_adjacent': True}}
    # This is the real extractor's videos projection, before brief/prompt/schema.
    def videos(source):
        return {'videos': [{**{key: source[key] for key in ('id', 'title', 'author', 'url')},
                            'segments': source['segments']}]}
    new_original = deepcopy(new_source)
    projected = generation.compact_source_provenance(videos(new_source))
    assert new_source == new_original
    assert 'original_cues' not in json.dumps(projected)
    assert ' '.join(segment['text'] for segment in new_segments) == ' '.join(
        segment['text'] for segment in old_segments)
    old_stats.update(segments=len(old_segments), source_json_utf8_compact_bytes=json_bytes(old_source),
                     projected_videos_json_utf8_compact_bytes=json_bytes(videos(old_source)))
    new_stats.update(segments=len(new_segments), source_json_utf8_compact_bytes=json_bytes(new_source),
                     projected_videos_json_utf8_compact_bytes=json_bytes(projected),
                     unprojected_videos_json_utf8_compact_bytes=json_bytes(videos(new_source)))
    result = {'scope': 'Synthetic contiguous 700 cues; normalization and extractor videos JSON only. '
                       'No model context, tokens, invoice, remote CPU or production latency measured.',
              'base_commit': BASELINE_COMMIT,
              'checkout_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
              'python': platform.python_version(), 'platform': platform.platform(),
              'cues': len(rows), 'input_text_characters': sum(len(row['text']) for row in rows),
              'legacy': old_stats, 'integrity_v1': new_stats,
              'paid_provider_calls': 0, 'network_calls': 0,
              'limitations': ['Single Windows workstation; CPU process timer granularity may hide short runs.',
                              'Tracemalloc measures Python allocation, not full process RSS.',
                              'Compact JSON bytes are not database volume size or billable token counts.',
                              'Continuous cues only; gapped cues intentionally produce more segments.']}
    target = ROOT / '.local' / 'p0-01-integridade' / 'benchmark-reproduzido.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
