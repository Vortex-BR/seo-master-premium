"""Saved content is delivered independently from optional editorial diagnoses."""
from copy import deepcopy
import json

import httpx
import pytest

from app import db, generation, publishing, wordpress
from app.editorial import delivery, store


FORMATS = ('markdown', 'html', 'json', 'wordpress', 'wordpress-html')


@pytest.mark.parametrize('format', FORMATS)
@pytest.mark.parametrize('review_state', ('legacy', 'missing', 'stale', 'failed', 'changed_brief'))
def test_saved_concise_article_exports_with_any_editorial_state(authed, job, format, review_state):
    job['article']['markdown'] = 'O autor observa as folhas do manjericão. [[v1s1]]'
    job['status'] = 'needs_review'
    job['review'] = {'article_hash': generation.article_hash(job['article']), 'findings': [
        {'severity': 'blocking', 'reason': 'O texto deveria ser maior.', 'passage': 'O autor observa',
         'suggestion': 'Expandir artificialmente.', 'source_ids': [], 'category': 'subjective_recommendation'}]}
    if review_state == 'missing':
        job['review'] = None
    elif review_state == 'stale':
        job['review']['article_hash'] = '0' * 64
    elif review_state == 'failed':
        job['status'] = 'error'
        job['error'] = 'O serviço de análise não respondeu.'
        job['review']['review_incomplete'] = True
    elif review_state == 'changed_brief':
        job['status'] = 'brief_updated'
        job['article_needs_generation'] = True
        job['generation_complete'] = False
    original = deepcopy(job['article'])
    db.save_job(job)
    response = authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format})
    assert response.status_code == 200, response.text
    assert db.get_job(job['id'])['article'] == original
    state = authed.get(f'/api/jobs/{job["id"]}').json()
    listing = authed.get('/api/jobs').json()[0]
    assert state['export_available'] is listing['export_available'] is True
    assert state['delivery_state'] == listing['delivery_state'] == 'article_available'
    assert state['export_error'] is None
    if format == 'json':
        assert response.json()['article'] == original
        assert response.json()['export_available'] is True


def test_saved_content_exports_during_internal_analysis_and_image_generation(authed, job):
    job['status'] = 'reviewing'
    job['image_tasks'] = [{'status': 'running'}]
    db.save_job(job)
    for format in FORMATS:
        assert authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format}).status_code == 200


@pytest.mark.parametrize('activity', ('writing', 'reviewing', 'image'))
def test_remote_sync_waits_for_concurrent_saves_while_downloads_remain_available(authed, job, monkeypatch, activity):
    if activity == 'image':
        job['image_tasks'] = [{'status': 'running'}]
    else:
        job['status'] = activity
    db.save_job(job)
    connection = []
    monkeypatch.setattr(wordpress, 'connection', lambda: connection.append(True))
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 409
    assert 'gravação' in response.json()['detail']
    assert not connection
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200


@pytest.fixture
def remote_wp(monkeypatch):
    """A mutable WordPress boundary with real serialization and synchronization."""
    state = {'posts': {}, 'writes': [], 'created': 0, 'fail_next': None}
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('u', 'p')))

    def handler(request):
        if request.method == 'GET':
            if request.url.path.endswith('/posts/42'):
                return httpx.Response(200, json=state['posts'][42])
            return httpx.Response(200, json=list(state['posts'].values()))
        payload = json.loads(request.content)
        state['writes'].append((request.url.path, payload))
        failure, state['fail_next'] = state['fail_next'], None
        if failure == 'before':
            raise httpx.ReadTimeout('WordPress write not confirmed')
        if request.url.path.endswith('/posts'):
            state['created'] += 1
        assert payload['status'] == 'pending'
        post = {'id': 42, 'status': 'pending', 'content': {'raw': payload['content']},
                'title': {'raw': payload['title']}, 'excerpt': {'raw': payload['excerpt']},
                'slug': payload['slug'], 'featured_media': payload['featured_media']}
        state['posts'][42] = post
        if failure == 'after':
            raise httpx.ReadTimeout('WordPress write not confirmed')
        return httpx.Response(201, json=post)

    original = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return state


@pytest.mark.parametrize('body', (None, {}, {'editorial_approval': False}, {'editorial_approval': True}))
def test_wordpress_click_sends_saved_article_with_suggestions_without_approval(authed, job, remote_wp, body):
    job['status'] = 'needs_review'
    job['article_needs_generation'] = True
    job['review']['article_hash'] = '0' * 64
    job['review']['findings'] = [{'severity': 'blocking', 'passage': 'O autor observa',
                                'reason': 'Acrescentar seções.', 'suggestion': 'Expandir.', 'source_ids': []}]
    original = deepcopy(job['article'])
    db.save_job(job)
    kwargs = {'json': body} if body is not None else {}
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', **kwargs)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'pending'
    assert remote_wp['created'] == 1
    assert original['title'] == remote_wp['writes'][0][1]['title']
    assert db.get_job(job['id'])['article'] == original


def test_edit_saves_and_imports_new_version_without_review_and_preserves_prior_diagnosis(authed, job, remote_wp):
    original = deepcopy(job['article'])
    previous_review = deepcopy(job['review'])
    article = {**original, 'title': 'Título atualizado pelo usuário',
               'markdown': 'O autor relata sua observação das folhas. [[v1s1]]'}
    endpoint = f'/api/jobs/{job["id"]}'
    assert authed.put(endpoint + '/article', json={**article,
                      'base_article_hash': generation.article_hash(job['article'])}).status_code == 200
    saved = db.get_job(job['id'])
    assert saved['review'] is None
    assert saved['review_history'][0]['article_hash'] == previous_review['article_hash']
    assert store.artifacts(job['id'], 'review_history')[0]['data'] == previous_review
    assert db.revisions(job['id'])[0]['data'] == original
    assert delivery.describe(saved)['export_available'] is True
    assert authed.post(endpoint + '/wordpress', json={}).status_code == 200
    payload = remote_wp['writes'][0][1]
    assert payload['title'] == article['title']
    assert 'relata sua observação' in payload['content']


@pytest.mark.parametrize('invalid_article', (None, {}, {'title': 'Artigo sem corpo'}, 'invalid',
                                            {'markdown': ''}, {'markdown': '   '}, {'title': '   '},
                                            {'slug': '  '}, {'markdown': 'Corpo\x00inválido'}))
def test_invalid_saved_content_returns_clear_technical_error(authed, job, monkeypatch, invalid_article):
    job['article'] = job['article'] | invalid_article if isinstance(invalid_article, dict) else invalid_article
    if invalid_article == {} or invalid_article == {'title': 'Artigo sem corpo'}:
        job['article'] = invalid_article
    job['review'] = None
    db.save_job(job)
    called = []
    monkeypatch.setattr(wordpress, 'connection', lambda: called.append(True))
    for format in FORMATS:
        response = authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format})
        assert response.status_code == 400
        assert response.json()['detail']
    assert authed.post(f'/api/jobs/{job["id"]}/wordpress', json={}).status_code == 400
    assert not called
    assert authed.get('/api/jobs').json()[0]['export_available'] is False
    detail = authed.get(f'/api/jobs/{job["id"]}').json()
    assert detail['export_available'] is False and detail['export_error']


def test_repeated_import_updates_one_pending_post_and_preserves_external_edit(authed, job, remote_wp):
    endpoint = f'/api/jobs/{job["id"]}/wordpress'
    assert authed.post(endpoint, json={}).status_code == 200
    assert authed.post(endpoint, json={}).status_code == 200
    assert remote_wp['created'] == 1
    assert [path for path, _ in remote_wp['writes']] == ['/wp-json/wp/v2/posts', '/wp-json/wp/v2/posts/42']
    remote_wp['posts'][42]['content']['raw'] += '\nUma edição externa com o marcador preservado.'
    response = authed.post(endpoint, json={})
    assert response.status_code == 400
    assert 'fora do aplicativo' in response.json()['detail']
    assert len(remote_wp['writes']) == 2


@pytest.mark.parametrize('field', ('title', 'excerpt', 'slug', 'featured_media'))
def test_remote_metadata_changes_are_preserved_before_import_updates(authed, job, remote_wp, field):
    endpoint = f'/api/jobs/{job["id"]}/wordpress'
    assert authed.post(endpoint, json={}).status_code == 200
    if field in ('title', 'excerpt'):
        remote_wp['posts'][42][field]['raw'] = 'Uma edição externa.'
    else:
        remote_wp['posts'][42][field] = 99 if field == 'featured_media' else 'edicao-externa'
    response = authed.post(endpoint, json={})
    assert response.status_code == 400
    assert 'fora do aplicativo' in response.json()['detail']
    assert len(remote_wp['writes']) == 1


@pytest.mark.parametrize('external_edit', (False, True))
def test_legacy_sync_reconstructs_previous_saved_version_and_preserves_external_content(authed, job, remote_wp, external_edit):
    old_content = publishing.render(job, gutenberg=True) + f'\n<!-- seo-master:{job["id"]} -->'
    job['wordpress'] = {'site': 'https://blog.example', 'id': 42,
                        'article_hash': generation.article_hash(job['article'])}
    db.save_job(job)
    # A manual edit must still compare against the earlier saved version.
    article = {**job['article'], 'markdown': 'Uma versão escolhida pelo usuário. [[v1s1]]'}
    assert authed.put(f'/api/jobs/{job["id"]}/article', json={**article,
                      'base_article_hash': generation.article_hash(job['article'])}).status_code == 200
    remote_wp['posts'][42] = {'id': 42, 'status': 'pending', 'content': {'raw': old_content}}
    if external_edit:
        remote_wp['posts'][42]['content']['raw'] += '\nUma alteração realizada diretamente no WordPress.'
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == (400 if external_edit else 200), response.text
    assert len(remote_wp['writes']) == (0 if external_edit else 1)
    assert remote_wp['created'] == 0


def test_legacy_post_with_unverifiable_version_never_overwrites_content(authed, job, remote_wp):
    job['wordpress'] = {'site': 'https://blog.example', 'id': 42}
    db.save_job(job)
    remote_wp['posts'][42] = {'id': 42, 'status': 'pending',
                              'content': {'raw': f'Outro conteúdo. <!-- seo-master:{job["id"]} -->'}}
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'versão verificável' in response.json()['detail']
    assert remote_wp['writes'] == []


def test_legacy_sync_preserves_external_featured_image_without_metadata_snapshot(authed, job, remote_wp):
    old_content = publishing.render(job, gutenberg=True) + f'\n<!-- seo-master:{job["id"]} -->'
    job['wordpress'] = {'site': 'https://blog.example', 'id': 42,
                        'article_hash': generation.article_hash(job['article'])}
    db.save_job(job)
    remote_wp['posts'][42] = {'id': 42, 'status': 'pending', 'content': {'raw': old_content},
                              'title': {'raw': job['article']['title']},
                              'excerpt': {'raw': job['article']['excerpt']},
                              'slug': job['article']['slug'], 'featured_media': 99}
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'fora do aplicativo' in response.json()['detail']
    assert remote_wp['posts'][42]['featured_media'] == 99
    assert remote_wp['writes'] == []


def test_legacy_sync_with_unverifiable_featured_baseline_stops_before_media_updates(authed, job, remote_wp):
    # A featured-only image does not appear in the article body, so a body hash
    # cannot establish which image was previously selected on the remote post.
    job['media'] = [{'id': 'featured-image', 'in_body': False, 'featured': True,
                     'wordpress': {'site': 'https://blog.example', 'id': 77,
                                   'url': 'https://blog.example/uploads/featured.webp'}}]
    old_content = publishing.render(job, gutenberg=True) + f'\n<!-- seo-master:{job["id"]} -->'
    job['wordpress'] = {'site': 'https://blog.example', 'id': 42,
                        'article_hash': generation.article_hash(job['article'])}
    db.save_job(job)
    remote_wp['posts'][42] = {'id': 42, 'status': 'pending', 'content': {'raw': old_content},
                              'title': {'raw': job['article']['title']},
                              'excerpt': {'raw': job['article']['excerpt']},
                              'slug': job['article']['slug'], 'featured_media': 77}
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'fora do aplicativo' in response.json()['detail']
    assert remote_wp['posts'][42]['featured_media'] == 77
    assert remote_wp['writes'] == []


@pytest.mark.parametrize('scenario', ('creation_after', 'update_before', 'update_after'))
def test_unconfirmed_import_reconciles_verified_content_without_recreating_posts(authed, job, remote_wp, scenario):
    endpoint = f'/api/jobs/{job["id"]}'
    if scenario.startswith('update'):
        assert authed.post(endpoint + '/wordpress', json={}).status_code == 200
        article = {**job['article'], 'markdown': 'A versão salva foi atualizada. [[v1s1]]'}
        assert authed.put(endpoint + '/article', json={**article,
                          'base_article_hash': generation.article_hash(job['article'])}).status_code == 200
    remote_wp['fail_next'] = scenario.split('_')[1]
    response = authed.post(endpoint + '/wordpress', json={})
    assert response.status_code == 400
    assert db.get_job(job['id'])['wordpress']['uncertain'] is True
    assert authed.post(endpoint + '/wordpress', json={}).status_code == 200
    assert remote_wp['created'] == 1
    assert remote_wp['writes'][-1][0] == '/wp-json/wp/v2/posts/42'
    assert db.get_job(job['id'])['wordpress']['uncertain'] is False


@pytest.mark.parametrize('status', ('publish', 'private', 'future'))
def test_wordpress_never_overwrites_a_post_outside_draft_or_pending(authed, job, remote_wp, status):
    endpoint = f'/api/jobs/{job["id"]}/wordpress'
    assert authed.post(endpoint, json={}).status_code == 200
    remote_wp['posts'][42]['status'] = status
    assert authed.post(endpoint, json={}).status_code == 400
    assert len(remote_wp['writes']) == 1


def test_wordpress_slug_collision_is_technical_and_never_creates_a_duplicate(authed, job, remote_wp):
    remote_wp['posts'][42] = {'id': 42, 'status': 'pending', 'content': {'raw': 'Outro artigo'}}
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'slug' in response.json()['detail']
    assert remote_wp['writes'] == []


def test_missing_wordpress_auth_still_returns_configuration_error(authed, job):
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'Configure URL' in response.json()['detail']


def test_wordpress_auth_failure_stops_before_remote_mutation(authed, job, monkeypatch):
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('u', 'wrong')))
    original = httpx.Client
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(401, json={'message': 'Credencial incorreta'})
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    response = authed.post(f'/api/jobs/{job["id"]}/wordpress', json={})
    assert response.status_code == 400
    assert 'permissões' in response.json()['detail']
    assert not db.get_job(job['id']).get('wordpress')


def test_direct_publishing_entry_points_reject_invalid_schema(job):
    job['article']['markdown'] = '   '
    with pytest.raises(ValueError, match='conteúdo'):
        publishing.render(job)
    with pytest.raises(ValueError, match='conteúdo'):
        publishing.wxr(job, 'https://studio.example')
