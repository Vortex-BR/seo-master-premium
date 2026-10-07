import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def data_dir():
    path = Path(os.getenv('DATA_DIR', './data'))
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect():
    conn = sqlite3.connect(data_dir() / 'seo.sqlite3', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS image_reference_cache (
                key TEXT PRIMARY KEY, expires REAL NOT NULL, data TEXT NOT NULL
            );
        ''')


def get_setting(key, default=None):
    with connect() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, json.dumps(value, ensure_ascii=False)))


def save_job(job):
    job['updated_at'] = now()
    with connect() as c:
        c.execute('''INSERT INTO jobs VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                  status=excluded.status, updated_at=excluded.updated_at, data=excluded.data''',
                  (job['id'], job['status'], job['created_at'], job['updated_at'], json.dumps(job, ensure_ascii=False)))


def get_job(job_id):
    with connect() as c:
        row = c.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row['data']) if row else None


def list_jobs():
    with connect() as c:
        return [json.loads(r['data']) for r in c.execute('SELECT data FROM jobs ORDER BY created_at DESC')]


def revision(job):
    if job.get('article'):
        with connect() as c:
            c.execute('INSERT INTO revisions (job_id,created_at,data) VALUES (?,?,?)',
                      (job['id'], now(), json.dumps(job['article'], ensure_ascii=False)))


def revisions(job_id):
    with connect() as c:
        return [dict(r) | {'data': json.loads(r['data'])} for r in c.execute(
            'SELECT * FROM revisions WHERE job_id=? ORDER BY id DESC LIMIT 30', (job_id,))]
