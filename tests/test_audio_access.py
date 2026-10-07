from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import db, local_audio, pipeline, youtube
from app.security import normalize_proxies, save_secret
from app.transcripts import Deadline, SourceError
from test_transcripts import wav_bytes


@pytest.mark.parametrize('stderr,code', [
    ('Sign in to confirm your age', 'age_restricted'),
    ('Sign in to confirm you are not a bot', 'ip_blocked'),
    ('HTTP Error 429: Too Many Requests', 'rate_limit'),
    ('Login required to watch this video', 'login_required'),
    ('Video is unavailable', 'restricted'),
    ('HTTP Error 407: Proxy authentication required', 'proxy_credentials'),
    ('PO Token required', 'token_required'),
])
def test_access_failures_have_distinct_diagnostics(stderr, code):
    assert local_audio.classify_download(stderr, 'route').diagnostic['code'] == code


def completed_download(command, **kwargs):
    Path(command[command.index('-o')+1].replace('%(ext)s', 'm4a')).write_bytes(b'audio')
    return SimpleNamespace(returncode=0)


def test_auto_tries_direct_even_with_saved_proxies(client, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    runner = Mock(side_effect=completed_download)
    monkeypatch.setattr(local_audio.subprocess, 'run', runner)
    attempts = []
    local_audio.download('abcdefghijk', 'https://youtu.be/abcdefghijk', ['http://example.test:80'], Deadline(60), lambda *a: None, attempts)
    command = runner.call_args.args[0]
    assert command[command.index('--proxy')+1] == ''
    assert attempts[-1]['outcome'] == 'ok'


def test_auto_falls_back_to_proxy_when_direct_is_blocked(client, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            return SimpleNamespace(returncode=1, stderr=b'Sign in to confirm you are not a bot')
        return completed_download(command)
    monkeypatch.setattr(local_audio.subprocess, 'run', run)
    attempts = []
    local_audio.download('abcdefghijk', 'https://youtu.be/abcdefghijk', ['http://example.test:80'], Deadline(60), lambda *a: None, attempts)
    assert [c[c.index('--proxy')+1] for c in commands] == ['', 'http://example.test:80']
    assert attempts[0]['retry_at']


def test_explicit_proxy_mode_never_uses_direct(client):
    db.set_setting('youtube_connection_mode', 'proxy')
    assert local_audio.connection_routes(['http://example.test']) == ['http://example.test']
    with pytest.raises(SourceError): local_audio.connection_routes([])
    db.set_setting('youtube_connection_mode', 'direct')
    assert local_audio.connection_routes(['http://example.test']) == [None]


def test_environment_proxy_format_matches_the_panel(client, monkeypatch):
    monkeypatch.setenv('YOUTUBE_PROXY_URLS', 'proxy.example:8080:user:p@ss:word')
    assert local_audio.configured_proxies() == ['http://user:p%40ss%3Aword@proxy.example:8080']
    monkeypatch.setenv('YOUTUBE_PROXY_URLS', 'http://secret:password@proxy.example:bad')
    with pytest.raises(SourceError) as failure: local_audio.configured_proxies()
    assert 'password' not in str(failure.value)


def test_access_test_uses_same_transport_and_caches_without_changing_job(authed, job, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    save_secret('youtube_proxy_urls', 'http://private-user:private-password@proxy.test:80')
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=b'abcdefghijk\n', stderr=b''))
    monkeypatch.setattr(local_audio.subprocess, 'run', runner)
    endpoint = f'/api/jobs/{job["id"]}/sources/abcdefghijk/test-access'
    before = db.get_job(job['id'])
    result = authed.post(endpoint)
    assert result.status_code == 200 and result.json()['ok']
    command = runner.call_args.args[0]
    assert '--check-formats' in command and '--skip-download' in command
    assert 'private-password' not in result.text and 'private-user' not in result.text
    assert authed.post(endpoint).json()['cached']
    assert runner.call_count == 1
    assert db.get_job(job['id']) == before
    db.set_setting('youtube_connection_mode', 'proxy')
    assert not authed.post(endpoint).json()['cached']
    assert runner.call_count == 2
    command = runner.call_args.args[0]
    assert command[command.index('--proxy')+1].startswith('http://private-user:')


def test_access_test_checks_source_membership_and_auth(authed, job, monkeypatch):
    test = Mock(); monkeypatch.setattr(local_audio, 'access_test', test)
    assert authed.post(f'/api/jobs/{job["id"]}/sources/otherabcdef/test-access').status_code == 400
    authed.post('/api/logout')
    assert authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/test-access').status_code == 401
    test.assert_not_called()


def test_restricted_source_does_not_disable_route_for_other_videos(client, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    runner = Mock(return_value=SimpleNamespace(returncode=1, stderr=b'Sign in to confirm your age'))
    monkeypatch.setattr(local_audio.subprocess, 'run', runner)
    result = local_audio.access_test('abcdefghijk')
    assert result['routes'][0]['code'] == 'age_restricted'
    assert result['paused_routes'] == 0
    local_audio.access_test('lmnopqrstuv')
    assert runner.call_count == 2


def test_large_valid_audio_reaches_streaming_upload(authed, job, monkeypatch):
    monkeypatch.setattr(pipeline, 'executor', Mock())
    payload = wav_bytes(seconds=50)
    assert len(payload) > 1500000
    result = authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename=audio.wav', content=payload)
    assert result.status_code == 202, result.text
    assert result.json()['bytes'] == len(payload)
    assert authed.post('/api/login', content=payload).status_code == 413


def test_upload_exemption_cannot_bypass_configured_media_limit(authed, job, monkeypatch):
    db.set_setting('audio_max_mb', 16)
    result = authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename=audio.wav',
                         content=b'audio', headers={'content-length': str(17*1024*1024)})
    assert result.status_code == 413
    assert not list(local_audio.root().glob('*/original*'))
