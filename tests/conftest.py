import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-password-long-enough')
    monkeypatch.setenv('COOKIE_SECURE', '0')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('YOUTUBE_PROXY_URLS', raising=False)
    monkeypatch.delenv('APP_URL', raising=False)
    with TestClient(app, headers={'X-Requested-With': 'SEO-Master'}) as c:
        yield c


@pytest.fixture
def authed(client):
    assert client.post('/api/login', json={'password': 'test-password-long-enough'}).status_code == 200
    return client


@pytest.fixture
def job(authed):
    source = {'id': 'v1', 'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'video_id': 'abcdefghijk',
              'title': 'Uma horta de manjericão', 'author': 'Autor de exemplo', 'thumbnail': '', 'status': 'ok',
              'segments': [{'id': 'v1s1', 'text': 'O autor observa o desenvolvimento das folhas do manjericão.', 'start': 10, 'end': 20}]}
    article = {'title': 'Como observar uma horta de manjericão', 'seo_title': 'Como observar uma horta de manjericão',
               'slug': 'horta-manjericao', 'meta_description': 'Observações sobre uma horta de manjericão.',
               'excerpt': 'Um relato de observação.', 'tags': ['horta'],
               'markdown': '## Observação da horta\n\nO autor observa o desenvolvimento das folhas do manjericão. [[v1s1]]\n\nO relato é uma experiência pessoal.'}
    value = {'id': 'test-job', 'created_at': db.now(), 'status': 'ready', 'brief': {'urls': [source['url']],
             'topic': 'Horta', 'keyword': 'horta', 'audience': 'Iniciantes', 'tone': 'Claro', 'instructions': '',
             'target_words': 800, 'research': False}, 'sources': [source], 'article': article, 'events': [], 'usage': []}
    from app.generation import article_hash
    value['review'] = {'article_hash': article_hash(article), 'findings': [], 'supported_claims': [], 'summary': 'Revisado'}
    db.save_job(value)
    return value
