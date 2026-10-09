from app import local_audio, transcripts


def test_diagnostics_scoped_authenticated_and_excludes_content(authed, job):
    video = 'abcdefghijk'
    audio = local_audio.root() / transcripts.fingerprint('youtube-audio-v1:' + video) / 'audio.m4a'
    for index, source in enumerate((audio, local_audio.root() / 'unrelated' / 'audio.m4a')):
        folder = local_audio.safe_path(str(index) * 64)
        local_audio.atomic_json(folder / 'request.json', {'audio': str(source), 'model': 'small', 'duration': 193})
        local_audio.atomic_json(folder / 'error.json', {'code': 'local_engine', 'exception': 'ValueError',
            'message': 'secret-worker-message', 'frames': [{'file': '/app/app/whisper_worker.py', 'line': 58, 'function': 'run'}]})
        local_audio.atomic_json(folder / 'checkpoint.json', {'rows': [{'text': 'private-transcript'}], 'completed_blocks': 1})
    url = f'/api/jobs/{job["id"]}/sources/{video}/diagnostics'
    response = authed.get(url)
    assert response.status_code == 200
    result = response.json()
    assert len(result['attempts']) == 1
    assert result['attempts'][0]['exception'] == 'ValueError'
    assert result['attempts'][0]['frames'][0]['file'] == 'whisper_worker.py'
    assert result['attempts'][0]['saved_segments'] == 1
    assert 'private-transcript' not in response.text
    assert 'secret-worker-message' not in response.text
    assert str(audio) not in response.text
    assert authed.get(url.replace(video, 'lmnopqrstuv')).status_code == 404
    authed.post('/api/logout')
    assert authed.get(url).status_code == 401
