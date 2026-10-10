import json
import uuid

from .. import db, generation
from .contracts import VoiceProfile


def init():
    with db.connect() as c:
        c.executescript('''
          CREATE TABLE IF NOT EXISTS editorial_profiles (
            version TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, cycle_id TEXT NOT NULL, role TEXT NOT NULL,
            input_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS agent_runs_job ON agent_runs(job_id, created_at);
          CREATE INDEX IF NOT EXISTS agent_runs_cache ON agent_runs(job_id, cycle_id, role, input_hash, status);
          CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, cycle_id TEXT NOT NULL,
            created_at TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS change_sets (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, cycle_id TEXT NOT NULL,
            created_at TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS change_sets_job ON change_sets(job_id, created_at);
          CREATE TABLE IF NOT EXISTS editorial_artifacts (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
            version TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS editorial_artifacts_job ON editorial_artifacts(job_id, kind, scope, created_at);
          CREATE TABLE IF NOT EXISTS editorial_issues (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS editorial_issues_job ON editorial_issues(job_id, status);
        ''')


def profile(*, persist=True):
    value = bounded_profile(db.get_setting('editorial_profile', VoiceProfile().model_dump()))
    result = {'profile': VoiceProfile.model_validate(value).model_dump(),
              'brand_name': db.get_setting('brand_name', ''), 'brand_voice': db.get_setting('brand_voice', '')}
    result['version'] = generation.article_hash(result)
    if persist:
        with db.connect() as c:
            c.execute('INSERT OR IGNORE INTO editorial_profiles VALUES (?,?,?)',
                      (result['version'], db.now(), json.dumps(result, ensure_ascii=False)))
    return result


def bounded_profile(value):
    """Migrate operational limits without rewriting saved editorial voice/history."""
    value = dict(value)
    legacy = 'max_spend_usd' not in value
    calls = value.get('max_calls', 24)
    # Eight was the previous release's forced ceiling, rather than a user choice.
    if legacy and calls == 8:
        calls = 24
    value['max_calls'] = max(4, min(24, calls))
    # The operator chooses the allowance; there is no fixed global article cap.
    # Validate persisted values as strictly as new API settings.
    value['max_spend_usd'] = VoiceProfile(max_spend_usd=value.get('max_spend_usd', 1.00)).max_spend_usd
    value['max_rounds'] = 0
    value['research_tool_calls'] = max(1, min(2, value.get('research_tool_calls', 2)))
    return value


def new_id():
    return uuid.uuid4().hex


def voice(snapshot):
    """Operational budgets and automation switches are not voice instructions."""
    controls = {'auto_apply', 'auto_write', 'max_rounds', 'max_calls', 'max_spend_usd', 'research_tool_calls', 'context_chars', 'block_chars'}
    result = {key: value for key, value in snapshot.items() if key != 'profile'}
    result['profile'] = {key: value for key, value in snapshot['profile'].items() if key not in controls}
    result['version'] = generation.article_hash({k: v for k, v in result.items() if k != 'version'})
    return result


def save_run(job, role, fingerprint, data, run_id=None, status='running'):
    run_id = run_id or new_id()
    with db.connect() as c:
        c.execute('''INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, data=excluded.data''',
                  (run_id, job['id'], job['editorial']['cycle_id'], role, fingerprint, status, db.now(),
                   json.dumps(data, ensure_ascii=False)))
    return run_id


def cached(job, role, fingerprint):
    with db.connect() as c:
        row = c.execute('''SELECT data FROM agent_runs WHERE job_id=? AND cycle_id=? AND role=?
                           AND input_hash=? AND status='completed' ORDER BY created_at DESC LIMIT 1''',
                        (job['id'], job['editorial']['cycle_id'], role, fingerprint)).fetchone()
    return json.loads(row['data']) if row else None


def get_run(run_id):
    with db.connect() as c:
        row = c.execute('SELECT data FROM agent_runs WHERE id=? AND status=\'completed\'', (run_id,)).fetchone()
    return json.loads(row['data']) if row else None


def message(job, sender, recipient, kind, data):
    payload = {'sender': sender, 'recipient': recipient, 'kind': kind,
               'article_hash': generation.article_hash(job['article']) if job.get('article') else None, **data}
    with db.connect() as c:
        c.execute('INSERT INTO agent_messages (job_id,cycle_id,created_at,data) VALUES (?,?,?,?)',
                  (job['id'], job['editorial']['cycle_id'], db.now(), json.dumps(payload, ensure_ascii=False)))


def save_changes(job, changes):
    with db.connect() as c:
        c.execute('''INSERT INTO change_sets VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, data=excluded.data''',
                  (changes['id'], job['id'], job['editorial']['cycle_id'], db.now(), changes['status'],
                   json.dumps(changes, ensure_ascii=False)))


def get_changes(job_id, change_id):
    with db.connect() as c:
        row = c.execute('SELECT data FROM change_sets WHERE id=? AND job_id=?', (change_id, job_id)).fetchone()
    return json.loads(row['data']) if row else None


def report(job):
    result = {'cycle': job.get('editorial'), 'runs': [], 'messages': [], 'changes': [], 'totals': {}}
    with db.connect() as c:
        for table, key in [('agent_runs', 'runs'), ('agent_messages', 'messages'), ('change_sets', 'changes')]:
            result['totals'][key] = c.execute(f'SELECT COUNT(*) FROM {table} WHERE job_id=?', (job['id'],)).fetchone()[0]
            rows = c.execute(f'SELECT * FROM {table} WHERE job_id=? ORDER BY created_at DESC LIMIT 1200', (job['id'],))
            for row in rows:
                item = dict(row)
                item['data'] = json.loads(item['data'])
                # Large article snapshots are private storage for undo, not needed in the report.
                item['data'].pop('before_article', None)
                item['data'].pop('after_article', None)
                result[key].append(item)
    return result


def artifact(job, kind, scope, data, dependencies):
    """Immutable, content-addressed snapshots; old versions remain reviewable."""
    version = generation.article_hash({'kind': kind, 'scope': scope, 'data': data, 'dependencies': dependencies})
    item = {'version': version, 'kind': kind, 'scope': scope, 'created_at': db.now(),
            'dependencies': dependencies, 'data': data}
    ident = generation.article_hash({'job': job['id'], 'kind': kind, 'scope': scope, 'version': version})
    with db.connect() as c:
        c.execute('INSERT OR IGNORE INTO editorial_artifacts VALUES (?,?,?,?,?,?,?,?)',
                  (ident, job['id'], kind, scope, version, generation.article_hash(dependencies),
                   item['created_at'], json.dumps(item, ensure_ascii=False)))
    return item


def artifacts(job_id, kind=None):
    with db.connect() as c:
        rows = c.execute('SELECT data FROM editorial_artifacts WHERE job_id=?' +
                         (' AND kind=?' if kind else '') + ' ORDER BY created_at DESC',
                         (job_id, kind) if kind else (job_id,))
        return [json.loads(row['data']) for row in rows]


def issue(job, origin, key, reason, *, recipient='apuration', essential=False, source_ids=None):
    ident = generation.article_hash({'job': job['id'], 'input': inputs_version(job), 'origin': origin, 'key': key})[:32]
    item = {'id': ident, 'origin': origin, 'key': key, 'reason': reason, 'recipient': recipient,
            'essential': essential, 'source_ids': source_ids or [], 'input_version': inputs_version(job),
            'status': 'open', 'resolution': None, 'created_at': db.now()}
    with db.connect() as c:
        c.execute('INSERT OR IGNORE INTO editorial_issues VALUES (?,?,?,?,?)',
                  (ident, job['id'], 'open', item['created_at'], json.dumps(item, ensure_ascii=False)))
    return ident


def inputs_version(job):
    return generation.article_hash({'brief': job['brief'], 'sources': job.get('sources', [])})


def issues(job, current=True):
    with db.connect() as c:
        result = [json.loads(row['data']) for row in c.execute(
            'SELECT data FROM editorial_issues WHERE job_id=? ORDER BY created_at', (job['id'],))]
    return [item for item in result if not current or item['input_version'] == inputs_version(job)]


def resolve_issue(job, ident, reason, evidence_ids, actor='Administrador'):
    """Resolution is explicit and retains the original problem and sources."""
    with db.connect() as c:
        row = c.execute('SELECT data FROM editorial_issues WHERE id=? AND job_id=?', (ident, job['id'])).fetchone()
        if not row:
            raise ValueError('Pendência não encontrada.')
        item = json.loads(row['data'])
        if item['input_version'] != inputs_version(job):
            raise ValueError('Esta pendência pertence a outra versão das fontes.')
        if set(evidence_ids) - set(generation.evidence_map(job)):
            raise ValueError('A resolução citou uma fonte ausente.')
        item.update(status='resolved', resolution={'reason': reason, 'source_ids': evidence_ids, 'at': db.now(), 'actor': actor})
        c.execute('UPDATE editorial_issues SET status=?,data=? WHERE id=?',
                  ('resolved', json.dumps(item, ensure_ascii=False), ident))
    return item


def archive_review(job, reason):
    """Preserve a review before invalidation, including reviews from old jobs."""
    review = job.get('review')
    if not review:
        return None
    version = review.get('article_hash') or generation.article_hash(job.get('article'))
    snapshot = artifact(job, 'review_history', version, review, {'article': version})
    history = job.setdefault('review_history', [])
    if not any(item['version'] == snapshot['version'] for item in history):
        history.append({'version': snapshot['version'], 'article_hash': version,
                        'archived_at': db.now(), 'reason': reason})
    # Immutable artifacts retain the complete audit; job JSON only needs an index.
    job['review_history'] = history[-80:]
    return snapshot


def invalidate(job, reason, upstream=False):
    archive_review(job, reason)
    if job.get('editorial'):
        job['editorial']['stale'] = True
        job['editorial']['stale_reason'] = reason
    job['review'] = None
    if job.get('draft_delivery'):
        job['draft_delivery']['review_pending'] = True
    if upstream:
        if job.get('apuration'):
            job['apuration']['valid'] = False
        if job.get('plan'):
            job['plan']['valid'] = False
