import sqlite3
import pytest

from app import db
from app.security import get_secret, public_https_url, validate_proxies


def test_auth_and_csrf(client):
    assert client.get('/health').status_code == 200
    assert client.get('/api/jobs').status_code == 401
    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    response = client.post('/api/login', json={'password': 'test-password-long-enough'}, headers={'X-Requested-With': ''})
    assert response.status_code == 403
    response = client.post('/api/login', json={'password': 'test-password-long-enough'}, headers={'Origin': 'https://evil.example'})
    assert response.status_code == 403


def test_secret_encryption_and_non_disclosure(authed):
    value = 'unit-test-secret-not-real'
    response = authed.put('/api/settings', json={'openai_api_key': value, 'brand_name': 'Meu blog'})
    assert response.status_code == 200
    assert value not in response.text
    assert response.json()['openai_api_key_configured'] is True
    assert get_secret('openai_api_key') == value
    with db.connect() as c:
        assert value not in c.execute("SELECT value FROM settings WHERE key='openai_api_key'").fetchone()[0]
    authed.put('/api/settings', json={'openai_api_key': None})
    assert get_secret('openai_api_key') == value


@pytest.mark.parametrize('url', ['http://example.com', 'https://127.0.0.1', 'https://[::1]', 'https://user:pass@example.com', 'https://169.254.169.254', 'https://example.com:8080'])
def test_ssrf_rejected(url):
    with pytest.raises(ValueError):
        public_https_url(url)


def test_password_change_revokes_sessions(authed):
    assert authed.post('/api/password', json={'current_password': 'wrong', 'new_password': 'a-new-long-password'}).status_code == 400
    assert authed.post('/api/password', json={'current_password': 'test-password-long-enough', 'new_password': 'a-new-long-password'}).status_code == 200
    assert authed.get('/api/me').status_code == 401
    assert authed.post('/api/login', json={'password': 'a-new-long-password'}).status_code == 200


def test_validation_does_not_reflect_secrets(authed):
    secret = 'private-' * 100
    response = authed.put('/api/settings', json={'openai_api_key': secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_bruteforce_limit(client):
    for _ in range(10):
        assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 429


def test_proxy_formats_and_secrecy(authed, monkeypatch):
    monkeypatch.setattr('app.security.socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 80))])
    raw = '8.8.8.8:8000:test-user:test-password'
    assert validate_proxies(raw) == 'http://test-user:test-password@8.8.8.8:8000'
    response = authed.put('/api/settings', json={'youtube_proxy_urls': raw})
    assert response.status_code == 200
    assert response.json()['youtube_proxy_urls_configured'] is True
    assert 'test-password' not in response.text
    assert 'test-password' in get_secret('youtube_proxy_urls')
    authed.put('/api/settings', json={'youtube_proxy_urls': None})
    assert 'test-password' in get_secret('youtube_proxy_urls')


@pytest.mark.parametrize('url', ['http://127.0.0.1:8000', 'http://[::1]', 'ftp://example.com', 'invalid'])
def test_proxy_rejects_private_or_invalid_destinations(url):
    with pytest.raises(ValueError):
        validate_proxies(url)
