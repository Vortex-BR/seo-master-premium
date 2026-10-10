"""Strategic limits are durable pre-dispatch guards, independent of billing."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

from app import cost_observability, db, generation, spending
from app.strategy import agents, budget, coordinator, engine, store
from app.strategy.contracts import StrategyBudget


def make_cycle(client, **limits):
    cycle = engine.start('p101-budget', budget=limits)
    cycle['project_context'] = {'project_id': 'p101-budget', 'business': {'name': 'Horta'}}
    store.save_cycle(cycle)
    return cycle


def business_request(cycle):
    return agents.prepare_execution(cycle, 'business', coordinator._build_context(cycle, 'business', {}))[0]


def successful_business(*args, **kwargs):
    return {'summary': 'Conteúdo de teste offline.', 'business_topics': ['Horta']}


def observe_rows(table, cycle):
    with db.connect() as c:
        return [json.loads(row['data']) for row in c.execute(
            f'SELECT data FROM {table} WHERE entity_id=?', (cycle['id'],))]


def test_small_explicit_allowances_are_accepted_and_financial_cap_is_not_global():
    config = StrategyBudget(max_agent_calls=1, max_tokens_estimate=12, max_spend_usd=3.5)
    assert config.max_spend_usd == 3.5
    assert budget.generation_job({'id': 'cycle', 'budget': config.model_dump()})['financial_budget_usd'] == 3.5
    assert 'financial_budget_usd' not in budget.generation_job({'id': 'default', 'budget': {}})


def test_token_limit_refuses_before_dispatch_without_consuming_attempt(client, monkeypatch):
    cycle = make_cycle(client, max_tokens_estimate=1)
    called = []
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: called.append(args))
    with pytest.raises(budget.StrategyBudgetExceeded, match='limite conservador de tokens'):
        coordinator.invoke_agent(cycle, 'business', {})
    saved = store.get_cycle(cycle['id'])
    assert not called and not store.cycle_runs(cycle['id'])
    assert saved['calls'] == 0 and saved['budget_state']['tokens_reserved'] == 0


def test_explicit_cycle_money_allowance_is_honored_before_provider_send(client, monkeypatch):
    cycle = make_cycle(client, max_spend_usd=0.000001)
    db.set_setting('editorial_profile', {'max_spend_usd': 3.5})

    class OfflineClient:
        responses = SimpleNamespace(
            input_tokens=SimpleNamespace(count=lambda **kwargs: SimpleNamespace(input_tokens=100)),
            create=lambda **kwargs: pytest.fail('Cycle money allowance was bypassed'))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(generation, 'client', OfflineClient)
    with pytest.raises(spending.SpendLimitExceeded, match='saldo financeiro'):
        coordinator.invoke_agent(cycle, 'business', {})
    assert spending.summary(budget.generation_job(cycle), persist=False)['limit_usd'] == 0.000001
    assert store.cycle_runs(cycle['id'])[0]['status'] == 'failed'
    # Attempt allowance differs from an actual paid send or a confirmed bill.
    assert store.get_cycle(cycle['id'])['calls'] == 1


def test_timeout_keeps_attempt_and_token_allowance_across_retry(client, monkeypatch):
    cycle = make_cycle(client)
    bound = budget.request_bound(business_request(cycle))
    cycle['budget']['max_tokens_estimate'] = bound
    store.save_cycle(cycle)
    dispatched = []

    def timeout(*args, **kwargs):
        dispatched.append(args[3])
        persisted = store.get_cycle(cycle['id'])
        assert persisted['calls'] == 1 and persisted['budget_state']['tokens_reserved'] == bound
        assert store.cycle_runs(cycle['id'])[0]['budget_attempt']['tokens_bound'] == bound
        raise TimeoutError('Simulação sem fornecedor')

    monkeypatch.setattr(generation, 'structured', timeout)
    with pytest.raises(TimeoutError):
        coordinator.invoke_agent(cycle, 'business', {})
    restored = store.get_cycle(cycle['id'])
    with pytest.raises(budget.StrategyBudgetExceeded):
        coordinator.invoke_agent(restored, 'business', {})
    assert dispatched == ['strategy_business']
    assert store.cycle_runs(cycle['id'])[0]['status'] == 'failed'
    assert store.get_cycle(cycle['id'])['budget_state']['tokens_reserved'] == bound


def test_stale_cycle_snapshot_cannot_renew_a_consumed_attempt(client, monkeypatch):
    cycle = make_cycle(client, max_agent_calls=1)
    stale = deepcopy(cycle)
    dispatched = []

    def timeout(*args, **kwargs):
        dispatched.append(args[3])
        raise TimeoutError('Offline')

    monkeypatch.setattr(generation, 'structured', timeout)
    with pytest.raises(TimeoutError):
        coordinator.invoke_agent(cycle, 'business', {})
    with pytest.raises(budget.StrategyBudgetExceeded, match='limite de chamadas'):
        coordinator.invoke_agent(stale, 'business', {})
    assert dispatched == ['strategy_business']
    assert store.get_cycle(cycle['id'])['calls'] == 1


def test_concurrent_attempts_cannot_spend_the_same_remaining_call(client, monkeypatch):
    cycle = make_cycle(client, max_agent_calls=1)
    provider_started = threading.Event()
    loser_finished = threading.Event()
    dispatched = []

    def provider(*args, **kwargs):
        dispatched.append(args[3])
        provider_started.set()
        assert loser_finished.wait(timeout=10), 'Competing attempt did not reach its guard'
        return successful_business()

    def attempt(snapshot):
        try:
            return coordinator.invoke_agent(snapshot, 'business', {})
        except budget.StrategyBudgetExceeded:
            loser_finished.set()
            return 'refused'

    monkeypatch.setattr(generation, 'structured', provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, deepcopy(cycle))
        assert provider_started.wait(timeout=10)
        second = pool.submit(attempt, deepcopy(cycle))
        outcomes = [first.result(timeout=15), second.result(timeout=15)]
    assert outcomes.count('refused') == 1 and dispatched == ['strategy_business']
    assert store.get_cycle(cycle['id'])['calls'] == 1
    assert len(store.cycle_runs(cycle['id'])) == 1


def test_cached_output_works_with_exhausted_allowance_and_separate_cache_telemetry(client, monkeypatch):
    cycle = make_cycle(client, max_agent_calls=1)
    monkeypatch.setattr(generation, 'structured', successful_business)
    expected, run_id = coordinator.invoke_agent(cycle, 'business', {})
    before = deepcopy(cycle['budget_state'])
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Cache contacted provider'))
    with cost_observability.run(budget.generation_job(cycle), scope='strategy_cycle', operation='retry'):
        actual, cached_id = coordinator.invoke_agent(cycle, 'business', {})
    assert actual == expected and cached_id == run_id
    assert store.get_cycle(cycle['id'])['budget_state'] == before
    event = observe_rows('cost_events', cycle)[0]
    assert event['scope'] == 'strategy_cycle' and event['origin'] == 'application_cache'
    assert 'cached_input_tokens' not in event
    assert event['calculated_usd'] == 0 and event['infrastructure_usd'] is None


def test_synthesis_falls_back_locally_at_token_limit_without_new_attempt(client, monkeypatch):
    cycle = make_cycle(client, max_tokens_estimate=1)
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Exceeded budget contacted provider'))
    plan = coordinator.synthesise(cycle, {})
    assert 'agregação determinística' in plan['summary']
    assert store.get_cycle(cycle['id'])['calls'] == 0
    assert not store.cycle_runs(cycle['id'])


def test_research_tools_are_unavailable_even_with_declared_allowance(client, monkeypatch):
    cycle = make_cycle(client, max_research_queries=10, max_video_lookups=20)
    original = agents.prepare_execution

    def with_unsupported_tool(*args, **kwargs):
        request, *rest = original(*args, **kwargs)
        return {**request, 'tools': [{'type': 'web_search'}], 'max_tool_calls': 1}, *rest

    monkeypatch.setattr(agents, 'prepare_execution', with_unsupported_tool)
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Unsupported tool contacted provider'))
    with pytest.raises(budget.StrategyBudgetExceeded, match='Nenhuma ferramenta foi chamada'):
        coordinator.invoke_agent(cycle, 'business', {})
    report = store.cycle_report(cycle['id'])['budget_status']
    assert report['research_queries'] == {'available': False, 'limit': 10, 'used': 0}
    assert report['video_lookups'] == {'available': False, 'limit': 20, 'used': 0}
    assert store.get_cycle(cycle['id'])['calls'] == 0


def test_legacy_unknown_attempts_preserve_history_without_fresh_allowance(client, monkeypatch):
    cycle = make_cycle(client)
    cycle.pop('budget_state')
    cycle['calls'] = 1
    cycle['usage'] = [{'stage': 'strategy_business', 'input_tokens': None, 'output_tokens': None}]
    # A pre-migration row, rather than asking the current writer to erase its
    # durable counters (which intentionally cannot renew an allowance).
    with db.connect() as c:
        c.execute('UPDATE strategy_cycles SET data=? WHERE id=?', (json.dumps(cycle), cycle['id']))
    before = deepcopy(cycle['usage'])
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Legacy unknown history contacted provider'))
    with pytest.raises(budget.StrategyBudgetExceeded, match='Inicie um novo ciclo explicitamente'):
        coordinator.invoke_agent(cycle, 'business', {})
    saved = store.get_cycle(cycle['id'])
    assert saved['calls'] == 1 and saved['usage'] == before and 'budget_state' not in saved
    report = store.cycle_report(cycle['id'])['budget_status']
    assert report['tokens_reserved'] is None and not report['tracking_known']


def test_stale_post_response_or_cache_save_cannot_reset_durable_allowance(client, monkeypatch):
    cycle = make_cycle(client, max_agent_calls=1)
    stale = deepcopy(cycle)
    monkeypatch.setattr(generation, 'structured', successful_business)
    coordinator.invoke_agent(cycle, 'business', {})
    consumed = deepcopy(cycle['budget_state'])
    stale['events'].append({'time': db.now(), 'message': 'Stale UI/worker state'})
    store.save_cycle(stale)
    persisted = store.get_cycle(cycle['id'])
    assert persisted['calls'] == 1 and persisted['budget_state'] == consumed


def test_readonly_legacy_budget_projection_preserves_unvalidated_configuration(client):
    cycle = make_cycle(client)
    old = {'max_agent_calls': 1000, 'max_tokens_estimate': 'unknown'}
    with db.connect() as c:
        cycle['budget'] = old
        c.execute('UPDATE strategy_cycles SET data=? WHERE id=?', (json.dumps(cycle), cycle['id']))
    report = store.cycle_report(cycle['id'])
    assert report['budget_status']['limits'] == old
    assert not report['budget_status']['limits_valid']
    assert store.get_cycle(cycle['id'])['budget'] == old


def test_invalid_legacy_configuration_fails_recoverably_inside_observed_run(client, monkeypatch):
    cycle = make_cycle(client)
    cycle['budget'] = {'max_tokens_estimate': 'unknown'}
    with db.connect() as c:
        c.execute('UPDATE strategy_cycles SET data=? WHERE id=?', (json.dumps(cycle), cycle['id']))
    monkeypatch.setattr(engine, 'get_secret', lambda _: 'offline-key')
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Invalid budget contacted provider'))
    engine.run(cycle['id'])
    saved = store.get_cycle(cycle['id'])
    assert saved['status'] == 'failed' and saved['error']
    assert observe_rows('cost_runs', cycle)[0]['metadata']['cycle_status'] == 'failed'
    assert observe_rows('cost_runs', cycle)[0]['outcome'] == 'failed'


def test_resuming_compatible_checkpoint_reuses_source_run_and_accounts_remaining_attempts(client, monkeypatch):
    cycle = make_cycle(client)
    monkeypatch.setattr(generation, 'structured', successful_business)
    _, first_run = coordinator.invoke_agent(cycle, 'business', {})
    cycle['status'] = 'failed'
    store.save_cycle(cycle)
    calls = []

    def complete_remaining(current, schema, instruction, stage, extra=None, **kwargs):
        calls.append(stage)
        return {'summary': 'Offline', **({'opportunities': []} if stage == 'strategy_coordinator' else {})}

    monkeypatch.setattr(generation, 'structured', complete_remaining)
    monkeypatch.setattr(engine, 'get_secret', lambda _: 'offline-key')
    monkeypatch.setattr(engine.executor, 'submit', lambda fn, cycle_id: fn(cycle_id))
    engine.resume(cycle['id'])
    saved = store.get_cycle(cycle['id'])
    assert saved['status'] == 'ready' and saved['completed']['business'] == first_run
    assert saved['calls'] == 9 and 'strategy_business' not in calls and len(calls) == 8
    cache = observe_rows('cost_events', cycle)[0]
    assert cache['metadata']['reason'] == 'compatible_checkpoint'
    financial = observe_rows('cost_runs', cycle)[0]
    assert financial['scope'] == 'strategy_cycle' and financial['pipeline_version'] == 'strategy-agents-2'
    assert financial['metadata']['cycle_status'] == 'ready'
    assert financial['outcome'] == 'ready'


def test_process_cancellation_preserves_reservation_before_recovery(client, monkeypatch):
    cycle = make_cycle(client, max_agent_calls=1)
    monkeypatch.setattr(engine, 'get_secret', lambda _: 'offline-key')
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        engine.run(cycle['id'])
    interrupted = store.get_cycle(cycle['id'])
    assert interrupted['calls'] == 1 and interrupted['budget_state']['tokens_reserved'] > 0
    assert observe_rows('cost_runs', cycle)[0]['status'] == 'cancelled'
    engine.recover()
    assert store.get_cycle(cycle['id'])['status'] == 'interrupted'
    monkeypatch.setattr(generation, 'structured', lambda *args, **kwargs: pytest.fail('Cancellation renewed the budget'))
    engine.run(cycle['id'])
    assert store.get_cycle(cycle['id'])['status'] == 'failed'
    assert store.get_cycle(cycle['id'])['calls'] == 1
