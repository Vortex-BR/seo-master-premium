import json
import re
from pathlib import Path

from .. import db, generation

CATALOG = Path(__file__).with_name('rules.json')


def package():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def init():
    bundle = package()
    with db.connect() as c:
        c.executescript('''
          CREATE TABLE IF NOT EXISTS seo_packages (version TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE VIRTUAL TABLE IF NOT EXISTS seo_rules_fts USING fts5(
            rule_id UNINDEXED, version UNINDEXED, title, content, tokenize='unicode61 remove_diacritics 2');
        ''')
        exists = c.execute('SELECT 1 FROM seo_packages WHERE version=?', (bundle['version'],)).fetchone()
        if not exists:
            c.execute('INSERT INTO seo_packages VALUES (?,?)', (bundle['version'], json.dumps(bundle, ensure_ascii=False)))
            for rule in bundle['rules']:
                c.execute('INSERT INTO seo_rules_fts VALUES (?,?,?,?)',
                          (rule['id'], bundle['version'], rule['title'], json.dumps(rule, ensure_ascii=False)))


def get_package(version=None):
    version = version or package()['version']
    with db.connect() as c:
        row = c.execute('SELECT data FROM seo_packages WHERE version=?', (version,)).fetchone()
    if not row:
        raise ValueError('A versão da documentação não está disponível. Inicie uma nova execução editorial.')
    return json.loads(row['data'])


def continuation_compatible(version):
    """Allow only additive local timestamp diagnostics in a frozen plan cycle.

    Existing instructions, dependencies and paid deliveries keep their original
    catalog. Other catalog changes still require a new cycle as before.
    """
    current = package()
    if version == current['version']:
        return True
    if not isinstance(version, str) or not version:
        return False
    try:
        frozen = get_package(version)
    except ValueError:
        return False
    metadata = lambda bundle: {key: value for key, value in bundle.items()
                               if key not in ('version', 'reviewed_at', 'rules')}
    if metadata(frozen) != metadata(current):
        return False
    previous = {rule['id']: rule for rule in frozen['rules']}
    latest = {rule['id']: rule for rule in current['rules']}
    return (set(latest) - set(previous) <= {'video_first.timestamp_alignment'}
            and all(latest.get(ident) == rule for ident, rule in previous.items()))


def retrieve(sector, query='', version=None):
    bundle = get_package(version)
    rules = bundle['rules']
    selected = [r for r in rules if sector in r['sectors']]
    tokens = re.findall(r'[^\W_]{3,}', query, re.UNICODE)[:12]
    if tokens:
        expression = ' OR '.join('"' + t.replace('"', '') + '"' for t in tokens)
        with db.connect() as c:
            matches = c.execute('''SELECT rule_id FROM seo_rules_fts WHERE seo_rules_fts MATCH ?
                                    AND version=? ORDER BY rank LIMIT 4''', (expression, bundle['version'])).fetchall()
        ids = {r['id'] for r in selected}
        selected += [r for m in matches for r in rules if r['id'] == m['rule_id'] and r['id'] not in ids]
    return {'version': bundle['version'], 'reviewed_at': bundle['reviewed_at'], 'rules': selected}


def status():
    bundle = get_package()
    return {**bundle, 'content_hash': generation.article_hash(bundle),
            'mode': 'Documentação interpretada e verificações próprias; motor Yoast não executado.',
            'update_policy': 'Versões revisadas e distribuídas com o app. Atualizações externas não alteram as instruções automaticamente.'}
