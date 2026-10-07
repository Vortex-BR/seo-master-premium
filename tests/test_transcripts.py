from copy import deepcopy
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import httpx
import pytest

from app import db, local_audio, pipeline, source_cache, transcripts, whisper_worker, youtube


TEXT = 'Esta transcrição de áudio contém uma explicação completa, com contexto, condições e referências que podem ser conferidas no material original.'


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1000000
        def time(self): return self.now
        def monotonic(self): return self.now
        def sleep(self, seconds): self.now += seconds
    value = Clock()
    monkeypatch.setattr(transcripts, 'time', value)
    return value


def remote(monkeypatch, handler):
    original = httpx.Client
    calls = []
    def dispatch(request):
        calls.append(request.url.path)
        return handler(request)
    transport = httpx.MockTransport(dispatch)
    monkeypatch.setattr(transcripts.httpx, 'Client', lambda **kwargs: original(transport=transport, **kwargs))
    return calls


def supadata(deadline=10):
    return transcripts.supadata('abcdefghijk', 'https://youtu.be/abcdefghijk', 'test-transcript-secret',
                                'native', transcripts.Deadline(deadline), lambda *args: None)


def test_immediate_remote_transcript_is_reused_and_retains_original_time(client, monkeypatch, clock):
    calls = remote(monkeypatch, lambda r: httpx.Response(200, json={'content': [{'text': TEXT, 'offset': 1234, 'duration': 4000}], 'lang': 'pt'}))
    result = supadata()
    clock.sleep(20)
    assert supadata() == result
    assert result['rows'][0]['start'] == 1.234
    assert calls == ['/v1/transcript']
    with db.connect() as conn:
        assert 'test-transcript-secret' not in str([dict(row) for row in conn.execute('SELECT * FROM transcript_requests')])


def test_pending_ticket_survives_timeout_and_resume_without_new_request(client, monkeypatch, clock):
    completed = False
    def respond(request):
        if request.url.path == '/v1/transcript':
            return httpx.Response(202, json={'jobId': 'durable-ticket'})
        return httpx.Response(200, json={'status': 'completed', 'content': TEXT, 'lang': 'pt'} if completed else {'status': 'active'})
    calls = remote(monkeypatch, respond)
    with pytest.raises(transcripts.SourceError) as first:
        supadata(3)
    assert first.value.diagnostic['pending']
    record = transcripts.request_record(transcripts.request_key('abcdefghijk', 'test-transcript-secret', 'native'))
    assert record['ticket'] == 'durable-ticket'
    completed = True
    assert supadata()['rows'][0]['text'] == TEXT
    assert calls.count('/v1/transcript') == 1
    assert calls.count('/v1/transcript/durable-ticket') == 2


def test_ambiguous_submission_is_never_sent_twice(client, monkeypatch, clock):
    def timeout(request): raise httpx.ReadTimeout('private upstream details', request=request)
    calls = remote(monkeypatch, timeout)
    for _ in range(2):
        with pytest.raises(transcripts.SourceError) as failure:
            supadata()
        assert failure.value.diagnostic['uncertain']
        assert 'private upstream details' not in str(failure.value)
    assert calls == ['/v1/transcript']


@pytest.mark.parametrize('status,code', [(401, 'credentials'), (402, 'quota'), (403, 'restricted'),
                                       (404, 'not_found'), (206, 'no_captions'), (429, 'rate_limit')])
def test_remote_errors_are_classified_and_do_not_repeat_blindly(client, monkeypatch, clock, status, code):
    calls = remote(monkeypatch, lambda r: httpx.Response(status, json={'message': 'Never expose this key'}, headers={'Retry-After': '30'}))
    for _ in range(2):
        with pytest.raises(transcripts.SourceError) as failure:
            supadata()
        assert failure.value.diagnostic['code'] == code
        assert 'Never expose' not in str(failure.value)
    assert len(calls) == 1


def test_polling_429_preserves_ticket_and_respects_retry_after(client, monkeypatch, clock):
    limited = True
    def respond(request):
        if request.url.path == '/v1/transcript': return httpx.Response(202, json={'jobId': 'ticket'})
        if limited: return httpx.Response(429, headers={'Retry-After': '25'})
        return httpx.Response(200, json={'status': 'completed', 'result': {'content': TEXT, 'lang': 'pt'}})
    calls = remote(monkeypatch, respond)
    with pytest.raises(transcripts.SourceError) as failure: supadata()
    assert failure.value.diagnostic['pending']
    with pytest.raises(transcripts.SourceError): supadata()
    assert len(calls) == 2
    clock.sleep(26); limited = False
    assert supadata()['language'] == 'pt'
    assert calls.count('/v1/transcript') == 1


@pytest.mark.parametrize('content', [None, {}, [{'text': TEXT, 'offset': -1}],
                                    [{'text': TEXT, 'duration': float('nan')}], [{'text': 25}], []])
def test_invalid_remote_payload_is_refused(content):
    with pytest.raises(transcripts.SourceError): transcripts.normalize({'content': content})


def test_local_audio_is_default_and_never_calls_captions_or_paid_providers(client, monkeypatch):
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': 'https://youtu.be/' + vid})
    download = Mock(return_value='audio.wav')
    monkeypatch.setattr(local_audio, 'download', download)
    monkeypatch.setattr(local_audio, 'transcribe', lambda path, progress: {'rows': [{'text': TEXT, 'start': 2, 'duration': 9}], 'language': 'pt', 'model': 'small', 'duration': 15, 'warnings': [], 'completed_at': 1000000, 'audio_sha256': 'a'*64})
    captions, paid = Mock(), Mock()
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', captions)
    monkeypatch.setattr(transcripts, 'supadata', paid)
    monkeypatch.setattr(youtube, 'get_secret', lambda key: '')
    source = youtube.extract('https://youtu.be/abcdefghijk', 'v2')
    assert source['medium'] == 'audio'
    assert source['segments'][0]['id'] == 'v2s1'
    assert source['segments'][0]['start'] == 2
    assert source['provider'] == 'Whisper local · YouTube'
    captions.assert_not_called(); paid.assert_not_called()


def test_download_block_does_not_silently_use_captions(client, monkeypatch):
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': 'https://youtu.be/' + vid})
    monkeypatch.setattr(local_audio, 'download', Mock(side_effect=transcripts.SourceError('Download blocked', code='ip_blocked')))
    captions = Mock(); monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', captions)
    with pytest.raises(transcripts.SourceError) as failure: youtube.extract('https://youtu.be/abcdefghijk', 'v1')
    assert failure.value.diagnostic['code'] == 'ip_blocked'
    assert failure.value.info['video_id'] == 'abcdefghijk'
    captions.assert_not_called()


def test_audio_routes_are_paused_across_videos_without_leaking_credentials(client, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    monkeypatch.setattr(local_audio.shutil, 'which', lambda value: '/bin/node')
    download = Mock(return_value=SimpleNamespace(returncode=1, stderr=b'Sign in to confirm you are not a bot'))
    monkeypatch.setattr(local_audio.subprocess, 'run', download)
    for video in ('abcdefghijk', 'lmnopqrstuv'):
        with pytest.raises(transcripts.SourceError):
            local_audio.download(video, 'https://youtu.be/'+video, ['http://private-user:private-password@proxy.test:80'], transcripts.Deadline(60), lambda *args: None, [])
    assert download.call_count == 1
    with db.connect() as conn:
        saved = str([dict(row) for row in conn.execute('SELECT * FROM transcript_routes')])
    assert 'private-password' not in saved
    assert 'private-user' not in saved


def test_audio_routes_have_independent_health_from_caption_connections(client, monkeypatch):
    failure = transcripts.SourceError('Captions blocked', code='ip_blocked')
    transcripts.pause_route(transcripts.route_key('youtube', 'direct'), failure, 300)
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    monkeypatch.setattr(local_audio.shutil, 'which', lambda value: '/bin/node')
    def download(command, **kwargs):
        template = command[command.index('-o')+1]
        from pathlib import Path
        Path(template.replace('%(ext)s', 'm4a')).write_bytes(b'audio')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local_audio.subprocess, 'run', download)
    assert local_audio.download('abcdefghijk', 'https://youtu.be/abcdefghijk', [], transcripts.Deadline(60), lambda *args: None, []).suffix == '.m4a'


def test_cache_does_not_replace_audio_with_captions_or_reuse_uploaded_origins(job):
    source = deepcopy(job['sources'][0])
    source.update(provider='Legendas do YouTube', extracted_at=db.now())
    source['segments'][0]['text'] = TEXT
    job['sources'] = [source]; db.save_job(job)
    assert source_cache.find_recent('abcdefghijk', 'v1', 'another', audio_only=True) is None
    source.update(provider='Whisper local · arquivo enviado', medium='audio')
    db.save_job(job)
    assert source_cache.find_recent('abcdefghijk', 'v1', 'another', audio_only=True) is None


def wav_bytes(seconds=2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * int(16000 * seconds))
    return output.getvalue()


def test_audio_upload_is_private_bounded_and_only_queues_transcription(authed, job, monkeypatch):
    executor = Mock(); monkeypatch.setattr(pipeline, 'executor', executor)
    response = authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename=audio.wav', content=wav_bytes(), headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 202, response.text
    saved = db.get_job(job['id'])
    assert saved['status'] == 'queued'
    upload = saved['audio_uploads']['abcdefghijk']
    assert local_audio.uploaded_path(upload).exists()
    assert saved['article_needs_generation']
    assert saved['sources'][0]['status'] == 'uploaded'
    assert saved['usage'] == []
    executor.submit.assert_called_once_with(pipeline.run, job['id'], 'extract')
    assert 'original.wav' not in response.text


@pytest.mark.parametrize('filename,payload,status', [('bad.txt', b'file', 400), ('bad.wav', b'not audio', 400), ('bad.m3u', b'http://127.0.0.1', 400)])
def test_invalid_upload_leaves_sources_intact(authed, job, filename, payload, status):
    response = authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename={filename}', content=payload)
    assert response.status_code == status
    assert db.get_job(job['id'])['sources'] == job['sources']
    assert not list(local_audio.root().glob('*/original*'))


def test_audio_upload_requires_auth_and_rejects_size_before_writing(client, authed, job):
    endpoint=f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename=audio.wav'
    assert authed.post(endpoint, content=b'', headers={'Content-Length': str(300*1024*1024)}).status_code == 413
    authed.post('/api/logout')
    assert client.post(endpoint, content=wav_bytes()).status_code == 401
    assert not list(local_audio.root().glob('*/original*'))


def test_missing_audio_does_not_reach_writer_and_preserves_other_videos(job, monkeypatch):
    job['brief']['urls'].append('https://youtu.be/lmnopqrstuv')
    db.save_job(job)
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': 'https://youtu.be/'+vid})
    monkeypatch.setattr(youtube, 'extract', Mock(side_effect=transcripts.SourceError('Áudio bloqueado', code='ip_blocked')))
    writer=Mock(); monkeypatch.setattr(pipeline.engine, 'run', writer)
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='sources_unavailable'
    assert saved['sources'][0]==job['sources'][0]
    assert saved['sources'][1]['extraction']['diagnostic']['code']=='ip_blocked'
    writer.assert_not_called()


def test_worker_resumes_completed_blocks_and_keeps_global_timestamps(tmp_path, monkeypatch):
    folder=tmp_path/'work';folder.mkdir()
    local_audio.atomic_json(folder/'checkpoint.json', {'rows':[{'text':TEXT,'start':0,'duration':8}], 'completed_blocks':1, 'languages':['pt'], 'warnings':[]})
    segment=SimpleNamespace(text=TEXT, start=.2, end=1.8, avg_logprob=-1.5, no_speech_prob=.1)
    model=Mock();model.transcribe.return_value=(iter([segment]),SimpleNamespace(language='pt'))
    import faster_whisper
    constructor=Mock(return_value=model);monkeypatch.setattr(faster_whisper,'WhisperModel',constructor)
    def decode(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(wav_bytes(2))
        return SimpleNamespace(returncode=0)
    decoder=Mock(side_effect=decode);monkeypatch.setattr(whisper_worker.subprocess,'run',decoder)
    request={'directory':str(folder),'audio':str(tmp_path/'audio.wav'),'format':'wav','models':str(tmp_path/'models'),
             'duration':602,'model':'small','device':'cpu','compute_type':'int8','threads':2,'audio_sha256':'a'*64}
    assert whisper_worker.run(request)==0
    result=local_audio.load_json(folder/'result.json')
    assert len(result['rows'])==2
    assert result['rows'][1]['start']==600.2
    assert result['warnings'][0]['start']==600.2
    assert result['audio_sha256']=='a'*64
    assert decoder.call_count==1
    assert decoder.call_args.args[0][decoder.call_args.args[0].index('-ss')+1]=='600'


def test_remote_reset_requires_confirmation_and_cannot_replace_pending_request(authed, job, monkeypatch):
    job['sources'][0]['status']='error';db.save_job(job)
    monkeypatch.setattr(transcripts,'get_secret',lambda name:'test-transcript-secret')
    key=transcripts.request_key('abcdefghijk','test-transcript-secret','native')
    _,record=transcripts.claim_request(key,'abcdefghijk','native')
    record.update(state='pending',ticket='pending-ticket');transcripts.save_request(key,record)
    endpoint=f'/api/jobs/{job["id"]}/sources/abcdefghijk/reset-transcription'
    assert authed.post(endpoint,json={'confirm_new_request':False}).status_code==400
    assert authed.post(endpoint,json={'confirm_new_request':True}).status_code==400
    assert transcripts.request_record(key)['ticket']=='pending-ticket'


def test_low_confidence_audio_used_in_article_remains_a_review_issue(job, newsroom_ai):
    job['sources'][0].update(medium='audio', transcription_warnings=[{'start':12,'end':14,'reason':'Baixa confiança.'}])
    db.save_job(job)
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='needs_review', saved.get('error')
    assert any(f['origin']=='pending_issue' and f['source_ids']==['v1s1'] for f in saved['review']['findings'])
    from app.editorial import store
    issue=next(i for i in store.issues(saved) if i['origin']=='transcription')
    store.resolve_issue(saved,issue['id'],'Conferi o trecho no áudio original e confirmei a fala e os termos registrados.',['v1s1'])
    pipeline.run(job['id'],'review')
    assert not any(f.get('origin')=='pending_issue' for f in db.get_job(job['id'])['review']['findings'])


def test_uploaded_audio_does_not_need_a_youtube_connection(client, monkeypatch, tmp_path):
    metadata=Mock(side_effect=AssertionError('YouTube must not be contacted for uploaded audio'))
    monkeypatch.setattr(youtube,'metadata',metadata)
    monkeypatch.setattr(local_audio,'uploaded_path',lambda upload:tmp_path/'original.wav')
    monkeypatch.setattr(local_audio,'transcribe',lambda *args:{'rows':[{'text':TEXT,'start':0,'duration':2}],
         'language':'pt','duration':2,'model':'small','audio_sha256':'a'*64,'warnings':[],'completed_at':1000000})
    assert youtube.extract('https://youtu.be/abcdefghijk','v1',uploaded={'id':'a'*32})['input_origin']=='uploaded_audio'
    metadata.assert_not_called()


def test_worker_can_be_stopped_with_its_process_group():
    import os
    import subprocess
    import sys
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
         start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    try:
        local_audio.stop_worker(process)
        assert process.poll() is not None
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_upload_size_is_enforced_without_content_length(authed, job, monkeypatch):
    original=local_audio.configuration()
    monkeypatch.setattr(local_audio,'configuration',lambda:{**original,'max_mb':1})
    def chunks():
        yield wav_bytes()
        yield b'\0'*(1024*1024)
    response=authed.post(f'/api/jobs/{job["id"]}/sources/abcdefghijk/audio?filename=audio.wav',content=chunks())
    assert response.status_code==413
    assert db.get_job(job['id'])['sources']==job['sources']
    assert not list(local_audio.root().glob('*/original*'))


def test_human_transcript_correction_replaces_old_recognition_flags(authed, job):
    job['sources'][0].update(medium='audio',transcription_warnings=[{'start':12,'reason':'Baixa confiança.'}],
                            transcription_model='small',input_origin='uploaded_audio')
    db.save_job(job)
    response=authed.post(f'/api/jobs/{job["id"]}/source',json={'video_id':'abcdefghijk','text':TEXT})
    assert response.status_code==200
    source=db.get_job(job['id'])['sources'][0]
    assert source['provider']=='Transcrição fornecida pelo usuário'
    assert 'transcription_warnings' not in source
    assert 'medium' not in source
