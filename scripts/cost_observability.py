"""Export an execution cost report without contacting providers or changing source DB/WAL."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cost_observability import read_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--run-id')
    parser.add_argument('--output', type=Path, help='JSON privado; omitido imprime na saída padrão.')
    args = parser.parse_args()
    report = read_report(args.database, job_id=args.job_id, run_id=args.run_id)
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        target = args.output.resolve()
        source = args.database.resolve()
        if target in (source, Path(str(source) + '-wal'), Path(str(source) + '-shm')):
            parser.error('A saída precisa ser diferente do banco e seus arquivos auxiliares.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + '\n', encoding='utf-8')
    else:
        print(content)


if __name__ == '__main__':
    main()
