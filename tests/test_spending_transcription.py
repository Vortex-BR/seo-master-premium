from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from openai import APIStatusError

from app import db, local_audio, spending, transcripts, youtube


@pytest.fixture
def paid_audio(monkeypatch):
    """Exercise the real budget boundary without downloading or billing audio."""
    def download(command, **kwargs):
        template = command[command.index('-o') + 1]
        Path(template.replace('%(ext)s', 'mp3')).write_bytes(b'test audio')
        return SimpleNamespace(returncode=0)

    downloader = Mock(side_effect=download)
    monkeypatch.setattr(youtube.subprocess, 'run', downloader)
    probe = Mock(return_value=90.25)
    monkeypatch.setattr(local_audio, 'probe', probe)
    api = Mock()
    api.audio.transcriptions.create.return_value = SimpleNamespace(
        segments=[SimpleNamespace(text='A complete explanation.', start=0, end=90)],
        language='pt')
    context = MagicMock()
    context.__enter__.return_value = api
    factory = Mock(return_value=context)
    monkeypatch.setattr(youtube, 'OpenAI', factory)
    return SimpleNamespace(api=api, factory=factory, probe=probe, download=downloader)


def test_paid_transcription_reserves_measured_duration_before_provider(job, paid_audio):
    expected = .010465  # ceil(90.25 seconds) * .006/60 * 1.15.

    def complete(**kwargs):
        current = spending.summary(job)
        assert current['reserved_usd'] == pytest.approx(expected)
        assert current['spent_usd'] == 0
        return SimpleNamespace(
            segments=[SimpleNamespace(text='A complete explanation.', start=0, end=90)],
            language='pt')

    paid_audio.api.audio.transcriptions.create.side_effect = complete
    rows, language = youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key',
                                             financial_job=job)
    assert language == 'pt'
    assert rows[0]['duration'] == 90
    paid_audio.factory.assert_called_once_with(api_key='test-key', timeout=180, max_retries=0)
    assert spending.summary(job)['spent_usd'] == pytest.approx(expected)
    assert spending.summary(job)['reserved_usd'] == 0
    assert db.get_job(job['id'])['usage'][0]['reservation_id']
    assert db.get_job(job['id'])['usage'][0]['model'] == 'whisper-1'


def test_paid_transcription_without_article_does_not_download_or_call_provider(paid_audio):
    with pytest.raises(spending.SpendLimitExceeded, match='vinculada'):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key')
    paid_audio.download.assert_not_called()
    paid_audio.factory.assert_not_called()


def test_paid_transcription_cannot_bypass_existing_article_spend(job, paid_audio):
    db.set_setting('editorial_profile', {'max_spend_usd': .50})
    job['usage'] = [{'stage': 'writer', 'estimated_usd': .499, 'response_id': 'old-paid-response'}]
    db.save_job(job)
    with pytest.raises(spending.SpendLimitExceeded):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key', financial_job=job)
    paid_audio.api.audio.transcriptions.create.assert_not_called()
    assert spending.summary(job)['spent_usd'] == pytest.approx(.499)


def test_fallback_budget_error_retains_financial_diagnostic_for_sources(job, paid_audio, monkeypatch):
    db.set_setting('editorial_profile', {'max_spend_usd': .50})
    db.set_setting('transcript_provider', 'supadata')
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': 'https://youtu.be/' + vid})
    monkeypatch.setattr(youtube, 'get_secret', lambda name: 'test-key' if name == 'openai_api_key' else '')
    job['usage'] = [{'estimated_usd': .499, 'response_id': 'old-paid-response'}]
    db.save_job(job)
    with pytest.raises(transcripts.SourceError) as failure:
        youtube.extract('https://youtu.be/abcdefghijk', 'v1', audio_fallback=True, financial_job=job)
    assert failure.value.diagnostic['code'] == 'financial_budget'
    assert failure.value.diagnostic['provider'] == 'Transcrição de áudio OpenAI'
    assert 'nenhuma chamada' in str(failure.value).lower()
    paid_audio.api.audio.transcriptions.create.assert_not_called()


@pytest.mark.parametrize('failure,retained', [(400, False), (500, True), ('timeout', True)])
def test_paid_transcription_rejection_releases_but_uncertain_failure_retains(job, paid_audio, failure, retained):
    if failure == 'timeout':
        error = httpx.ReadTimeout('private provider payload')
    else:
        response = httpx.Response(failure, request=httpx.Request('POST', 'https://example.test/transcription'))
        error = APIStatusError('private provider payload', response=response, body={})
    paid_audio.api.audio.transcriptions.create.side_effect = error
    with pytest.raises(type(error)):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key', financial_job=job)
    current = spending.summary(db.get_job(job['id']))
    assert current['reserved_usd'] == pytest.approx(.010465 if retained else 0)
    assert current['spent_usd'] == 0
    assert db.get_job(job['id'])['usage'] == []


def test_paid_transcription_timeout_cannot_renew_budget_on_retry(job, paid_audio):
    db.set_setting('editorial_profile', {'max_spend_usd': .015})
    paid_audio.api.audio.transcriptions.create.side_effect = httpx.ReadTimeout('uncertain request')
    with pytest.raises(httpx.ReadTimeout):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key', financial_job=job)
    with pytest.raises(spending.SpendLimitExceeded):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key', financial_job=db.get_job(job['id']))
    assert paid_audio.api.audio.transcriptions.create.call_count == 1


@pytest.mark.parametrize('duration', [0, float('nan'), float('inf'), 2701])
def test_invalid_audio_duration_cannot_start_paid_transcription(job, paid_audio, duration):
    paid_audio.probe.return_value = duration
    with pytest.raises(transcripts.SourceError):
        youtube.transcribe_audio('https://youtu.be/abcdefghijk', 'test-key', financial_job=job)
    paid_audio.factory.assert_not_called()
    assert spending.summary(job)['spent_usd'] == spending.summary(job)['reserved_usd'] == 0


def test_default_local_transcription_does_not_reserve_money_even_with_fallback_enabled(job, paid_audio, monkeypatch):
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': 'https://youtu.be/' + vid})
    monkeypatch.setattr(local_audio, 'download', Mock(return_value='audio.wav'))
    monkeypatch.setattr(local_audio, 'transcribe', lambda path, progress: {
        'rows': [{'text': 'This explanation includes all of the context and useful details needed to understand the source video.',
                  'start': 0, 'duration': 9}], 'language': 'pt', 'model': 'small', 'duration': 15,
        'warnings': [], 'completed_at': 1000000, 'audio_sha256': 'a' * 64})
    source = youtube.extract('https://youtu.be/abcdefghijk', 'v1', audio_fallback=True, financial_job=job)
    assert source['provider'] == 'Whisper local · YouTube'
    paid_audio.factory.assert_not_called()
    assert spending.summary(job)['spent_usd'] == spending.summary(job)['reserved_usd'] == 0
