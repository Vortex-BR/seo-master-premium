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
        ''')


def profile():
    value = db.get_setting('editorial_profile', VoiceProfile().model_dump())
    result = {'profile': VoiceProfile.model_validate(value).model_dump(),
              'brand_name': db.get_setting('brand_name', ''), 'brand_voice': db.get_setting('brand_voice', '')}
    result['version'] = generation.article_hash(result)
    with db.connect() as c:
        c.execute('INSERT OR IGNORE INTO editorial_profiles VALUES (?,?,?)',
                  (result['version'], db.now(), json.dumps(result, ensure_ascii=False)))
    return result


def new_id():
    return uuid.uuid4().hex


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
    result = {'cycle': job.get('editorial'), 'runs': [], 'messages': [], 'changes': []}
    with db.connect() as c:
        for table, key in [('agent_runs', 'runs'), ('agent_messages', 'messages'), ('change_sets', 'changes')]:
            rows = c.execute(f'SELECT * FROM {table} WHERE job_id=? ORDER BY created_at DESC LIMIT 150', (job['id'],))
            for row in rows:
                item = dict(row)
                item['data'] = json.loads(item['data'])
                # Large article snapshots are private storage for undo, not needed in the report.
                item['data'].pop('before_article', None)
                item['data'].pop('after_article', None)
                result[key].append(item)
    return result


def invalidate(job, reason):
    if job.get('editorial'):
        job['editorial']['stale'] = True
        job['editorial']['stale_reason'] = reason
    job['review'] = None
