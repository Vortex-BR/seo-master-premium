"""Strategy engine — executor with persistence, checkpoints and budget control.

Runs strategy cycles as background jobs, checkpointing after each agent
so that restarts resume from the last completed step.
"""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

from .. import db, generation
from ..security import get_secret
from . import agents, coordinator, store

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='strategy')
cycle_lock = threading.RLock()

ACTIVE_STATES = {'queued', 'collecting', 'analyzing', 'planning'}


def safe_error(exc):
    """Return a user-safe error message without leaking internal details."""
    if isinstance(exc, ValidationError):
        return 'A IA devolveu uma resposta fora do formato esperado para a análise estratégica.'
    if isinstance(exc, AuthenticationError):
        return 'A chave OpenAI não foi aceita. Confira a chave em Integrações.'
    if isinstance(exc, RateLimitError):
        return 'A OpenAI informou limite de uso ou saldo insuficiente.'
    if isinstance(exc, APIConnectionError):
        return 'Não foi possível conectar à OpenAI. Tente novamente em alguns minutos.'
    if isinstance(exc, APIStatusError):
        return f'A OpenAI retornou HTTP {exc.status_code}.'
    if isinstance(exc, ValueError):
        return str(exc)[:1000]
    return 'A análise estratégica não pôde ser concluída. Os dados foram preservados.'


def step(cycle, status, message):
    """Update cycle status and append an event."""
    cycle['status'] = status
    cycle.setdefault('events', []).append({'time': db.now(), 'message': message})
    cycle['events'] = cycle['events'][-80:]
    store.save_cycle(cycle)


def start(project_id='default', focus='', budget=None):
    """Create and queue a new strategy cycle."""
    from .contracts import StrategyBudget
    budget = budget or StrategyBudget().model_dump()
    if isinstance(budget, StrategyBudget):
        budget = budget.model_dump()

    cycle = {
        'id': store.new_id(),
        'project_id': project_id,
        'status': 'queued',
        'created_at': db.now(),
        'updated_at': db.now(),
        'focus': focus,
        'budget': budget,
        'calls': 0,
        'completed': {},
        'current_role': None,
        'events': [],
        'usage': [],
        'error': None,
        'plan': None,
        'agents_version': agents.VERSION,
    }
    store.save_cycle(cycle)
    return cycle


def run(cycle_id):
    """Execute a full strategy cycle: run agents in phases, then synthesise."""
    cycle = store.get_cycle(cycle_id)
    if not cycle:
        return

    try:
        if not get_secret('openai_api_key'):
            step(cycle, 'failed', 'Configure a chave OpenAI em Integrações.')
            cycle['error'] = 'Configure a chave OpenAI em Integrações.'
            store.save_cycle(cycle)
            return

        step(cycle, 'collecting', 'Coletando contexto do projeto e dados disponíveis.')

        # Phase: collect project context (placeholder for OpenSEO integration)
        if not cycle.get('project_context'):
            cycle['project_context'] = _collect_project_context(cycle)
            store.save_cycle(cycle)

        step(cycle, 'analyzing', 'Executando análise dos especialistas.')

        # Run agents in dependency phases
        results = {}
        for phase_index, phase_roles in enumerate(agents.PHASES):
            for role in phase_roles:
                if role in cycle['completed']:
                    # Retrieve cached result
                    run_data = store.get_run(cycle['completed'][role])
                    if run_data and run_data.get('output'):
                        results[role] = run_data['output']
                        continue

                try:
                    output, run_id = coordinator.invoke_agent(cycle, role, results)
                    results[role] = output
                    step(cycle, 'analyzing',
                         f'{agents.ROLES[role]["name"]} concluído ({len(results)}/{len(agents.ROLES)}).')
                except Exception as exc:
                    logger.warning('Strategy agent %s failed: %s', role, type(exc).__name__)
                    cycle['events'].append({
                        'time': db.now(),
                        'message': f'{agents.ROLES[role]["name"]}: falhou — {safe_error(exc)}'
                    })
                    store.save_cycle(cycle)
                    # Non-critical agents can fail without stopping the cycle
                    if role in ('business', 'performance'):
                        raise  # These are critical
                    continue

        step(cycle, 'planning', 'Coordenador sintetizando o plano estratégico.')

        # Synthesise the strategy plan
        plan = coordinator.synthesise(cycle, results)
        cycle['plan'] = plan

        # Save opportunities
        for opp in plan.get('opportunities', []):
            opp['created_at'] = db.now()
            store.save_opportunity(opp, cycle, cycle['project_id'])

        cycle['current_role'] = None
        cycle['error'] = None
        step(cycle, 'ready', f'Análise concluída: {len(plan.get("opportunities", []))} oportunidade(s) identificada(s).')

    except Exception as exc:
        logger.warning('Strategy cycle %s failed: %s', cycle_id, type(exc).__name__)
        cycle['error'] = safe_error(exc)
        cycle['current_role'] = None
        step(cycle, 'failed', cycle['error'])


def submit(project_id='default', focus='', budget=None):
    """Start a strategy cycle in the background."""
    with cycle_lock:
        active = [c for c in store.list_cycles(project_id, limit=5)
                  if c['status'] in ACTIVE_STATES]
        if active:
            raise ValueError('Já existe um ciclo estratégico em andamento para este projeto.')

        cycle = start(project_id, focus, budget)
        executor.submit(run, cycle['id'])
        return cycle


def resume(cycle_id):
    """Resume a failed or interrupted cycle."""
    with cycle_lock:
        cycle = store.get_cycle(cycle_id)
        if not cycle:
            raise ValueError('Ciclo não encontrado.')
        if cycle['status'] in ACTIVE_STATES:
            raise ValueError('Este ciclo já está em andamento.')
        if cycle['status'] == 'ready':
            raise ValueError('Este ciclo já foi concluído.')
        cycle['error'] = None
        step(cycle, 'queued', 'Ciclo retomado. Etapas concluídas serão reaproveitadas.')
        executor.submit(run, cycle['id'])
        return cycle


def recover():
    """Mark active cycles as interrupted after a restart."""
    for project_cycles in _all_active_cycles():
        for cycle in project_cycles:
            if cycle['status'] in ACTIVE_STATES:
                cycle['error'] = 'O serviço reiniciou durante a análise. Retome o ciclo.'
                step(cycle, 'interrupted', cycle['error'])


def _all_active_cycles():
    """Find all cycles with active status across all projects."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT data FROM strategy_cycles WHERE status IN ('queued','collecting','analyzing','planning')")
        result = [json.loads(r['data']) for r in rows]
    # Group by project for the caller
    projects = {}
    for cycle in result:
        projects.setdefault(cycle['project_id'], []).append(cycle)
    return list(projects.values())


def _collect_project_context(cycle):
    """Collect project context from available sources.

    This is a placeholder that returns basic context structure.
    Full implementation will integrate with OpenSEO project data.
    """
    return {
        'project_id': cycle['project_id'],
        'collected_at': db.now(),
        'business': {
            'name': db.get_setting('brand_name', ''),
            'voice': db.get_setting('brand_voice', ''),
        },
        'connections': {
            'openai': bool(get_secret('openai_api_key')),
            'wordpress': bool(db.get_setting('wp_url', '')),
        },
        'note': 'Contexto básico. Integração completa com OpenSEO pendente.',
    }

