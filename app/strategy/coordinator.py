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
from . import agents, budget, store
from .contracts import StrategyPlan

logger = logging.getLogger(__name__)


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
    prepared = agents.prepare_execution(cycle, role, context)
    identity = agents.execution_identity(cycle, role, context, prepared=prepared)
    fingerprint = generation.article_hash(identity)

    # Check for cached result
    cached = store.cached_run(cycle, role, fingerprint)
    if cached and cached.get('execution_identity') == identity:
        from .. import cost_observability
        output = spec['schema'].model_validate(cached.get('output', {})).model_dump()
        cost_observability.record_cache(budget.generation_job(cycle), f'strategy_{role}',
                                        dependency_fingerprint=fingerprint,
                                        metadata={'strategy_run_id': cached['run_id']})
        cycle['completed'][role] = cached['run_id']
        store.save_cycle(cycle)
        return deepcopy(output), cached['run_id']

    cycle['current_role'] = role
    cycle['events'].append({
        'time': db.now(),
        'message': f'{spec["name"]}: analisando.'
    })
    cycle['events'] = cycle['events'][-80:]
    run_id = store.new_id()
    run = {
        'run_id': run_id,
        'name': spec['name'],
        'role': role,
        'sector': spec['sector'],
        'input_hash': fingerprint,
        'execution_identity': identity,
        'started_at': db.now(),
    }
    store.begin_attempt(cycle, role, fingerprint, run, prepared[0])

    try:
        output = generation.structured(
            budget.generation_job(cycle),
            spec['schema'],
            spec['prompt'],
            f'strategy_{role}',
            context,
            prepared=prepared,
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
    prepared = agents.prepare_execution(cycle, 'coordinator', context)
    identity = agents.execution_identity(cycle, 'coordinator', context, prepared=prepared)
    fingerprint = generation.article_hash(identity)
    cached = store.cached_run(cycle, 'coordinator', fingerprint)
    if cached and cached.get('execution_identity') == identity:
        from .. import cost_observability
        cost_observability.record_cache(budget.generation_job(cycle), 'strategy_coordinator',
                                        dependency_fingerprint=fingerprint,
                                        metadata={'strategy_run_id': cached['run_id']})
        return StrategyPlan.model_validate(cached.get('output', {})).model_dump()
    run_id = store.new_id()
    run = {
        'run_id': run_id,
        'name': 'Coordenador estratégico',
        'role': 'coordinator',
        'sector': 'coordination',
        'input_hash': fingerprint,
        'execution_identity': identity,
        'started_at': db.now(),
    }
    try:
        store.begin_attempt(cycle, 'coordinator', fingerprint, run, prepared[0])
    except budget.StrategyBudgetExceeded as exc:
        cycle.setdefault('events', []).append({'time': db.now(), 'message': str(exc)})
        cycle['events'] = cycle['events'][-80:]
        store.save_cycle(cycle)
        return _deterministic_plan(cycle, results)

    try:
        output = generation.structured(
            budget.generation_job(cycle),
            StrategyPlan,
            agents.COORDINATOR_PROMPT,
            'strategy_coordinator',
            context,
            prepared=prepared,
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
    seen = set()
    for i, finding in enumerate(all_findings[:20]):
        if finding.get('severity') in ('critical', 'opportunity'):
            # Restarting deterministic synthesis cannot manufacture another
            # opportunity identity for an unchanged finding in the same cycle.
            fingerprint = generation.article_hash({'cycle_id': cycle['id'], 'finding': finding})
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            opp_id = 'fallback_' + fingerprint[:32]
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
