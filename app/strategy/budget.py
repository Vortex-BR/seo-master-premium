"""Offline, conservative attempt limits for a strategy cycle.

The durable USD ledger remains the monetary authority. These counters bound
work before dispatch and never claim to measure provider billing. A timeout or
restart retains its entire attempted allowance; only application reuse spends
no new allowance. The current strategic agents consume supplied snapshots and
do not perform research or discover videos themselves.
"""
import json
from copy import deepcopy

from pydantic import ValidationError

from .contracts import StrategyBudget


VERSION = 'strategy-budget-v1'


class StrategyBudgetExceeded(ValueError):
    """The request was stopped before opening a paid provider operation."""


def initial_state():
    return {'version': VERSION, 'calls': 0, 'tokens_reserved': 0,
            'research_queries': 0, 'video_lookups': 0}


def effective_budget(cycle):
    return StrategyBudget.model_validate(cycle.get('budget') or {}).model_dump()


def request_bound(request):
    """Upper bound for this text request, without a network token counter.

    A token contains at least one UTF-8 byte; include all schema/instruction
    bytes and 4096 framing tokens, plus the configured output ceiling. This
    intentionally differs from measured usage and from the USD safety margin.
    """
    if request.get('tools'):
        raise StrategyBudgetExceeded(
            'O ciclo estratégico não dispõe de pesquisa ou descoberta de vídeos '
            'com integração e controle financeiro próprios. Nenhuma ferramenta foi chamada.')
    maximum = request.get('max_output_tokens')
    if type(maximum) is not int or maximum <= 0:
        raise StrategyBudgetExceeded(
            'A chamada estratégica não tem limite de saída válido. Nenhuma chamada foi enviada.')
    measured = {key: request[key] for key in
                ('model', 'instructions', 'input', 'text', 'tools', 'tool_choice') if key in request}
    return len(json.dumps(measured, ensure_ascii=False).encode('utf-8')) + 4096 + maximum


def consume(cycle, request):
    """Update the in-memory state; caller commits it atomically with an attempt."""
    limits = effective_budget(cycle)
    calls = cycle.get('calls', 0)
    if type(calls) is not int or calls < 0:
        raise StrategyBudgetExceeded('O histórico de chamadas estratégicas é inválido; os dados foram preservados.')
    if calls >= limits['max_agent_calls']:
        raise StrategyBudgetExceeded(
            'O ciclo estratégico atingiu o limite de chamadas configurado. '
            'Revise os resultados parciais ou ajuste o orçamento.')
    state = deepcopy(cycle.get('budget_state'))
    if state is None and calls == 0:
        state = initial_state()
    if (not isinstance(state, dict) or state.get('version') != VERSION
            or any(type(state.get(key)) is not int or state[key] < 0 for key in
                   ('calls', 'tokens_reserved', 'research_queries', 'video_lookups'))
            or state['calls'] != calls):
        raise StrategyBudgetExceeded(
            'As tentativas históricas deste ciclo não possuem um limite de tokens '
            'recuperável. Nenhuma nova chamada foi enviada; os dados foram preservados. '
            'Inicie um novo ciclo explicitamente.')
    tokens = request_bound(request)
    if state['tokens_reserved'] + tokens > limits['max_tokens_estimate']:
        raise StrategyBudgetExceeded(
            'O ciclo estratégico atingiu o limite conservador de tokens configurado. '
            'Retentativas e etapas anteriores mantêm sua reserva. Nenhuma chamada foi enviada.')
    # Discovery integrations are absent. Reject inconsistent future counters
    # rather than silently resetting them or allowing unguarded tool dispatch.
    if (state['research_queries'] > limits['max_research_queries']
            or state['video_lookups'] > limits['max_video_lookups']):
        raise StrategyBudgetExceeded('O ciclo estratégico ultrapassou os limites de serviços configurados.')
    state['calls'] += 1
    state['tokens_reserved'] += tokens
    cycle.update(calls=state['calls'], budget_state=state)
    return {'tokens_bound': tokens, 'tokens_bound_method': 'utf8-bytes-plus-framing-output-v1',
            'calls_after': state['calls'], 'tokens_reserved_after': state['tokens_reserved']}


def status(cycle):
    """Pure display projection, including explicit unavailable-service states."""
    valid_limits = True
    try:
        limits = effective_budget(cycle)
    except ValidationError:
        # Historical configurations remain inspectable. Validation is required
        # for dispatch, never as an implicit migration on a GET/read-only path.
        raw = cycle.get('budget')
        limits = deepcopy(raw) if isinstance(raw, dict) else {'legacy_value': raw}
        valid_limits = False
    state = cycle.get('budget_state')
    known = (isinstance(state, dict) and state.get('version') == VERSION
             and all(type(state.get(key)) is int and state[key] >= 0 for key in
                     ('calls', 'tokens_reserved', 'research_queries', 'video_lookups'))
             and state['calls'] == cycle.get('calls'))
    return {'version': VERSION, 'limits': limits, 'limits_valid': valid_limits, 'tracking_known': known,
            'calls': cycle.get('calls'),
            'tokens_reserved': state.get('tokens_reserved') if known else None,
            'tokens_notice': 'Reserva conservadora por tentativa; não equivale a tokens faturados.',
            'research_queries': {'available': False, 'limit': limits.get('max_research_queries'),
                                 'used': state.get('research_queries') if known else None},
            'video_lookups': {'available': False, 'limit': limits.get('max_video_lookups'),
                            'used': state.get('video_lookups') if known else None}}


def generation_job(cycle):
    """Synthetic entity for the existing text provider and monetary ledger."""
    job = {'id': cycle['id'], 'brief': {'topic': cycle.get('focus', ''), 'keyword': ''},
           'sources': [], 'usage': cycle.setdefault('usage', [])}
    explicit = effective_budget(cycle)['max_spend_usd']
    if explicit is not None:
        job['financial_budget_usd'] = explicit
    return job
