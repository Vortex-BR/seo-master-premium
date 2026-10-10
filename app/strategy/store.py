"""Persistence layer for strategic intelligence cycles.

Uses the same db.connect() / SQLite pattern as editorial/store.py.
Tables: strategy_cycles, strategy_runs, opportunities.
"""
import json
import uuid
from copy import deepcopy
from contextlib import nullcontext

from .. import db, generation


class OpportunityConflict(ValueError):
    """A strategic command cannot safely proceed in the current state."""


class OpportunityNotFound(LookupError):
    """The requested public opportunity identifier does not exist."""


class ProductionQueueFull(OpportunityConflict):
    """The bounded editorial queue has no room for a new intent."""


_OPPORTUNITY_SELECT = '''SELECT o.*, COALESCE(k.provider_opportunity_id,o.id) AS provider_opportunity_id
                       FROM opportunities o LEFT JOIN strategy_opportunity_keys k ON k.opportunity_id=o.id'''


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

            CREATE TABLE IF NOT EXISTS strategy_opportunity_keys (
                opportunity_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                provider_opportunity_id TEXT NOT NULL,
                UNIQUE(project_id, cycle_id, provider_opportunity_id)
            );

            CREATE TABLE IF NOT EXISTS strategy_production_intents (
                id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL,
                UNIQUE(opportunity_id, operation_key),
                UNIQUE(job_id)
            );
        ''')
        # Additive migration: retain the original seven-column opportunity
        # table so older readers/writers remain schema-compatible on rollback.
        # Provider IDs are scoped in an independent identity table.
        c.execute('BEGIN IMMEDIATE')
        c.execute('''INSERT OR IGNORE INTO strategy_opportunity_keys
                     (opportunity_id,project_id,cycle_id,provider_opportunity_id)
                     SELECT id,project_id,cycle_id,id FROM opportunities''')


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

def save_opportunity(opportunity, cycle, project_id='default', _connection=None):
    """Persist a proposed opportunity without overwriting scope or human commands.

    The first public ID remains compatible with legacy links.  If another
    project/cycle received the same provider ID, its public ID is deterministic
    for that scope.  The caller's dict is updated so the cycle plan uses the
    public identifier returned by the store.
    """
    candidate = deepcopy(opportunity)
    provider_id = candidate.get('provider_opportunity_id') or candidate['opportunity_id']
    cycle_id = cycle['id']
    with (db.connect() if _connection is None else nullcontext(_connection)) as c:
        if _connection is None:
            c.execute('BEGIN IMMEDIATE')
        row = c.execute(_OPPORTUNITY_SELECT + '''
                           WHERE o.project_id=? AND o.cycle_id=?
                           AND COALESCE(k.provider_opportunity_id,o.id)=?''',
                        (project_id, cycle_id, provider_id)).fetchone()
        if row:
            previous = _opportunity(row)
            public_id = previous['opportunity_id']
            candidate['created_at'] = previous['created_at']
            if previous.get('decision_history') or previous.get('job_id') or previous['status'] != 'proposed':
                # Regenerated model output is a proposal, never a new human
                # decision or an instruction to unlink an existing paid job.
                for key in ('status', 'decision_history', 'job_id', 'intent_id', 'production_history'):
                    if key in previous:
                        candidate[key] = deepcopy(previous[key])
                for key in ('action', 'main_question', 'target_page', 'queries', 'related_products',
                            'selected_videos', 'evidence', 'justification', 'gaps', 'monitoring_plan'):
                    if key in previous:
                        candidate[key] = deepcopy(previous[key])
        else:
            public_id = candidate['opportunity_id']
            if c.execute('SELECT 1 FROM opportunities WHERE id=?', (public_id,)).fetchone():
                scoped = json.dumps([project_id, cycle_id, provider_id], ensure_ascii=False)
                public_id = 'opp_' + uuid.uuid5(uuid.NAMESPACE_URL, scoped).hex
            # UUID-derived public IDs cannot silently overwrite a legacy ID.
            if c.execute('SELECT 1 FROM opportunities WHERE id=?', (public_id,)).fetchone():
                raise OpportunityConflict('O identificador desta pauta conflita com outro registro; os dados foram preservados.')
            candidate.setdefault('created_at', db.now())
        candidate.update(opportunity_id=public_id, provider_opportunity_id=provider_id,
                         cycle_id=cycle_id, project_id=project_id, updated_at=db.now())
        _save_opportunity(c, candidate)
    opportunity.clear()
    opportunity.update(candidate)
    return deepcopy(candidate)


def validate_plan_opportunities(plan):
    """Reject ambiguous model IDs before any opportunity from the batch is saved."""
    opportunities = plan.get('opportunities', [])
    if not isinstance(opportunities, list):
        raise OpportunityConflict('O plano estratégico devolveu uma lista de pautas inválida.')
    seen = set()
    for opportunity in opportunities:
        identifier = opportunity.get('opportunity_id') if isinstance(opportunity, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 64:
            raise OpportunityConflict('O plano estratégico contém um identificador de pauta inválido; nada foi salvo.')
        if identifier in seen:
            raise OpportunityConflict('O plano estratégico repetiu um identificador de pauta; nenhuma oportunidade deste lote foi salva.')
        seen.add(identifier)


def save_plan_opportunities(plan, cycle, project_id='default'):
    """Persist a complete proposal batch atomically, preserving human decisions."""
    validate_plan_opportunities(plan)
    candidates = deepcopy(plan.get('opportunities', []))
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for candidate in candidates:
            # Model output cannot grant itself human approval.
            candidate['status'] = 'proposed'
            save_opportunity(candidate, cycle, project_id, _connection=c)
    plan['opportunities'] = candidates
    return deepcopy(candidates)


def _opportunity(row):
    """Hydrate scope from indexed columns without rewriting legacy JSON on read."""
    if row is None:
        return None
    result = json.loads(row['data'])
    result.update(opportunity_id=row['id'], cycle_id=row['cycle_id'],
                  project_id=row['project_id'], status=row['status'],
                  created_at=row['created_at'], updated_at=row['updated_at'],
                  provider_opportunity_id=row['provider_opportunity_id'])
    return result


def _save_opportunity(c, opportunity):
    c.execute('''INSERT INTO opportunities
                 (id,cycle_id,project_id,status,created_at,updated_at,data)
                 VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status, updated_at=excluded.updated_at, data=excluded.data''',
              (opportunity['opportunity_id'], opportunity['cycle_id'], opportunity['project_id'],
               opportunity['status'], opportunity['created_at'], opportunity['updated_at'],
               json.dumps(opportunity, ensure_ascii=False)))
    c.execute('''INSERT INTO strategy_opportunity_keys
                 (opportunity_id,project_id,cycle_id,provider_opportunity_id) VALUES (?,?,?,?)
                 ON CONFLICT(opportunity_id) DO NOTHING''',
              (opportunity['opportunity_id'], opportunity['project_id'], opportunity['cycle_id'],
               opportunity['provider_opportunity_id']))


def get_opportunity(opportunity_id):
    """Retrieve an opportunity by id."""
    with db.connect() as c:
        row = c.execute(_OPPORTUNITY_SELECT + ' WHERE o.id=?', (opportunity_id,)).fetchone()
    return _opportunity(row)


def list_opportunities(project_id='default', status=None, limit=50):
    """List opportunities for a project, optionally filtered by status."""
    with db.connect() as c:
        if status:
            rows = c.execute(
                _OPPORTUNITY_SELECT + ' WHERE o.project_id=? AND o.status=? ORDER BY o.created_at DESC LIMIT ?',
                (project_id, status, limit))
        else:
            rows = c.execute(
                _OPPORTUNITY_SELECT + ' WHERE o.project_id=? ORDER BY o.created_at DESC LIMIT ?',
                (project_id, limit))
        return [_opportunity(r) for r in rows]


def decide_opportunity(opportunity_id, action, reason='', actor='Administrador'):
    """Serialize approval/rejection with production and preserve every decision."""
    if action not in ('approve', 'reject'):
        raise OpportunityConflict('Decisão de pauta inválida.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        opportunity = _opportunity(c.execute(_OPPORTUNITY_SELECT + ' WHERE o.id=?', (opportunity_id,)).fetchone())
        if opportunity is None:
            raise OpportunityNotFound('Oportunidade não encontrada.')
        if opportunity['status'] not in ('proposed', 'approved', 'rejected'):
            raise OpportunityConflict('Esta pauta já iniciou produção; a decisão anterior foi preservada. Acompanhe o artigo existente.')
        opportunity['status'] = 'approved' if action == 'approve' else 'rejected'
        opportunity.setdefault('decision_history', []).append({
            'action': action, 'reason': reason, 'at': db.now(), 'actor': actor,
        })
        opportunity['updated_at'] = db.now()
        _save_opportunity(c, opportunity)
    return deepcopy(opportunity)


def _production_result(intent, created=False):
    return {'ok': True, 'job_id': intent['job_id'], 'intent_id': intent['id'],
            'opportunity_id': intent['opportunity_id'], 'created': created, 'reused': not created}


def create_production(opportunity_id, job_factory, operation_key=None,
                      queue_limit=10, active_states=None):
    """Atomically create one intent, its editorial job and its opportunity link.

    ``job_factory(opportunity)`` only builds an in-memory job; it must not call
    providers, save to the database or enqueue work.  Dispatch happens after
    commit and only the ``created`` caller dispatches.  A dispatch failure keeps
    the same recoverable job for the normal job-resume command.
    """
    operation_key = operation_key or 'default'
    if operation_key != 'default':
        raise OpportunityConflict('Esta pauta já possui uma intenção única; trocar a chave não autoriza uma segunda produção.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        opportunity = _opportunity(c.execute(_OPPORTUNITY_SELECT + ' WHERE o.id=?', (opportunity_id,)).fetchone())
        if opportunity is None:
            raise OpportunityNotFound('Oportunidade não encontrada.')
        if opportunity['status'] in ('rejected', 'cancelled'):
            raise OpportunityConflict('Esta pauta foi rejeitada ou cancelada; não foi iniciada uma produção.')
        row = c.execute('''SELECT data FROM strategy_production_intents
                           WHERE opportunity_id=? AND operation_key=?''',
                        (opportunity_id, operation_key)).fetchone()
        if row:
            intent = json.loads(row['data'])
            if not c.execute('SELECT 1 FROM jobs WHERE id=?', (intent['job_id'],)).fetchone():
                raise OpportunityConflict('O artigo associado a esta intenção não está disponível; o vínculo foi preservado para recuperação.')
            return _production_result(intent)
        if opportunity.get('job_id'):
            # Adopt a pre-patch link without regenerating or modifying the job.
            job_id = opportunity['job_id']
            if not c.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
                raise OpportunityConflict('O artigo associado à pauta não está disponível; não foi criada uma segunda produção.')
            intent = _new_intent(opportunity, operation_key, job_id, legacy=True)
            _save_intent(c, intent)
            return _production_result(intent)
        if opportunity.get('action') != 'create':
            raise OpportunityConflict('Esta ação estratégica não cria um novo artigo. Aprove uma pauta de criação compatível.')
        if opportunity['status'] != 'approved':
            raise OpportunityConflict('Aprove esta pauta antes de iniciar uma nova produção.')
        active_states = set(active_states or {
            'queued', 'extracting', 'analyzing', 'researching', 'writing', 'optimizing', 'reviewing',
        })
        placeholders = ','.join('?' for _ in active_states)
        queued = c.execute(f'''SELECT COUNT(*) FROM jobs WHERE status IN ({placeholders})
                               OR (status='new' AND id IN (SELECT job_id FROM strategy_production_intents))''',
                           tuple(active_states)).fetchone()[0]
        if queued >= queue_limit:
            raise ProductionQueueFull('A fila está cheia. Aguarde os artigos em andamento.')
        job = deepcopy(job_factory(deepcopy(opportunity)))
        if not job.get('id') or job.get('status') != 'new' or not job.get('created_at'):
            raise OpportunityConflict('O pedido de produção não pôde ser preparado com segurança.')
        if c.execute('SELECT 1 FROM jobs WHERE id=?', (job['id'],)).fetchone():
            raise OpportunityConflict('O identificador do artigo já está em uso; nenhuma produção foi substituída.')
        intent = _new_intent(opportunity, operation_key, job['id'])
        job.update(opportunity_id=opportunity_id, strategy_intent_id=intent['id'],
                   strategy_project_id=opportunity['project_id'], strategy_cycle_id=opportunity['cycle_id'],
                   updated_at=db.now())
        c.execute('INSERT INTO jobs (id,status,created_at,updated_at,data) VALUES (?,?,?,?,?)',
                  (job['id'], job['status'], job['created_at'], job['updated_at'], json.dumps(job, ensure_ascii=False)))
        _save_intent(c, intent)
        opportunity.update(status='in_progress', job_id=job['id'], intent_id=intent['id'], updated_at=db.now())
        opportunity.setdefault('production_history', []).append({
            'intent_id': intent['id'], 'job_id': job['id'], 'operation_key': operation_key, 'at': intent['created_at'],
        })
        _save_opportunity(c, opportunity)
        return _production_result(intent, created=True)


def _new_intent(opportunity, operation_key, job_id, legacy=False):
    return {'id': new_id(), 'opportunity_id': opportunity['opportunity_id'],
            'project_id': opportunity['project_id'], 'cycle_id': opportunity['cycle_id'],
            'operation_key': operation_key, 'job_id': job_id, 'created_at': db.now(),
            'legacy_link': legacy, 'contract_version': 1}


def _save_intent(c, intent):
    if c.execute('SELECT 1 FROM strategy_production_intents WHERE job_id=?', (intent['job_id'],)).fetchone():
        raise OpportunityConflict('Este artigo já pertence a outra intenção; os vínculos existentes foram preservados.')
    c.execute('INSERT INTO strategy_production_intents VALUES (?,?,?,?,?,?)',
              (intent['id'], intent['opportunity_id'], intent['operation_key'], intent['job_id'],
               intent['created_at'], json.dumps(intent, ensure_ascii=False)))


def cycle_report(cycle_id):
    """Build a summary report for a strategy cycle."""
    cycle = get_cycle(cycle_id)
    if not cycle:
        return None
    runs = cycle_runs(cycle_id)
    with db.connect() as c:
        opps = [_opportunity(r) for r in c.execute(
            _OPPORTUNITY_SELECT + ' WHERE o.cycle_id=? ORDER BY o.created_at', (cycle_id,))]
    return {
        'cycle': cycle,
        'runs': runs,
        'opportunities': opps,
        'agent_count': len([r for r in runs if r.get('status') == 'completed']),
    }
