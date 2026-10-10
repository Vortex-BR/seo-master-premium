"""Paid transcript tickets survive an integrity upgrade without resubmission."""
from copy import deepcopy
import json
import time
from unittest.mock import Mock

import httpx

from app import db, local_audio, transcripts, youtube


TEXT = ('A explicação técnica mantém as condições e as ressalvas do procedimento. '
        'Cada comparação pode ser conferida na transcrição original. ')
VIDEO = 'abcdefghijk'
KEY = 'isolated-supadata-test-key'


def save_record(record):
    identifier = transcripts.request_key(VIDEO, KEY, 'native')
    with db.connect() as conn:
        conn.execute('INSERT INTO transcript_requests VALUES (?,?)',
                     (identifier, json.dumps(record, ensure_ascii=False)))
    return identifier


def obtain():
    return transcripts.supadata(VIDEO, 'https://youtu.be/' + VIDEO, KEY, 'native',
                               transcripts.Deadline(10), lambda *args: None)


def test_legacy_paid_cache_keeps_text_and_positive_times_but_exposes_ambiguous_zero(client, monkeypatch):
    record = {'state': 'completed', 'completed_at': time.time(), 'ticket': 'already-paid',
              'result': {'language': 'pt', 'rows': [
                  {'text': TEXT, 'start': 10, 'duration': 0},
                  {'text': TEXT + 'Outra fala.', 'start': 200, 'duration': 5}]}}
    identifier = save_record(record)
    network = Mock(side_effect=AssertionError('No new paid or polling request is allowed.'))
    monkeypatch.setattr(transcripts.httpx, 'Client', network)
    first, second = obtain(), obtain()
    assert first == second
    assert first['provider_cache_legacy'] is True
    assert first['provider_adapter_version'] == 'supadata-rows-legacy'
    segments = youtube.segment_rows(first['rows'], 'v1')
    assert segments[0]['text'] == TEXT.strip()
    assert (segments[0]['start'], segments[0]['end'], segments[0]['duration']) == (10, None, None)
    assert (segments[1]['start'], segments[1]['end']) == (200, 205)
    assert all(s['normalization_warnings'][0]['code'] == 'legacy_provider_metadata' for s in segments)
    assert transcripts.request_record(identifier) == record
    network.assert_not_called()


def test_current_paid_cache_retains_true_zero_duration_and_provider_metadata(client, monkeypatch):
    result = transcripts.normalize({'lang': 'pt', 'content': [
        {'id': 'provider-cue', 'text': TEXT, 'offset': 10000, 'duration': 0,
         'speaker': 'voice-a', 'confidence': .4}]})
    record = {'state': 'completed', 'completed_at': time.time(), 'result': deepcopy(result)}
    identifier = save_record(record)
    network = Mock(side_effect=AssertionError('Completed tickets must be reused.'))
    monkeypatch.setattr(transcripts.httpx, 'Client', network)
    cached = obtain()
    assert cached['provider_adapter_version'] == transcripts.SUPADATA_ROWS_VERSION
    assert not cached.get('provider_cache_legacy')
    segments = youtube.segment_rows(cached['rows'], 'v1')
    assert (segments[0]['start'], segments[0]['end']) == (10, 10)
    assert segments[0]['speaker'] == 'voice-a' and segments[0]['confidence'] == .4
    assert segments[0]['original_cues'][0]['original_id'] == 'provider-cue'
    assert transcripts.request_record(identifier) == record
    network.assert_not_called()


def test_pending_old_ticket_is_polled_once_without_a_new_paid_submission(client, monkeypatch):
    save_record({'state': 'pending', 'ticket': 'existing-ticket', 'created_at': time.time()})
    calls = []

    def reply(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'status': 'completed', 'result': {
            'lang': 'pt', 'content': [{'text': TEXT, 'offset': 10000, 'duration': 15000,
                                     'id': 'fresh-cue', 'speaker': 'voice-a', 'confidence': .7}]}})

    original_client = httpx.Client
    monkeypatch.setattr(transcripts.httpx, 'Client', lambda **kwargs:
                        original_client(transport=httpx.MockTransport(reply), **kwargs))
    result = obtain()
    assert result['provider_adapter_version'] == transcripts.SUPADATA_ROWS_VERSION
    assert result['rows'][0]['id'] == 'fresh-cue'
    assert (result['rows'][0]['start'], result['rows'][0]['duration']) == (10, 15)
    assert calls == [('GET', '/v1/transcript/existing-ticket')]
    assert obtain() == result
    assert calls == [('GET', '/v1/transcript/existing-ticket')]


def test_legacy_cache_provenance_reaches_new_source_without_inference_or_network(client, monkeypatch):
    identifier = save_record({'state': 'completed', 'completed_at': time.time(), 'result': {
        'language': 'pt', 'rows': [{'text': TEXT, 'start': 10, 'duration': 0}]}})
    original = transcripts.request_record(identifier)
    monkeypatch.setattr(transcripts, 'configuration', lambda: {'provider': 'supadata', 'mode': 'native', 'timeout': 60})
    monkeypatch.setattr(youtube, 'get_secret', lambda name: KEY if name == 'supadata_api_key' else '')
    monkeypatch.setattr(youtube, 'metadata', youtube.base_metadata)
    monkeypatch.setattr(local_audio, 'configured_proxies', lambda: [])
    network = Mock(side_effect=AssertionError('No external request is allowed.'))
    monkeypatch.setattr(transcripts.httpx, 'Client', network)
    source = youtube.extract('https://youtu.be/' + VIDEO, 'v1')
    assert source['normalization_version'] == youtube.NORMALIZATION_VERSION
    assert source['provider_adapter_version'] == 'supadata-rows-legacy'
    assert source['provider_cache_legacy'] is True
    assert source['segments'][0]['confidence'] is None
    assert source['segments'][0]['speaker'] is None
    assert source['segments'][0]['end'] is None
    assert transcripts.request_record(identifier) == original
    network.assert_not_called()
