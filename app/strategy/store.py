"""Persistence layer for strategic intelligence cycles.

Uses the same db.connect() / SQLite pattern as editorial/store.py.
Tables: strategy_cycles, strategy_runs, opportunities.
"""
import json
import uuid

from .. import db, generation


def init():
    """Create strategy tables if they do not exist."""
    with db.connect() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS strategy_cycles (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS strategy_cycles_project
                ON strategy_cycles(project_id, created_at);

            CREATE TABLE IF NOT EXISTS strategy_runs (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                role TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS strategy_runs_cycle
                ON strategy_runs(cycle_id, role, status);

            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS opportunities_project
                ON opportunities(project_id, status, created_at);
        ''')


def new_id():
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

def save_cycle(cycle):
    """Insert or update a strategy cycle."""
    cycle['updated_at'] = db.now()
    with db.connect() as c:
        c.execute('''INSERT INTO strategy_cycles VALUES (?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, updated_at=excluded.updated_at, data=excluded.data''',
                  (cycle['id'], cycle['project_id'], cycle['status'],
                   cycle['created_at'], cycle['updated_at'],
                   json.dumps(cycle, ensure_ascii=False)))


def get_cycle(cycle_id):
    """Retrieve a strategy cycle by id."""
    with db.connect() as c:
        row = c.execute('SELECT data FROM strategy_cycles WHERE id=?', (cycle_id,)).fetchone()
    return json.loads(row['data']) if row else None


def list_cycles(project_id='default', limit=20):
    """List cycles for a project, newest first."""
    with db.connect() as c:
        rows = c.execute(
            'SELECT data FROM strategy_cycles WHERE project_id=? ORDER BY created_at DESC LIMIT ?',
            (project_id, limit))
        return [json.loads(r['data']) for r in rows]


# ---------------------------------------------------------------------------
# Agent runs (within a cycle)
# ---------------------------------------------------------------------------

def save_run(cycle, role, fingerprint, data, run_id=None, status='running'):
    """Persist an agent execution record."""
    run_id = run_id or new_id()
    with db.connect() as c:
        c.execute('''INSERT INTO strategy_runs VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, data=excluded.data''',
                  (run_id, cycle['id'], role, fingerprint, status, db.now(),
                   json.dumps(data, ensure_ascii=False)))
    return run_id


def cached_run(cycle, role, fingerprint):
    """Find a completed run for this cycle/role/fingerprint."""
    with db.connect() as c:
        row = c.execute(
            '''SELECT data FROM strategy_runs
               WHERE cycle_id=? AND role=? AND input_hash=? AND status='completed'
               ORDER BY created_at DESC LIMIT 1''',
            (cycle['id'], role, fingerprint)).fetchone()
    return json.loads(row['data']) if row else None


def get_run(run_id):
    """Retrieve a specific agent run."""
    with db.connect() as c:
        row = c.execute(
            "SELECT data FROM strategy_runs WHERE id=? AND status='completed'",
            (run_id,)).fetchone()
    return json.loads(row['data']) if row else None


def cycle_runs(cycle_id):
    """List all runs for a cycle."""
    with db.connect() as c:
        rows = c.execute(
            'SELECT data FROM strategy_runs WHERE cycle_id=? ORDER BY created_at',
            (cycle_id,))
        return [json.loads(r['data']) for r in rows]


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------

def save_opportunity(opportunity, cycle, project_id='default'):
    """Insert or update an opportunity."""
    opportunity['updated_at'] = db.now()
    with db.connect() as c:
        c.execute('''INSERT INTO opportunities VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, updated_at=excluded.updated_at, data=excluded.data''',
                  (opportunity['opportunity_id'], cycle['id'], project_id,
                   opportunity['status'], opportunity.get('created_at', db.now()),
                   opportunity['updated_at'],
                   json.dumps(opportunity, ensure_ascii=False)))


def get_opportunity(opportunity_id):
    """Retrieve an opportunity by id."""
    with db.connect() as c:
        row = c.execute('SELECT data FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
    return json.loads(row['data']) if row else None


def list_opportunities(project_id='default', status=None, limit=50):
    """List opportunities for a project, optionally filtered by status."""
    with db.connect() as c:
        if status:
            rows = c.execute(
                'SELECT data FROM opportunities WHERE project_id=? AND status=? ORDER BY created_at DESC LIMIT ?',
                (project_id, status, limit))
        else:
            rows = c.execute(
                'SELECT data FROM opportunities WHERE project_id=? ORDER BY created_at DESC LIMIT ?',
                (project_id, limit))
        return [json.loads(r['data']) for r in rows]


def cycle_report(cycle_id):
    """Build a summary report for a strategy cycle."""
    cycle = get_cycle(cycle_id)
    if not cycle:
        return None
    runs = cycle_runs(cycle_id)
    with db.connect() as c:
        opps = [json.loads(r['data']) for r in c.execute(
            'SELECT data FROM opportunities WHERE cycle_id=? ORDER BY created_at', (cycle_id,))]
    return {
        'cycle': cycle,
        'runs': runs,
        'opportunities': opps,
        'agent_count': len([r for r in runs if r.get('status') == 'completed']),
    }
