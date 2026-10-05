from unittest.mock import Mock
import pytest
import httpx

from app import db, generation, pipeline, wordpress, youtube
from app.schemas import Brief


@pytest.mark.parametrize('url', ['https://youtu.be/uibZD5Dgrao?t=1', 'https://www.youtube.com/watch?v=uibZD5Dgrao&list=x', 'https://youtube.com/shorts/uibZD5Dgrao', 'https://m.youtube.com/live/uibZD5Dgrao'])
def test_video_links(url):
    assert youtube.video_id(url) == 'uibZD5Dgrao'


@pytest.mark.parametrize('url', ['https://youtube.com.evil.test/watch?v=uibZD5Dgrao', 'file:///etc/passwd', 'https://youtube.com/playlist?list=abc', 'https://youtu.be/abc'])
def test_invalid_video_links(url):
    with pytest.raises(ValueError):
        youtube.video_id(url)


def test_duplicate_videos_removed():
    assert len(Brief(urls=['https://youtu.be/uibZD5Dgrao', 'https://www.youtube.com/watch?v=uibZD5Dgrao']).urls) == 1


def test_manual_timestamps():
    text = 'Uma transcrição detalhada com informações para testar a preservação da origem. ' * 3
    segments = youtube.manual_segments(text, 'v1')
    assert segments[0]['start'] is None
    segments = youtube.manual_segments('1\n00:01:20,500 --> 00:01:30,000\n' + text + '\n\n', 'v2')
    assert segments[0]['start'] == 80.5
    assert segments[0]['id'] == 'v2s1'


def test_edits_invalidate_review_and_preserve_version(authed, job):
    article = job['article'] | {'title': 'Uma edição nova'}
    assert authed.put('/api/jobs/test-job/article', json=article).status_code == 200
    saved = db.get_job('test-job')
    assert saved['review'] is None
    assert saved['status'] == 'needs_review'
    assert db.revisions('test-job')[0]['data']['title'] == job['article']['title']
    assert authed.post('/api/jobs/test-job/wordpress', json={'editorial_approval': True}).status_code == 400


def test_missing_citations_and_xss(job):
    assert generation.deterministic_findings(job) == []
    job['article']['markdown'] += '\n[[invented]]\n<script>alert(1)</script><img src=x onerror=alert(1)>'
    assert len(generation.deterministic_findings(job)) == 1
    rendered = generation.render_article(job)
    assert '<script>' not in rendered
    assert '<img' not in rendered
    assert 'watch?v=abcdefghijk&amp;t=10s' in rendered


def test_extraction_discards_untraceable_claims(job):
    good = {'statement': 'O autor observa as folhas.', 'kind': 'fato',
            'evidence': [{'source_id': 'v1s1', 'excerpt': 'O autor observa o desenvolvimento'}]}
    bad = {'statement': 'O autor mediu resultados.', 'kind': 'fato',
           'evidence': [{'source_id': 'v1s1', 'excerpt': 'Números que não existem no vídeo'}]}
    result = generation.validate_dossier({'claims': [good, bad], 'gaps': []}, generation.evidence_map(job))
    assert result['claims'] == [good]
    assert result['gaps']
    with pytest.raises(ValueError):
        generation.validate_dossier({'claims': [bad], 'gaps': []}, generation.evidence_map(job))


def test_recovery_marks_jobs_interrupted(job):
    job['status'] = 'writing'
    db.save_job(job)
    pipeline.recover()
    assert db.get_job(job['id'])['status'] == 'interrupted'


def test_no_key_preserves_sources(job):
    job.pop('article')
    db.save_job(job)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'awaiting_key'
    assert saved['sources'][0]['segments']


def test_failed_source_never_generates(job, monkeypatch):
    job['sources'] = []
    job.pop('article')
    db.save_job(job)
    monkeypatch.setattr(youtube, 'extract', Mock(side_effect=ValueError('Vídeo bloqueado')))
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'title': 'Vídeo', 'video_id': vid, 'url': job['brief']['urls'][0]})
    writer = Mock()
    monkeypatch.setattr(generation, 'write_article', writer)
    pipeline.run(job['id'])
    assert db.get_job(job['id'])['status'] == 'error'
    writer.assert_not_called()


def test_wordpress_draft_and_reconciliation(job, monkeypatch):
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('user','pass')))
    marker = f'<!-- seo-master:{job["id"]} -->'
    posts = []
    def handler(request):
        import json
        if request.method == 'GET':
            body = {'id': 42, 'status': 'draft', 'content': {'raw': marker}} if request.url.path.endswith('/42') else []
            return httpx.Response(200, json=body)
        payload = json.loads(request.content)
        assert payload['status'] == 'draft'
        assert marker in payload['content']
        posts.append(str(request.url))
        return httpx.Response(201, json={'id': 42, 'status': 'draft'})
    original = httpx.Client
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    wordpress.send_draft(job)
    assert job['wordpress']['id'] == 42
    wordpress.send_draft(job)
    assert posts == ['https://blog.example/wp-json/wp/v2/posts', 'https://blog.example/wp-json/wp/v2/posts/42']


def test_wordpress_uncertain_send_is_not_recreated(job, monkeypatch):
    monkeypatch.setattr(wordpress, 'connection', lambda: ('https://blog.example', httpx.BasicAuth('u','p')))
    job['wordpress'] = {'uncertain': True, 'site': 'https://blog.example'}
    original = httpx.Client
    def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200,json=[])
    monkeypatch.setattr(wordpress.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match='sem confirmação'):
        wordpress.send_draft(job)


def test_export_uses_sources_and_safe_html(authed, job):
    response = authed.get('/api/jobs/test-job/export')
    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    assert 'watch?v=abcdefghijk&amp;t=10s' in response.text
    assert authed.get('/api/jobs/test-job/export?format=json').json()['evidence']['v1s1']['kind'] == 'transcript'
    preview = authed.get('/api/jobs/test-job/preview')
    assert preview.headers['x-frame-options'] == 'SAMEORIGIN'
    assert "frame-ancestors 'self'" in preview.headers['content-security-policy']
    assert authed.get('/').headers['x-frame-options'] == 'DENY'


def test_resume_reuses_completed_analysis_and_writing(job, monkeypatch):
    job['dossier'] = {'claims': [], 'gaps': []}
    job['generation_complete'] = True
    db.save_job(job)
    monkeypatch.setattr(pipeline, 'get_secret', lambda name: 'test-key')
    analyze = Mock()
    write = Mock()
    monkeypatch.setattr(generation, 'extract_dossier', analyze)
    monkeypatch.setattr(generation, 'write_article', write)
    monkeypatch.setattr(generation, 'review_article', lambda job: {'findings': []})
    pipeline.run(job['id'], 'resume')
    analyze.assert_not_called()
    write.assert_not_called()
    assert db.get_job(job['id'])['status'] == 'ready'


def test_proxy_fallback_uses_next_proxy(job, monkeypatch):
    monkeypatch.setattr(youtube, 'get_secret', lambda name: 'http://proxy-one:8000\nhttp://proxy-two:8000' if name == 'youtube_proxy_urls' else '')
    monkeypatch.setattr(youtube.random, 'shuffle', lambda values: None)
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'url': 'https://www.youtube.com/watch?v='+vid, 'title': 'Example'})
    first, second = Mock(), Mock()
    first.list.side_effect = ValueError('Blocked')
    transcript = second.list.return_value.find_transcript.return_value.fetch.return_value
    transcript.language_code = 'pt'
    transcript.to_raw_data.return_value = [{'text': 'Uma transcrição de exemplo sobre observações da horta, feita pelo apresentador do vídeo. ' * 3, 'start': 0, 'duration': 20}]
    constructor = Mock(side_effect=[first, second])
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', constructor)
    source = youtube.extract('https://youtu.be/abcdefghijk', 'v1')
    assert source['provider'] == 'Legendas do YouTube via proxy'
    assert constructor.call_count == 2


def test_research_without_citations_is_explicit_and_never_used_as_evidence(job, monkeypatch):
    from types import SimpleNamespace
    response = SimpleNamespace(status='completed', output=[], output_text='An uncited note.', id='test-response', usage=None)
    api = Mock()
    api.__enter__ = Mock(return_value=api)
    api.__exit__ = Mock(return_value=False)
    api.responses.create.return_value = response
    monkeypatch.setattr(generation, 'client', lambda: api)
    job['dossier'] = {'claims': []}
    result = generation.research(job)
    assert result['status'] == 'unavailable'
    assert result['text'] == ''
    assert result['sources'] == []
    assert 'apenas os vídeos' in result['notice']


def test_scoped_schema_disallows_invented_source_ids():
    from app.schemas import Dossier
    schema = generation.scoped_schema(Dossier, ['v1s1', 'w1'])
    base = {'main_question':'Question','summary':'Summary','examples':[],'conflicts':[],'gaps':[],'outline':[]}
    good = {'statement':'Statement','kind':'fato','evidence':[{'source_id':'v1s1','excerpt':'quoted text'}]}
    assert schema.model_validate(base | {'claims':[good]}).claims[0].evidence[0].source_id == 'v1s1'
    bad = good | {'evidence':[{'source_id':'invented','excerpt':'quoted text'}]}
    with pytest.raises(ValueError):
        schema.model_validate(base | {'claims':[bad]})


def test_editorial_decision_is_audited_and_versioned(authed, job):
    job['review']['reviewed_at'] = 'review-v1'
    job['review']['findings'] = [{'severity':'blocking', 'passage':'O autor observa', 'reason':'Sugestão imprecisa', 'suggestion':'Conferir', 'source_ids':['v1s1']}]
    db.save_job(job)
    body = {'finding_index':0, 'review_version':'review-v1', 'article_hash':generation.article_hash(job['article']),
            'reason':'Conferi o trecho v1s1: a afirmação está atribuída corretamente ao autor.'}
    assert authed.post('/api/jobs/test-job/review/decision', json=body).status_code == 200
    saved = db.get_job(job['id'])
    assert generation.unresolved_findings(saved) == []
    assert saved['review']['decision_history'][0]['reason'] == body['reason']
    wordpress.ensure_reviewed(saved)
    assert authed.post('/api/jobs/test-job/review/decision', json=body | {'review_version':'stale'}).status_code == 409
    assert authed.post('/api/jobs/test-job/review/decision', json=body | {'dismiss':False}).status_code == 200
    assert len(generation.unresolved_findings(db.get_job(job['id']))) == 1


def test_editor_cannot_dismiss_nonexistent_references(authed, job):
    job['article']['markdown'] += ' [[invented]]'
    job['review'] = {'reviewed_at':'v1','article_hash':generation.article_hash(job['article']), 'findings':generation.deterministic_findings(job)}
    db.save_job(job)
    response = authed.post('/api/jobs/test-job/review/decision', json={'finding_index':0,'review_version':'v1',
        'article_hash':job['review']['article_hash'],'reason':'Quero dispensar esta referência inexistente mesmo assim.'})
    assert response.status_code == 400
    with pytest.raises(ValueError):
        wordpress.ensure_reviewed(db.get_job(job['id']))
