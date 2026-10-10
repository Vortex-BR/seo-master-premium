"""An unchanged input must not hide changed model/prompt/schema dependencies."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import create_model

from app import generation
from app.strategy import agents, coordinator, engine, store


def cycle_fixture(client):
    store.init()
    cycle = engine.start('cache-fixture')
    cycle['project_context'] = {'project_id': 'cache-fixture', 'business': {'name': 'Horta'}}
    return cycle


def test_model_changes_strategy_fingerprint(client, monkeypatch):
    cycle = cycle_fixture(client)
    context = coordinator._build_context(cycle, 'business', {})
    monkeypatch.setattr(generation, 'model', lambda: 'model-a')
    first = agents.execution_fingerprint(cycle, 'business', context)
    monkeypatch.setattr(generation, 'model', lambda: 'model-b')
    assert agents.execution_fingerprint(cycle, 'business', context) != first


@pytest.mark.parametrize('dependency', ['prompt', 'schema', 'context_version', 'agents_version', 'config', 'context'])
def test_each_relevant_dependency_changes_strategy_fingerprint(client, monkeypatch, dependency):
    cycle = cycle_fixture(client)
    context = coordinator._build_context(cycle, 'business', {})
    first = agents.execution_fingerprint(cycle, 'business', context)
    scope_token = None
    if dependency == 'prompt':
        monkeypatch.setitem(agents.ROLES['business'], 'prompt', 'Prompt diferente')
    elif dependency == 'schema':
        replacement = create_model('ChangedBusiness', __base__=agents.ROLES['business']['schema'],
                                   extra_context=(str, ''))
        monkeypatch.setitem(agents.ROLES['business'], 'schema', replacement)
    elif dependency == 'context_version':
        monkeypatch.setattr(agents, 'CONTEXT_VERSION', 'changed-context')
    elif dependency == 'agents_version':
        monkeypatch.setattr(agents, 'VERSION', agents.VERSION + 1)
    elif dependency == 'config':
        scope_token = generation.agent_scope.set({'max_output_tokens': 1200})
    else:
        context['focus'] = 'Diferente'
    try:
        assert agents.execution_fingerprint(cycle, 'business', context) != first
    finally:
        if scope_token is not None:
            generation.agent_scope.reset(scope_token)


def test_identical_request_and_dictionary_order_have_same_identity(client):
    cycle = cycle_fixture(client)
    context = coordinator._build_context(cycle, 'business', {})
    assert agents.execution_identity(cycle, 'business', context) == agents.execution_identity(
        deepcopy(cycle), 'business', dict(reversed(list(context.items()))))


@pytest.mark.parametrize('field', ['id', 'project_id'])
def test_identical_input_from_another_cycle_or_project_cannot_reuse_checkpoint(client, field):
    cycle = cycle_fixture(client)
    context = coordinator._build_context(cycle, 'business', {})
    other = deepcopy(cycle)
    other[field] = 'another-scope'
    assert agents.execution_fingerprint(cycle, 'business', context) != agents.execution_fingerprint(
        other, 'business', context)


def test_changed_model_cannot_reuse_coordinator_cache(client, monkeypatch):
    cycle = cycle_fixture(client)
    calls = []
    monkeypatch.setattr(generation, 'model', lambda: 'model-a')
    def fake_structured(*args, **kwargs):
        calls.append(args[3])
        return {'summary': 'Resumo disponível.', 'business_topics': ['Horta']}
    monkeypatch.setattr(generation, 'structured', fake_structured)
    coordinator.invoke_agent(cycle, 'business', {})
    monkeypatch.setattr(generation, 'model', lambda: 'model-b')
    coordinator.invoke_agent(cycle, 'business', {})
    assert calls == ['strategy_business', 'strategy_business']


@pytest.mark.parametrize('change', ['legacy', 'model', 'prompt', 'schema', 'context', 'incomplete', 'invalid'])
def test_resume_incompatible_checkpoint_preserves_output_without_new_calls(client, monkeypatch, change):
    cycle = cycle_fixture(client)
    calls = []
    monkeypatch.setattr(generation, 'model', lambda: 'model-a')
    monkeypatch.setattr(engine, 'get_secret', lambda _: 'offline-key')
    def fake_structured(*args, **kwargs):
        calls.append(args[3])
        return {'summary': 'Resumo salvo.', 'business_topics': ['Horta']}
    monkeypatch.setattr(generation, 'structured', fake_structured)
    output, run_id = coordinator.invoke_agent(cycle, 'business', {})
    run = store.get_run(run_id)
    original_run = deepcopy(run)
    if change == 'legacy':
        run.pop('execution_identity')
        store.save_run(cycle, 'business', run['input_hash'], run, run_id, 'completed')
    elif change == 'model':
        monkeypatch.setattr(generation, 'model', lambda: 'model-b')
    elif change == 'prompt':
        monkeypatch.setitem(agents.ROLES['business'], 'prompt', 'Prompt alterado')
    elif change == 'schema':
        replacement = create_model('DifferentBusiness', __base__=agents.ROLES['business']['schema'],
                                   additional_fact=(str, ''))
        monkeypatch.setitem(agents.ROLES['business'], 'schema', replacement)
    elif change == 'context':
        cycle['focus'] = 'Foco diferente'
    elif change == 'incomplete':
        cycle['completed']['performance'] = 'missing-run'
    else:
        run['output'] = {'summary': 'Entrega incompleta'}
        store.save_run(cycle, 'business', run['input_hash'], run, run_id, 'completed')
    cycle['status'] = 'failed'
    store.save_cycle(cycle)
    monkeypatch.setattr(engine.executor, 'submit', lambda fn, arg: fn(arg))
    engine.resume(cycle['id'])
    recovered = store.get_cycle(cycle['id'])
    assert recovered['status'] == 'failed'
    assert 'novo ciclo explicitamente' in recovered['error']
    assert calls == ['strategy_business']
    assert recovered['calls'] == 1
    assert store.get_run(run_id)['output'] == run['output']
    if change not in ('invalid', 'legacy'):
        assert store.get_run(run_id) == original_run


def test_legacy_run_read_path_preserves_unknown_historical_fields(client):
    cycle = cycle_fixture(client)
    legacy = {'run_id': 'legacy-run', 'status': 'completed',
              'output': {'summary': 'Histórico', 'business_topics': ['Horta'],
                         'unknown_historical_field': {'kept': True}}}
    store.save_run(cycle, 'business', 'legacy-hash', legacy, 'legacy-run', 'completed')
    assert store.get_run('legacy-run') == legacy


@pytest.mark.parametrize('role', ['business', 'coordinator'])
def test_wire_cache_identity_and_usage_share_the_model_frozen_before_send(client, monkeypatch, role):
    """A settings change during transport cannot label B output as A or charge A as B."""
    cycle = cycle_fixture(client)
    selected = {'model': 'model-a'}
    sent = []
    monkeypatch.setattr(generation, 'model', lambda: selected['model'])

    class OfflineClient:
        def __enter__(self):
            # This occurs after fingerprinting but before the request is sent.
            selected['model'] = 'model-b'
            return self

        def __exit__(self, *args):
            return False

    def respond(job, api, request, stage, **kwargs):
        sent.append(deepcopy(request))
        return SimpleNamespace(id='offline-response', status='completed', usage=None,
                               incomplete_details=None, output=[]), None

    monkeypatch.setattr(generation, 'client', OfflineClient)
    monkeypatch.setattr(generation.spending, 'create_response', respond)
    output = {'summary': 'Entrega do modelo congelado', **(
        {'business_topics': ['Horta']} if role == 'business' else {'opportunities': []})}
    monkeypatch.setattr(generation, 'parse_structured_response', lambda response, schema: deepcopy(output))
    if role == 'business':
        first, run_id = coordinator.invoke_agent(cycle, role, {})
    else:
        first = coordinator.synthesise(cycle, {})
        run_id = store.cycle_runs(cycle['id'])[0]['run_id']
    assert sent[0]['model'] == 'model-a'
    assert store.get_run(run_id)['execution_identity']['model'] == 'model-a'
    assert cycle['usage'][0]['model'] == 'model-a'
    selected['model'] = 'model-a'
    second = (coordinator.invoke_agent(cycle, role, {})[0] if role == 'business'
              else coordinator.synthesise(cycle, {}))
    assert second == first and len(sent) == 1


def test_synthesis_attempt_budget_is_durable_before_dispatch_and_after_failure(client, monkeypatch):
    cycle = cycle_fixture(client)
    cycle['budget']['max_agent_calls'] = 1
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args[3])
        assert store.get_cycle(cycle['id'])['calls'] == 1
        raise ValueError('Indisponibilidade simulada, sem cobrança')

    monkeypatch.setattr(generation, 'structured', unavailable)
    coordinator.synthesise(cycle, {})
    restored = store.get_cycle(cycle['id'])
    assert restored['calls'] == 1
    coordinator.synthesise(restored, {})
    assert calls == ['strategy_coordinator']
