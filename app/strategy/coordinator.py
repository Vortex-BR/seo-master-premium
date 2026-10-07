"""Strategic coordinator — orchestrates 8 specialist agents.

The coordinator:
1. Collects project context (business, pages, connections).
2. Runs agents in dependency phases.
3. Synthesises findings into a prioritised StrategyPlan with opportunities.
4. Enforces budget limits and checkpoints.
"""
import json
import logging
from copy import deepcopy

from .. import db, generation
from ..security import get_secret
from . import agents, store
from .contracts import StrategyPlan

logger = logging.getLogger(__name__)


def _input_hash(context):
    """Deterministic hash for an agent's input to enable caching."""
    return generation.article_hash(context)


def _build_context(cycle, role, prior_results):
    """Build the input context for a strategic agent."""
    spec = agents.ROLES[role]
    context = {
        'project': cycle.get('project_context', {}),
        'focus': cycle.get('focus', ''),
        'role': role,
        'name': spec['name'],
        'sector': spec['sector'],
    }
    # Inject dependencies' results
    for dep in spec['dependencies']:
        if dep in prior_results:
            context[f'{dep}_analysis'] = prior_results[dep]
    # Add data snapshots relevant to the role
    if cycle.get('gsc_data') and role in ('performance', 'competitors', 'results', 'architecture'):
        context['search_console'] = cycle['gsc_data']
    if cycle.get('ga4_data') and role in ('performance', 'results'):
        context['analytics'] = cycle['ga4_data']
    if cycle.get('audit_data') and role in ('technical', 'architecture'):
        context['site_audit'] = cycle['audit_data']
    if cycle.get('inventory') and role in ('architecture', 'curation', 'business'):
        context['page_inventory'] = cycle['inventory']
    if cycle.get('serp_data') and role in ('competitors', 'intent'):
        context['serp_results'] = cycle['serp_data']
    if cycle.get('videos') and role == 'curation':
        context['available_videos'] = cycle['videos']
    if cycle.get('prior_interventions') and role == 'results':
        context['interventions'] = cycle['prior_interventions']
    return context


def invoke_agent(cycle, role, prior_results):
    """Execute a single strategic agent, with caching and budget enforcement.

    Returns the agent's output dict and the run_id.
    """
    spec = agents.ROLES[role]
    context = _build_context(cycle, role, prior_results)
    fingerprint = _input_hash(context)

    # Check for cached result
    cached = store.cached_run(cycle, role, fingerprint)
    if cached:
        cycle['completed'][role] = cached['run_id']
        store.save_cycle(cycle)
        return deepcopy(cached.get('output', {})), cached['run_id']

    # Budget check
    if cycle['calls'] >= cycle['budget']['max_agent_calls']:
        raise ValueError(
            'O ciclo estratégico atingiu o limite de chamadas configurado. '
            'Revise os resultados parciais ou ajuste o orçamento.'
        )

    cycle['calls'] += 1
    cycle['current_role'] = role
    cycle['events'].append({
        'time': db.now(),
        'message': f'{spec["name"]}: analisando.'
    })
    cycle['events'] = cycle['events'][-80:]
    store.save_cycle(cycle)

    run_id = store.new_id()
    run = {
        'run_id': run_id,
        'name': spec['name'],
        'role': role,
        'sector': spec['sector'],
        'input_hash': fingerprint,
        'started_at': db.now(),
    }
    store.save_run(cycle, role, fingerprint, run, run_id)

    try:
        output = generation.structured(
            # We pass a minimal "job-like" dict for compatibility with generation.structured
            {'id': cycle['id'], 'brief': {'topic': cycle.get('focus', ''), 'keyword': ''},
             'sources': [], 'usage': cycle.setdefault('usage', [])},
            spec['schema'],
            spec['prompt'],
            f'strategy_{role}',
            context,
        )
        output = spec['schema'].model_validate(output).model_dump()
        run.update(output=output, finished_at=db.now(), status='completed')
        store.save_run(cycle, role, fingerprint, run, run_id, 'completed')
        cycle['completed'][role] = run_id
        store.save_cycle(cycle)
        return deepcopy(output), run_id

    except Exception as exc:
        run.update(
            error_type=type(exc).__name__,
            finished_at=db.now(),
            status='failed',
        )
        store.save_run(cycle, role, fingerprint, run, run_id, 'failed')
        raise


def synthesise(cycle, results):
    """Coordinator synthesis: combine agent results into a StrategyPlan.

    Uses an AI call to produce the final plan, or falls back to a
    deterministic aggregation if the AI call fails.
    """
    context = {
        'project': cycle.get('project_context', {}),
        'focus': cycle.get('focus', ''),
        'agent_results': {role: result for role, result in results.items()},
        'page_inventory': cycle.get('inventory', []),
    }
    fingerprint = _input_hash(context)
    cached = store.cached_run(cycle, 'coordinator', fingerprint)
    if cached:
        return deepcopy(cached.get('output', {}))

    if cycle['calls'] >= cycle['budget']['max_agent_calls']:
        # Fall back to deterministic aggregation
        return _deterministic_plan(cycle, results)

    cycle['calls'] += 1
    run_id = store.new_id()
    run = {
        'run_id': run_id,
        'name': 'Coordenador estratégico',
        'role': 'coordinator',
        'sector': 'coordination',
        'input_hash': fingerprint,
        'started_at': db.now(),
    }
    store.save_run(cycle, 'coordinator', fingerprint, run, run_id)

    try:
        output = generation.structured(
            {'id': cycle['id'], 'brief': {'topic': cycle.get('focus', ''), 'keyword': ''},
             'sources': [], 'usage': cycle.setdefault('usage', [])},
            StrategyPlan,
            agents.STRATEGY_RULES + '''
Você é o coordenador estratégico. Recebeu os pareceres de todos os especialistas.
Sua tarefa:
1. Sintetize os resultados numa análise coesa.
2. Identifique conflitos entre pareceres e preserve ambas as posições.
3. Proponha oportunidades priorizadas com ações concretas: criar, atualizar, consolidar,
   melhorar links/título, corrigir problema técnico ou investigar.
4. Cada oportunidade deve ter: pergunta do leitor, evidências, justificativa, esforço e plano
   de acompanhamento. Não atribua probabilidade de sucesso.
5. Considere o inventário de páginas existentes antes de propor nova URL.
6. Vídeos e fontes selecionados pela curadoria devem acompanhar as oportunidades de conteúdo.
7. Registre lacunas de contexto e foco para o próximo ciclo.
Ordene por: demanda observada, valor para o negócio, adequação à intenção, oportunidade
competitiva e esforço. Os pesos são heurísticas, não probabilidades.''',
            'strategy_coordinator',
            context,
        )
        output = StrategyPlan.model_validate(output).model_dump()
        run.update(output=output, finished_at=db.now(), status='completed')
        store.save_run(cycle, 'coordinator', fingerprint, run, run_id, 'completed')
        return output

    except Exception as exc:
        run.update(error_type=type(exc).__name__, finished_at=db.now(), status='failed')
        store.save_run(cycle, 'coordinator', fingerprint, run, run_id, 'failed')
        logger.warning('Coordinator synthesis failed: %s. Using deterministic plan.', type(exc).__name__)
        return _deterministic_plan(cycle, results)


def _deterministic_plan(cycle, results):
    """Build a plan without an AI call by aggregating all findings."""
    all_findings = []
    for role, result in results.items():
        for finding in result.get('findings', []):
            all_findings.append({'agent': role, **finding})

    # Sort by severity: critical first, then opportunity, then info
    severity_order = {'critical': 0, 'opportunity': 1, 'info': 2}
    all_findings.sort(key=lambda f: severity_order.get(f.get('severity', 'info'), 3))

    opportunities = []
    for i, finding in enumerate(all_findings[:20]):
        if finding.get('severity') in ('critical', 'opportunity'):
            opp_id = store.new_id()
            opportunities.append({
                'opportunity_id': opp_id,
                'action': 'investigate',
                'main_question': finding.get('summary', ''),
                'target_page': ', '.join(finding.get('related_pages', [])[:3]),
                'queries': finding.get('related_queries', [])[:10],
                'related_products': [],
                'selected_videos': [],
                'evidence': finding.get('evidence', []),
                'justification': f'[{finding["agent"]}] {finding.get("suggestion", "")}',
                'gaps': [],
                'effort': 'medium',
                'priority_score': max(0, 80 - i * 5),
                'monitoring_plan': '',
                'status': 'proposed',
            })

    return {
        'summary': 'Plano gerado por agregação determinística dos pareceres dos especialistas.',
        'opportunities': opportunities,
        'conflicts': [],
        'context_gaps': [g for r in results.values() for g in r.get('context_gaps', [])],
        'next_cycle_focus': '',
    }
