from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError, InternalServerError

from app import db, spending


def api_for(*, incoming=100, outgoing=100, cached=0, tools=0, **changes):
    result = NS(id='resp_money', output=[NS(type='web_search_call') for _ in range(tools)],
                usage=NS(input_tokens=incoming, output_tokens=outgoing,
                         input_tokens_details=NS(cached_tokens=cached)))
    result.__dict__.update(changes)
    api = Mock()
    api.responses.input_tokens.count.return_value = NS(input_tokens=100)
    api.responses.create.return_value = result
    return api


def request(**overrides):
    return {'model': 'gpt-4.1-mini', 'input': 'Um texto.', 'max_output_tokens': 1000, **overrides}


def test_default_limit_hard_cap_and_profile_cannot_raise_above_one(job):
    assert spending.summary(job)['limit_usd'] == 1
    db.set_setting('editorial_profile', {'max_spend_usd': 10})
    assert spending.summary(job)['limit_usd'] == 1
    db.set_setting('editorial_profile', {'max_spend_usd': .12})
    assert spending.summary(job)['limit_usd'] == .12


def test_money_refusal_happens_before_paid_request(job):
    spending.reserve(job, .9999, 'gpt-4.1-mini', 'previous')
    api = api_for()
    with pytest.raises(spending.SpendLimitExceeded):
        spending.create_response(job, api, request(), 'writing')
    api.responses.create.assert_not_called()


def test_concurrent_stale_snapshots_cannot_oversubscribe(job):
    def attempt(_):
        try:
            return spending.reserve(deepcopy(job), .6, 'gpt-4o', 'writing')
        except spending.SpendLimitExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(bool(r) for r in results) == 1
    assert spending.summary(job)['reserved_usd'] == .6


def test_settlement_cache_usage_and_telemetry_deduplicate(job):
    api = api_for(incoming=10000, outgoing=1000, cached=8000)
    result, record = spending.create_response(job, api, request(max_output_tokens=8000), 'writing')
    # A known cached input is billed at its cheaper rate, not twice.
    assert record['charged_usd'] == pytest.approx(.00368)
    job['usage'] = [{**spending.response_usage(result), 'model': 'gpt-4.1-mini',
                     'response_id': result.id, 'reservation_id': record['id']}]
    db.save_job(job)
    assert spending.summary(job)['spent_usd'] == pytest.approx(.00368)
    spending.finish(record['id'], spending.response_usage(result), response_id=result.id)
    assert spending.summary(job)['spent_usd'] == pytest.approx(.00368)
    assert spending.summary(job)['reserved_usd'] == 0


def test_historical_spend_survives_stale_job_overwrite_and_new_cycle(job):
    old = deepcopy(job)
    job['usage'] = [{'response_id': 'legacy', 'model': 'gpt-4o', 'stage': 'writer',
                     'input_tokens': 100000, 'output_tokens': 10000, 'web_search_calls': 0}]
    db.save_job(job)
    value = spending.summary(job)['spent_usd']
    assert value == pytest.approx(.4025)
    old['editorial'] = {'cycle_id': 'new-cycle', 'model': 'gpt-4.1-mini'}
    db.save_job(old)
    assert spending.summary(old)['spent_usd'] == value
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(old, .6, 'gpt-4.1-mini', 'writer')


def test_refused_reservation_still_commits_historical_bills(job):
    stale = deepcopy(job)
    job['usage'] = [{'response_id': 'old-bill', 'model': 'gpt-4o', 'estimated_usd': .9}]
    db.save_job(job)
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(job, .2, 'gpt-4o', 'writer')
    db.save_job(stale)
    assert spending.summary(stale)['spent_usd'] == .9


@pytest.mark.parametrize('status,released', [(400, True), (500, False)])
def test_definite_rejection_releases_but_ambiguous_provider_failure_keeps_reservation(job, status, released):
    api = api_for()
    raw = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
    error = BadRequestError if status == 400 else InternalServerError
    api.responses.create.side_effect = error('failure', response=raw, body={})
    with pytest.raises(error):
        spending.create_response(job, api, request(), 'writer')
    assert (spending.summary(db.get_job(job['id']))['reserved_usd'] == 0) is released


@pytest.mark.parametrize('usage', [None, NS(input_tokens=None, output_tokens=None),
                                   NS(input_tokens=0, output_tokens=0), NS(input_tokens=-1, output_tokens=3), Mock()])
def test_missing_usage_keeps_reservation_instead_of_freeing_paid_request(job, usage):
    api = api_for(usage=usage)
    _, record = spending.create_response(job, api, request(), 'writer')
    assert record['state'] == 'uncertain'
    assert spending.summary(job)['reserved_usd'] > 0


def test_text_image_and_audio_reservations_share_lifetime_cap(job):
    first = spending.reserve(job, .4, 'gpt-image-2', 'image_generation', image_task_id='image1')
    spending.finish(first, {'estimated_usd': .4, 'images': 1})
    spending.reserve(job, .4, 'whisper-1', 'audio_transcription')
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(job, .21, 'gpt-4o', 'writer')
    assert spending.summary(job)['spent_usd'] == .4
    assert spending.summary(job)['reserved_usd'] == .4


def test_unknown_model_refused_before_provider_and_search_cannot_spend_core_budget(job):
    api = api_for()
    unknown = request()
    unknown['model'] = 'unpriced-model'
    with pytest.raises(spending.SpendLimitExceeded):
        spending.create_response(job, api, unknown, 'writer')
    search = request()
    search.update(model='gpt-4o', tools=[{'type': 'web_search'}], max_tool_calls=2)
    with pytest.raises(spending.SpendLimitExceeded):
        spending.create_response(job, api, search, 'research', downstream=3)
    api.responses.create.assert_not_called()


def test_count_has_short_timeout_and_conservative_fallback(job):
    api = api_for()
    api.responses.input_tokens.count.side_effect = TimeoutError()
    _, record = spending.create_response(job, api, request(), 'writer')
    assert api.responses.input_tokens.count.call_args.kwargs['timeout'] == 5
    assert record['input_bound'] > 4096
    assert api.responses.create.call_args.kwargs['service_tier'] == 'default'
