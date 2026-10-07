import base64
import io
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from openai import OpenAI
from PIL import Image, ImageOps

from app import db, image_generation, image_references, media
from app.schemas import ImageGeneration
from app.security import get_secret


def stock_rows():
    return {
        'photos': [{'id': 11, 'width': 1600, 'height': 900, 'photographer': 'Ana',
                    'url': 'https://www.pexels.com/photo/seedling-11/', 'alt': 'Broto na luz natural',
                    'src': {'large': 'https://images.pexels.com/photos/11/large.jpg',
                            'medium': 'https://images.pexels.com/photos/11/medium.jpg'}}],
        'hits': [{'id': 22, 'imageWidth': 1600, 'imageHeight': 900, 'user': 'Carlos',
                  'pageURL': 'https://pixabay.com/photos/seedling-22/', 'tags': 'seedling, plant',
                  'webformatURL': 'https://pixabay.com/get/seedling_640.jpg',
                  'previewURL': 'https://cdn.pixabay.com/photo/seedling_150.jpg'}],
    }


@pytest.fixture
def stock_http(client, monkeypatch):
    requests = []
    rows = stock_rows()

    def handler(request):
        requests.append(request)
        if request.url.host == 'api.pexels.com':
            assert request.headers['Authorization'] == 'stock-key'
            assert request.url.params['orientation'] == 'landscape'
            return httpx.Response(200, json={'photos': rows['photos']})
        assert request.url.host == 'pixabay.com' and request.url.path == '/api/'
        assert request.url.params['key'] == 'stock-key'
        assert request.url.params['safesearch'] == 'true'
        return httpx.Response(200, json={'hits': rows['hits']})

    original_client = httpx.Client
    monkeypatch.setattr(image_references.httpx, 'Client',
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(image_references, 'get_secret', lambda name: 'stock-key')
    return requests, rows


def test_both_banks_are_visual_urls_with_authorship_and_no_download(stock_http):
    requests, _ = stock_http
    result = image_references.search('broto')
    assert [r['provider'] for r in result['items']] == ['pexels', 'pixabay']
    assert result['items'][0]['author'] == 'Ana'
    assert result['items'][1]['image_url'].endswith('_640.jpg')
    assert not result['warnings']
    assert len(requests) == 2  # Only JSON search endpoints, never image downloads.
    assert not (db.data_dir() / 'media').exists()


def test_search_cache_reuses_metadata_for_24_hours_and_refreshes_expired(stock_http):
    requests, _ = stock_http
    image_references.search('Broto')
    image_references.search('broto')
    assert len(requests) == 2
    with db.connect() as connection:
        connection.execute('UPDATE image_reference_cache SET expires=0')
    image_references.search('broto')
    assert len(requests) == 4


@pytest.mark.parametrize('url', ['http://images.pexels.com/a.jpg', 'https://images.pexels.com.evil.test/a.jpg',
                                'https://images.pexels.com@localhost/a.jpg', 'https://127.0.0.1/a.jpg',
                                'https://images.pexels.com:444/a.jpg', 'data:image/png;base64,xxx'])
def test_provider_results_reject_untrusted_image_urls(url):
    row = stock_rows()['photos'][0]
    row['src']['large'] = url
    assert image_references.normalize('pexels', [row]) == []


def test_one_bank_failure_preserves_other_results_without_exposing_keys(stock_http, monkeypatch, caplog):
    original = image_references.search_provider

    def partly_available(provider, query, key):
        if provider == 'pixabay':
            raise httpx.HTTPStatusError('private stock-key payload',
                request=httpx.Request('GET', 'https://pixabay.com/api/?key=stock-key'),
                response=httpx.Response(429))
        return original(provider, query, key)

    monkeypatch.setattr(image_references, 'search_provider', partly_available)
    result = image_references.search('broto')
    assert len(result['items']) == 1
    assert 'Pixabay' in result['warnings'][0]
    assert 'stock-key' not in str(result) and 'stock-key' not in caplog.text


def test_stock_credentials_are_encrypted_and_blank_keeps_them(authed):
    result = authed.put('/api/settings', json={'pexels_api_key': 'pexels-private',
                                             'pixabay_api_key': 'pixabay-private'})
    assert result.status_code == 200
    assert result.json()['pexels_api_key_configured'] and result.json()['pixabay_api_key_configured']
    assert 'private' not in result.text
    for provider in ('pexels', 'pixabay'):
        assert get_secret(provider + '_api_key') == provider + '-private'
        assert 'private' not in db.get_setting(provider + '_api_key')
    authed.put('/api/settings', json={'pexels_api_key': '', 'pixabay_api_key': None})
    assert get_secret('pexels_api_key') == 'pexels-private'
    assert get_secret('pixabay_api_key') == 'pixabay-private'


def test_reference_search_is_private_and_saves_latest_job_without_generating(authed, job, stock_http, monkeypatch):
    api_constructor = Mock()
    monkeypatch.setattr(image_generation, 'OpenAI', api_constructor)
    endpoint = f'/api/jobs/{job["id"]}/images/references'
    result = authed.post(endpoint, json={'query': 'broto'})
    assert result.status_code == 200 and len(result.json()['items']) == 2
    assert db.get_job(job['id'])['image_reference_results']['items'] == result.json()['items']
    assert not api_constructor.called
    assert 'https://images.pexels.com' in result.headers['content-security-policy']
    authed.post('/api/logout')
    assert authed.post(endpoint, json={'query': 'broto'}).status_code == 401


@pytest.mark.parametrize('ids,expired', [([], False), (['pexels:999'], False),
                                      (['pexels:11', 'pexels:11'], False), (['pexels:11'], True)])
def test_invalid_or_expired_selection_is_rejected_before_paid_work(job, ids, expired):
    job['image_reference_results'] = {'items': image_references.normalize('pexels', stock_rows()['photos']),
                                     'expires_at': 0 if expired else time.time() + 3600}
    with pytest.raises(ValueError):
        image_references.selected(job, ids)


def test_selected_visual_urls_reach_image_endpoint_once_and_deliver_banner(job, monkeypatch):
    references = image_references.normalize('pexels', stock_rows()['photos'])
    job['image_reference_results'] = {'items': references, 'expires_at': time.time() + 3600}
    monkeypatch.setattr(image_generation.executor, 'submit', Mock())
    monkeypatch.setattr(image_generation, 'get_secret', lambda _: 'openai-test')
    task = image_generation.submit(job, ImageGeneration(request_id='a' * 32,
               reference_mode='selected', reference_ids=['pexels:11'], size='1024x1024'))
    png = io.BytesIO()
    Image.new('RGB', (1536, 512), '#4d9051').save(png, 'PNG')
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png.getvalue()).decode())],
                               usage=None)
    api = MagicMock()
    api.__enter__.return_value = api
    api.post.return_value = response
    constructor = Mock(return_value=api)
    monkeypatch.setattr(image_generation, 'OpenAI', constructor)
    image_generation.run(job['id'], task['id'])
    image_generation.run(job['id'], task['id'])
    api.images.generate.assert_not_called()
    assert api.post.call_count == 1 and api.post.call_args.args == ('/images/edits',)
    body = api.post.call_args.kwargs['body']
    assert body['images'] == [{'image_url': references[0]['image_url']}]
    assert body['size'] == '1536x512' and body['output_format'] == 'png'
    assert '375 px' in body['prompt'] and '40%' in body['prompt']
    assert constructor.call_args.kwargs['max_retries'] == 0
    saved = db.get_job(job['id'])
    image = saved['media'][0]
    assert saved['image_tasks'][0]['status'] == 'completed'
    assert (image['width'], image['height']) == (1280, 420)
    assert image['references'] == references and image['compression'] == 'lossless'
    assert saved['usage'][-1]['reference_count'] == 1
    with Image.open(media.path(saved, image)) as decoded:
        assert decoded.format == 'WEBP' and decoded.size == (1280, 420)


def test_automatic_missing_references_stop_before_openai(job, monkeypatch):
    monkeypatch.setattr(image_generation.executor, 'submit', Mock())
    monkeypatch.setattr(image_generation, 'get_secret', lambda _: 'test-key')
    monkeypatch.setattr(image_references, 'configured', lambda: ['pexels'])
    monkeypatch.setattr(image_references, 'search', lambda _: {'items': [], 'warnings': ['Unavailable']})
    constructor = Mock()
    monkeypatch.setattr(image_generation, 'OpenAI', constructor)
    task = image_generation.submit(job, ImageGeneration(request_id='a' * 32))
    image_generation.run(job['id'], task['id'])
    assert not constructor.called
    assert db.get_job(job['id'])['image_tasks'][0]['status'] == 'error'


def test_lossless_banner_preserves_resized_pixels_and_central_crop():
    source = Image.new('RGB', (1280, 800), '#ae2121')
    source.paste('#2177ae', (0, 190, 1280, 610))
    source.paste('#27ae54', (500, 300, 780, 500))
    original = io.BytesIO()
    source.save(original, 'PNG')
    encoded, width, height = media.prepare(original.getvalue(), banner=True)
    expected = ImageOps.fit(source, (1280, 420), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    with Image.open(io.BytesIO(encoded)) as decoded:
        assert (width, height) == (1280, 420)
        assert decoded.convert('RGB').tobytes() == expected.tobytes()
        assert decoded.getpixel((640, 210)) == (39, 174, 84)
        assert decoded.getpixel((0, 0)) == (33, 119, 174)


def test_canvas_stays_compatible_with_older_image_models():
    assert image_generation.generation_size('gpt-image-2') == '1536x512'
    assert image_generation.generation_size('gpt-image-2-2026-04-21') == '1536x512'
    assert image_generation.generation_size('gpt-image-1.5') == '1536x1024'


def test_real_sdk_sends_json_reference_urls_and_records_usage(job, monkeypatch):
    import json
    requests = []
    refs = image_references.normalize('pexels', stock_rows()['photos'])
    lookup = {'items': refs, 'warnings': [], 'query': 'broto'}
    monkeypatch.setattr(image_generation.executor, 'submit', Mock())
    monkeypatch.setattr(image_generation, 'get_secret', lambda _: 'sdk-test-key')
    monkeypatch.setattr(image_references, 'search', lambda _: lookup)
    png = io.BytesIO()
    Image.new('RGB', (1536, 512), '#4d9051').save(png, 'PNG')

    def handler(request):
        requests.append(request)
        assert request.url.path == '/v1/images/edits'
        assert request.headers['content-type'] == 'application/json'
        assert request.headers['authorization'] == 'Bearer sdk-test-key'
        payload = json.loads(request.content)
        assert payload['images'] == [{'image_url': refs[0]['image_url']}]
        assert payload['model'] == 'gpt-image-2'
        return httpx.Response(200, headers={'x-request-id': 'sdk-request-1'}, json={
            'created': 123, 'data': [{'b64_json': base64.b64encode(png.getvalue()).decode()}],
            'usage': {'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300,
                      'input_tokens_details': {'image_tokens': 80, 'text_tokens': 20}}})

    monkeypatch.setattr(image_generation, 'OpenAI', lambda **kwargs:
                        OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    task = image_generation.submit(job, ImageGeneration(request_id='b' * 32))
    image_generation.run(job['id'], task['id'])
    assert len(requests) == 1
    saved = db.get_job(job['id'])
    assert saved['image_tasks'][0]['status'] == 'completed'
    assert saved['image_tasks'][0]['references'] == refs
    assert saved['image_tasks'][0]['reference_query'] == 'broto'
    assert saved['usage'][-1]['input_tokens'] == 100
    assert saved['usage'][-1]['request_id'] == 'sdk-request-1'
