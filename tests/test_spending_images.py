from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError, InternalServerError

from app import db, image_generation, spending


def options(**changes):
    return {'model': 'gpt-image-2', 'prompt': 'Uma imagem editorial simples.', 'n': 1,
            'quality': 'high', 'size': '1536x512', 'output_format': 'png', **changes}


def image_api(usage='confirmed'):
    result = NS(data=[], _request_id='image-request')
    result.usage = (NS(input_tokens=100, output_tokens=200,
                       input_tokens_details=NS(text_tokens=20, image_tokens=80),
                       output_tokens_details=NS(text_tokens=0, image_tokens=200))
                    if usage == 'confirmed' else usage)
    api = Mock()
    api.images.generate.return_value = result
    api.post.return_value = result
    return api


@pytest.mark.parametrize('quality,tokens', [('low', 56), ('medium', 535), ('high', 2140)])
def test_custom_banner_quote_matches_official_image_two_calculator(quality, tokens):
    assert image_generation.image_output_tokens('gpt-image-2', quality, 1536, 512) == tokens
    dollars, metadata = image_generation.image_quote(options(quality=quality), [])
    assert metadata['output_bound'] == tokens
    assert metadata['estimate_multiplier'] == 2
    assert metadata['pricing_basis'] == 'conservative_estimate'
    raw = ((metadata['text_input_bound'] * Decimal('5') + tokens * Decimal('30')) / 1000000)
    assert dollars == raw * 2 * spending.SAFETY


def test_image_two_grid_ties_are_rounded_to_even():
    # 16 / (2048 / 704) = 5.5, so the official calculator chooses 6.
    assert image_generation.image_output_tokens('gpt-image-2', 'low', 2048, 704) == 83


def test_references_remain_remote_and_have_conservative_dimension_based_quote(job):
    references = [{'image_url': 'https://images.pexels.com/photos/1/example.jpg', 'width': 1600, 'height': 900}]
    quote, metadata = image_generation.image_quote(options(), references)
    assert metadata['image_input_bound'] > 6240
    assert quote > image_generation.image_quote(options(), [])[0]
    api = image_api()
    result, record = image_generation.paid_image(job, api, options(), references, 'image-one')
    assert result is api.post.return_value
    api.images.generate.assert_not_called()
    assert api.post.call_args.kwargs['body']['images'] == [{'image_url': references[0]['image_url']}]
    assert record['image_task_id'] == 'image-one'
    assert record['usage']['image_input_tokens'] == 80
    assert record['usage']['text_input_tokens'] == 20
    assert record['usage']['image_output_tokens'] == 200
    assert spending.amount(record['charged_usd']) == spending.historical_cost({**record['usage'], 'model': 'gpt-image-2'})
    assert spending.summary(job)['reserved_usd'] == 0


def test_image_request_cannot_spend_money_already_reserved_for_text(job):
    spending.reserve(job, .99, 'gpt-4.1-mini', 'writing')
    api = image_api()
    with pytest.raises(spending.SpendLimitExceeded):
        image_generation.paid_image(job, api, options(), [], 'image-one')
    api.images.generate.assert_not_called()
    api.post.assert_not_called()
    assert spending.summary(job)['reserved_usd'] == .99


def test_uncertain_image_charge_survives_restart_and_blocks_text_overspending(job):
    api = image_api()
    api.images.generate.side_effect = TimeoutError('connection lost')
    quote = spending.amount(image_generation.image_quote(options(), [])[0])
    with pytest.raises(TimeoutError):
        image_generation.paid_image(job, api, options(), [], 'image-uncertain')
    stale = deepcopy(job)
    stale['editorial'] = {'cycle_id': 'another-cycle'}
    db.save_job(stale)
    assert spending.summary(db.get_job(job['id']))['reserved_usd'] == float(quote)
    with pytest.raises(spending.SpendLimitExceeded):
        spending.reserve(stale, 1 - quote + Decimal('.001'), 'gpt-4.1-mini', 'writer')


@pytest.mark.parametrize('status,released', [(400, True), (500, False)])
def test_image_rejection_and_ambiguous_failure_have_different_financial_results(job, status, released):
    api = image_api()
    response = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/images/generations'))
    error = BadRequestError if status == 400 else InternalServerError
    api.images.generate.side_effect = error('provider failure', response=response, body={})
    with pytest.raises(error):
        image_generation.paid_image(job, api, options(), [], 'image-one')
    assert (spending.summary(job)['reserved_usd'] == 0) is released


@pytest.mark.parametrize('usage', [None, NS(input_tokens=None, output_tokens=None),
                                   NS(input_tokens=0, output_tokens=0), Mock()])
def test_missing_or_invalid_image_usage_does_not_release_possible_charge(job, usage):
    api = image_api(usage=usage)
    _, record = image_generation.paid_image(job, api, options(), [], 'image-one')
    assert record['state'] == 'uncertain'
    assert spending.summary(job)['reserved_usd'] > 0


def test_unknown_image_model_and_unpriced_reference_dimensions_fail_before_request(job):
    api = image_api()
    with pytest.raises(spending.SpendLimitExceeded):
        image_generation.paid_image(job, api, options(model='unknown-image-model'), [], 'image-one')
    with pytest.raises(spending.SpendLimitExceeded):
        image_generation.paid_image(job, api, options(), [{'image_url': 'https://example.com/image.jpg'}], 'image-two')
    api.images.generate.assert_not_called()
    api.post.assert_not_called()
    assert spending.summary(job)['reserved_usd'] == 0


def test_image_telemetry_does_not_duplicate_settled_spend(job):
    api = image_api()
    _, record = image_generation.paid_image(job, api, options(), [], 'image-one')
    before = spending.summary(job)['spent_usd']
    job['usage'].append({'model': 'gpt-image-2', 'stage': 'image_generation', 'images': 1,
                         'image_task_id': 'image-one', 'input_tokens': 100, 'output_tokens': 200})
    db.save_job(job)
    assert spending.summary(job)['spent_usd'] == before == record['charged_usd']
