from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app import db, spending


def request(**overrides):
    return {'model': 'gpt-4.1-mini', 'input': '\u00e1\u6f22\U0001f642',
            'instructions': 'Complete the request.', 'max_output_tokens': 1000,
            'text': {'format': {'type': 'json_schema', 'name': 'result',
                               'schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}}}}},
            **overrides}


def api_for(count=None):
    api = Mock()
    if count is None:
        api.responses.input_tokens.count.side_effect = TimeoutError()
    else:
        api.responses.input_tokens.count.return_value = NS(input_tokens=count)
    api.responses.create.return_value = NS(id='bound-response', usage=None, output=[])
    return api


@pytest.fixture
def financial_job(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    db.init()
    value = {'id': 'request-bound-job', 'status': 'ready', 'created_at': db.now(),
             'financial_budget_usd': 100, 'usage': []}
    db.save_job(value)
    return value


def test_offline_bound_includes_utf8_schema_and_maximum_output_without_io(monkeypatch):
    monkeypatch.setattr(db, 'connect', Mock(side_effect=AssertionError('Offline estimate touched persistence.')))
    monkeypatch.setattr(spending, 'reserve', Mock(side_effect=AssertionError('Offline estimate reserved money.')))
    original = request()
    saved = deepcopy(original)
    # 227 UTF-8 bytes plus 4096 framing tokens, 1000 maximum output tokens,
    # registered mini rates, and the existing 15% safety margin.
    assert spending.text_request_bound(original) == Decimal('0.003829')
    assert spending.text_request_bound(request(max_output_tokens=2000)) == Decimal('0.005669')
    assert spending.text_request_bound(request(metadata={'ignored': 'x' * 20000})) == Decimal('0.003829')
    assert original == saved


@pytest.mark.parametrize('model,tools', [('gpt-4.1-mini', 0), ('gpt-4.1-mini', 2),
                                       ('gpt-4o', 2), ('gpt-4.1-mini-2025-04-14', 0)])
def test_offline_bound_matches_actual_fallback_reservation(financial_job, model, tools):
    wire = request(model=model)
    if tools:
        wire.update(tools=[{'type': 'web_search'}], tool_choice='required', max_tool_calls=tools)
    api = api_for()
    _, record = spending.create_response(financial_job, api, wire, 'writer')
    assert Decimal(str(record['reserved_usd'])) == spending.amount(spending.text_request_bound(wire))
    assert record['output_bound'] == wire['max_output_tokens']
    assert record['tools_bound'] == tools
    api.responses.create.assert_called_once()


@pytest.mark.parametrize('count', [None, 0, -1, True, 1.5])
def test_unusable_counter_preserves_offline_bound(financial_job, count):
    _, record = spending.create_response(financial_job, api_for(count), request(), 'writer')
    assert record['input_bound'] == 4323
    assert Decimal(str(record['reserved_usd'])) == spending.amount(spending.text_request_bound(request()))


def test_provider_count_keeps_existing_lower_reservation(financial_job):
    _, record = spending.create_response(financial_job, api_for(100), request(), 'writer')
    assert record['input_bound'] == 356
    assert record['reserved_usd'] == float(spending.amount(Decimal('0.002003760')))
    assert record['reserved_usd'] < spending.text_request_bound(request())


@pytest.mark.parametrize('tools', [None, 0, -1, 33, True, 1.5])
def test_offline_bound_rejects_unbounded_tools(tools):
    with pytest.raises(spending.SpendLimitExceeded):
        spending.text_request_bound(request(tools=[{'type': 'web_search'}], max_tool_calls=tools))


def test_offline_bound_rejects_unknown_tariff():
    with pytest.raises(spending.SpendLimitExceeded):
        spending.text_request_bound(request(model='unknown-model'))


def test_monetary_hold_uses_stronger_of_existing_floor_and_exact_review_bound(financial_job):
    financial_job['financial_budget_usd'] = 1
    spending.reserve(financial_job, .3, 'gpt-4.1-mini', 'writer', downstream=3, downstream_usd=.7)
    assert spending.summary(financial_job)['reserved_usd'] == .3
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(financial_job, .64, 'gpt-4.1-mini', 'writer', downstream=3, downstream_usd=.01)


def test_monetary_hold_reads_current_balance_despite_stale_snapshot(financial_job):
    financial_job['financial_budget_usd'] = 1
    stale = deepcopy(financial_job)
    spending.reserve(financial_job, .3, 'gpt-4.1-mini', 'other-attempt')
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(stale, .3, 'gpt-4.1-mini', 'writer', downstream_usd=.5)
    assert spending.summary(financial_job)['reserved_usd'] == .3


@pytest.mark.parametrize('hold', [-1, Decimal('-0.000000001'), float('nan'), float('inf'),
                                 float('-inf'), None, True, 'invalid'])
def test_invalid_monetary_hold_creates_no_reservation(financial_job, hold):
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(financial_job, .01, 'gpt-4.1-mini', 'writer', downstream_usd=hold)
    assert spending.summary(financial_job)['reserved_usd'] == 0


def test_monetary_hold_is_forwarded_and_refuses_before_paid_dispatch(financial_job):
    financial_job['financial_budget_usd'] = 1
    spending.reserve(financial_job, .97, 'gpt-4.1-mini', 'earlier')
    api = api_for(100)
    with pytest.raises(spending.SpendLimitExceeded):
        spending.create_response(financial_job, api, request(), 'writer', downstream_usd=.03)
    api.responses.create.assert_not_called()
    assert spending.summary(financial_job)['reserved_usd'] == .97
