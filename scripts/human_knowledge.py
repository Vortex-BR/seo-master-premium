"""Inspect a private HKL projection or resolve its original speech, offline."""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.editorial import human_knowledge


def _run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-json', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path,
                        help='Private JSON output; no application writes or provider calls.')
    parser.add_argument('--unit-id', help='Resolve one unit instead of creating a projection.')
    parser.add_argument('--dossier-json', type=Path, help='Optional saved dossier/artifact for resolution.')
    parser.add_argument('--source-snapshot', type=Path, help='Original HKL source artifact for an old version.')
    args = parser.parse_args()
    inputs = [path.resolve() for path in (args.job_json, args.dossier_json, args.source_snapshot) if path]
    if args.output.resolve() in inputs:
        parser.error('A saída deve ser diferente dos arquivos originais.')
    if (args.dossier_json or args.source_snapshot) and not args.unit_id:
        parser.error('Dossiê/snapshot de resolução exige --unit-id.')
    job = json.loads(args.job_json.read_text(encoding='utf-8-sig'))
    dossier = (json.loads(args.dossier_json.read_text(encoding='utf-8-sig')) if args.dossier_json
               else human_knowledge.project(job))
    if args.unit_id:
        sources = (json.loads(args.source_snapshot.read_text(encoding='utf-8-sig'))
                   if args.source_snapshot else None)
        result = human_knowledge.resolve_unit(job, dossier, args.unit_id, source_snapshot=sources)
    else:
        result = dossier
    # JSON validation finishes before any output is replaced. It is not a DB export.
    content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'operation': 'resolve' if args.unit_id else 'project',
                      'provider_calls': 0, 'application_writes': 0}, ensure_ascii=False))
    return 0


def main():
    try:
        return _run()
    except Exception as exc:
        # ValidationError includes input values: expose only the error class.
        # BaseException (including argparse exit and cancellation) propagates.
        print(json.dumps({'error': 'Human Knowledge offline operation failed',
                          'error_type': type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
