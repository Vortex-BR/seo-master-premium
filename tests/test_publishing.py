import base64
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
import pytest
from PIL import Image

from app import db, generation, image_generation, media, pipeline, publishing, wordpress
from app.schemas import ImageGeneration


def picture():
    output = io.BytesIO()
    Image.new('RGB', (640, 480), '#385e2e').save(output, 'PNG')
    return output.getvalue()


@pytest.fixture
def asset(job):
    return media.add(job, picture(), alt='Folhas verdes sobre uma mesa', caption='Observação das folhas',
                     credit='Imagem gerada com IA', origin='ai', featured=True)


def test_render_image_with_alt_caption_position_and_safe_markup(job, asset):
    asset.update(position=media.headings(job)[0]['id'], alt='Folhas "verdes" <script>', caption='<img onerror=alert(1)>')
    html = publishing.render(job)
    assert html.index('</h2>') < html.index('<figure') < html.index('O autor observa')
    assert 'alt="Folhas &quot;verdes&quot; &lt;script&gt;"' in html
    assert '<figcaption' in html and '&lt;img onerror' in html
    assert 'loading="lazy"' in html and 'width="1280"' in html and 'height="420"' in html
    assert '<script>' not in html
    assert job['article']['markdown'] not in html


def test_position_survives_paragraph_edits_and_falls_back_if_heading_changes(job, asset):
    key = media.headings(job)[0]['id']
    asset['position'] = key
    job['article']['markdown'] = 'Um novo parágrafo.\n\n' + job['article']['markdown']
    assert media.headings(job)[0]['id'] == key
    job['article']['markdown'] = job['article']['markdown'].replace('Observação da horta', 'Novas observações')
    assert media.public_item(job, asset)['position_missing']
    assert publishing.render(job).rfind('<figure') > publishing.render(job).rfind('experiência pessoal.')


def test_quoted_headings_do_not_shift_image_positions(job, asset):
    asset['position'] = media.headings(job)[0]['id']
    job['article']['markdown'] = '> ## Título dentro de citação\n\n' + job['article']['markdown']
    assert len(media.headings(job)) == 1
    rendered = publishing.render(job)
    assert rendered.index('</blockquote>') < rendered.index('Observação da horta') < rendered.index('<figure')


def test_wxr_is_native_draft_with_yoast_tags_and_remote_attachments(job, asset):
    job['article']['title'] = 'Folhas & imagens ]]> teste'
    xml = publishing.wxr(job, 'https://studio.example')
    ns = {'wp': 'http://wordpress.org/export/1.2/', 'c': 'http://purl.org/rss/1.0/modules/content/'}
    root = ET.fromstring(xml)
    assert root.findtext('channel/wp:wxr_version', namespaces=ns) == '1.2'
    attachment, post = root.findall('channel/item')
    assert post.findtext('title') == job['article']['title']
    assert post.findtext('wp:status', namespaces=ns) == 'pending'
    assert post.findtext('wp:post_type', namespaces=ns) == 'post'
    metadata = {m.findtext('wp:meta_key', namespaces=ns): m.findtext('wp:meta_value', namespaces=ns)
                for m in post.findall('wp:postmeta', ns)}
    assert metadata['_yoast_wpseo_title'] == job['article']['seo_title']
    assert metadata['_yoast_wpseo_metadesc'] == job['article']['meta_description']
    assert metadata['_yoast_wpseo_focuskw'] == job['brief']['keyword']
    assert metadata['_thumbnail_id'] == attachment.findtext('wp:post_id', namespaces=ns)
    content = post.findtext('c:encoded', namespaces=ns)
    assert '<!-- wp:image' in content and '<!-- wp:heading' in content
    assert '<html' not in content and '<h1' not in content and '/api/jobs/' not in content
    url = attachment.findtext('wp:attachment_url', namespaces=ns)
    assert url in content and '"id":10001' not in content
    assert post.find('category').attrib['domain'] == 'post_tag'


def test_gutenberg_export_uses_native_headings_and_lists(job):
    job['article']['markdown'] = ('## Escolhas iniciais\n\n- Primeiro item\n- Segundo item\n\n'
                                '### Próximos passos\n\n1. Uma ação\n2. Outra ação')
    rendered = publishing.render(job, gutenberg=True)
    assert '<!-- wp:heading -->' in rendered
    assert '<!-- wp:heading {"level":3} -->' in rendered
    assert '<!-- wp:list -->' in rendered and '<ul>' in rendered
    assert '<!-- wp:list {"ordered":true} -->' in rendered and '<ol>' in rendered
    assert '<!-- wp:html -->' not in rendered


def test_private_images_signed_import_expiry_and_revocation(authed, job, asset):
    private = f'/api/jobs/{job["id"]}/images/{asset["id"]}/file'
    assert authed.get(private).headers['content-type'] == 'image/webp'
    public = urlsplit(media.import_url(job, asset, 'https://studio.example')).path
    expired = urlsplit(media.import_url(job, asset, 'https://studio.example', int(time.time()) - 1)).path
    authed.cookies.clear()
    assert authed.get(private).status_code == 401
    assert authed.get(public).status_code == 200
    assert authed.get(expired).status_code == 404
    assert authed.get(public.replace(asset['id'], 'a' * 32)).status_code == 404
    assert authed.get(public).headers['x-robots-tag'] == 'noindex, nofollow'
    authed.post('/api/login', json={'password': 'test-password-long-enough'})
    assert authed.delete(f'/api/jobs/{job["id"]}/images/{asset["id"]}').status_code == 200
    assert authed.get(public).status_code == 404


def test_exports_include_images_without_private_links_and_reject_unknown_format(authed, job, asset):
    html = authed.get('/api/jobs/test-job/export?format=html').text
    assert 'data:image/webp;base64,' in html and '/api/jobs/' not in html
    assert authed.get('/api/jobs/test-job/export?format=wordpress').status_code == 200
    assert authed.get('/api/jobs/test-job/export?format=wordpress-html').status_code == 400
    assert authed.get('/api/jobs/test-job/export?format=unknown').status_code == 400
    assert authed.get('/api/jobs/test-job/export?format=json').json()['images'][0]['alt'] == asset['alt']


def test_missing_image_file_returns_technical_export_errors_but_saved_text_remains_available(authed, job, asset):
    media.path(job, asset).unlink()
    for format in ('html', 'wordpress'):
        response = authed.get('/api/jobs/test-job/export', params={'format': format})
        assert response.status_code == 400
        assert 'imagem' in response.json()['detail'] and 'indisponível' in response.json()['detail']
    assert authed.get('/api/jobs/test-job/export?format=markdown').status_code == 200


def test_edit_and_single_featured_image_preserve_text_review(authed, job, asset):
    second = media.add(job, picture())
    old_hash = job['review']['article_hash']
    url = f'/api/jobs/{job["id"]}/images/{second["id"]}'
    assert authed.put(url, json={'alt': 'Mesa com folhas', 'position': 'end', 'featured': True}).status_code == 200
    saved = db.get_job(job['id'])
    assert [m['id'] for m in saved['media'] if m['featured']] == [second['id']]
    assert saved['review']['article_hash'] == old_hash
    assert authed.put(url, json={'position': 'missing-section'}).status_code == 400


@pytest.mark.parametrize('data', [b'<svg onload="alert(1)"></svg>', b'not an image', b'x' * (media.MAX_BYTES+1)], ids=['svg', 'corrupt', 'oversized'])
def test_invalid_images_rejected_without_saved_assets(job, data):
    with pytest.raises(ValueError):
        media.add(job, data)
    assert not db.get_job(job['id']).get('media')


def test_image_request_is_idempotent_and_blocks_conflicting_work(authed, job, monkeypatch):
    queued = Mock()
    monkeypatch.setattr(image_generation.executor, 'submit', queued)
    monkeypatch.setattr(image_generation, 'get_secret', lambda key: 'test-key')
    body = {'request_id': 'a' * 32, 'quality': 'low'}
    endpoint = f'/api/jobs/{job["id"]}/images/generate'
    assert authed.post(endpoint, json=body).status_code == 202
    assert authed.post(endpoint, json=body).status_code == 202
    assert queued.call_count == 1
    assert authed.post(endpoint, json={'request_id': 'b' * 32}).status_code == 400
    assert authed.post(f'/api/jobs/{job["id"]}/generate').status_code == 400
    assert authed.put(f'/api/jobs/{job["id"]}/article', json=job['article']).status_code == 409
    image_generation.recover()
    assert db.get_job(job['id'])['image_tasks'][0]['status'] == 'interrupted'
    assert queued.call_count == 1


@pytest.mark.parametrize('fail', [False, True])
def test_paid_image_call_runs_once_and_records_success_or_safe_failure(job, monkeypatch, fail):
    monkeypatch.setattr(image_generation.executor, 'submit', Mock())
    monkeypatch.setattr(image_generation, 'get_secret', lambda key: 'test-key')
    task = image_generation.submit(job, ImageGeneration(request_id='a' * 32, reference_mode='none'))
    result = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(picture()).decode())], usage=None)
    api = MagicMock()
    api.__enter__.return_value = api
    api.images.generate.return_value = result
    if fail:
        api.images.generate.side_effect = RuntimeError('private provider payload')
    constructor = Mock(return_value=api)
    monkeypatch.setattr(image_generation, 'OpenAI', constructor)
    image_generation.run(job['id'], task['id'])
    image_generation.run(job['id'], task['id'])
    assert api.images.generate.call_count == 1
    assert constructor.call_args.kwargs['max_retries'] == 0
    saved = db.get_job(job['id'])
    assert saved['article'] == job['article']
    assert saved['image_tasks'][0]['status'] == ('error' if fail else 'completed')
    if fail:
        assert 'private' not in saved['image_tasks'][0]['error']
        assert not saved.get('media')
    else:
        assert len(saved['media']) == 1 and saved['media'][0]['origin'] == 'ai'
        assert saved['usage'][-1]['images'] == 1


def test_wordpress_uploads_once_reuses_media_and_sets_featured(job, asset, monkeypatch):
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('u', 'p')))
    slug = f'seo-master-{job["id"]}-{asset["id"]}'
    remote = {'id': 77, 'slug': slug, 'source_url': 'https://blog.example/uploads/folhas.webp'}
    marker = f'<!-- seo-master:{job["id"]} -->'
    uploads, posts = [], []
    def handler(request):
        url = request.url.path
        if request.method == 'GET':
            if url.endswith('/media/77'):
                return httpx.Response(200, json=remote)
            if url.endswith('/posts/42'):
                return httpx.Response(200, json={'id': 42, 'status': 'pending', 'content': {'raw': posts[-1]['content']}})
            return httpx.Response(200, json=[])
        if url.endswith('/media'):
            uploads.append(request)
            assert b'name="alt_text"' in request.content
            assert b'image/webp' in request.content
            return httpx.Response(201, json=remote)
        if url.endswith('/media/77'):
            assert json.loads(request.content)['alt_text'] == asset['alt']
            return httpx.Response(200, json=remote)
        payload = json.loads(request.content)
        assert payload['featured_media'] == 77
        assert '<!-- wp:image {"sizeSlug":"full","linkDestination":"none","id":77}' in payload['content']
        assert remote['source_url'] in payload['content'] and '/api/jobs/' not in payload['content']
        posts.append(payload)
        assert payload['status'] == 'pending'
        return httpx.Response(201, json={'id': 42, 'status': 'pending'})
    original = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    wordpress.send_for_review(job)
    wordpress.send_for_review(job)
    assert len(uploads) == 1 and len(posts) == 2


def test_ambiguous_media_upload_does_not_blindly_retry(job, asset, monkeypatch):
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('u', 'p')))
    writes = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[])
        writes.append(str(request.url))
        raise httpx.ReadTimeout('timeout')
    original = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match='não confirmou'):
        wordpress.send_for_review(job)
    with pytest.raises(ValueError, match='sem confirmação'):
        wordpress.send_for_review(db.get_job(job['id']))
    assert writes == ['https://blog.example/wp-json/wp/v2/media']
