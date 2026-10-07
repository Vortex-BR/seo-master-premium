from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import audio_routes, db, local_audio, transcripts
from app.security import save_secret


@pytest.fixture
def ready(client, monkeypatch):
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'javascript': True})
    db.set_setting('youtube_connection_mode', 'proxy')


def downloaded(command, **kwargs):
    Path(command[command.index('-o')+1].replace('%(ext)s', 'm4a')).write_bytes(b'audio')
    return SimpleNamespace(returncode=0)


def test_successful_access_test_is_prioritized_after_restart_for_download(ready, monkeypatch):
    proxies = ['http://user:private-password@proxy1.test:80', 'http://proxy2.test:80']
    save_secret('youtube_proxy_urls', '\n'.join(proxies))
    runner = Mock(side_effect=[SimpleNamespace(returncode=1, stderr=b'Sign in to confirm you are not a bot'),
                              SimpleNamespace(returncode=0, stdout=b'abcdefghijk\n')])
    monkeypatch.setattr(local_audio.subprocess, 'run', runner)
    assert local_audio.access_test('abcdefghijk')['ok']
    # Restore the first route's availability and reconnect to the persistent database.
    transcripts.healthy_route(audio_routes.key(proxies[0]))
    db.init()
    runner = Mock(side_effect=downloaded)
    monkeypatch.setattr(local_audio.subprocess, 'run', runner)
    local_audio.download('abcdefghijk', 'https://youtu.be/abcdefghijk', proxies, transcripts.Deadline(30), lambda *a: None, [])
    command = runner.call_args.args[0]
    assert command[command.index('--proxy')+1] == proxies[1]
    with db.connect() as conn:
        history = str([dict(row) for row in conn.execute('SELECT * FROM audio_route_history')])
    assert 'private-password' not in history and 'proxy1.test' not in history


def test_download_finds_working_route_after_first_three_fail(ready, monkeypatch):
    proxies = [f'http://proxy{i}.test:80' for i in range(6)]
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if len(commands) <= 4:
            return SimpleNamespace(returncode=1, stderr=b'Sign in to confirm you are not a bot')
        return downloaded(command)
    monkeypatch.setattr(local_audio.subprocess, 'run', run)
    attempts=[]
    assert local_audio.download('abcdefghijk','https://youtu.be/abcdefghijk',proxies,transcripts.Deadline(60),lambda *a:None,attempts).exists()
    assert len(commands)==5 and attempts[-1]['outcome']=='ok'
    assert audio_routes.ordered(proxies)[0]==proxies[4]


def test_negative_results_and_old_successes_do_not_keep_priority(ready, monkeypatch):
    now=[10000.0]
    monkeypatch.setattr(audio_routes.time,'time',lambda:now[0])
    first, second='http://proxy1.test', 'http://proxy2.test'
    audio_routes.record(second,'attempt'); audio_routes.record(second,'success')
    assert audio_routes.ordered([first,second])==[second,first]
    now[0]+=1
    audio_routes.record(second,'failure')
    assert audio_routes.ordered([first,second])==[first,second]
    now[0]+=1
    audio_routes.record(second,'success')
    now[0]+=audio_routes.SUCCESS_TTL+1
    assert audio_routes.ordered([first,second])==[first,second]
    assert audio_routes.ordered([second,None,first])[0] is None


def test_access_test_continues_beyond_three_within_its_deadline(ready, monkeypatch):
    save_secret('youtube_proxy_urls','\n'.join(f'http://proxy{i}.test' for i in range(6)))
    responses=[SimpleNamespace(returncode=1,stderr=b'not a bot') for _ in range(4)]
    responses.append(SimpleNamespace(returncode=0,stdout=b'abcdefghijk\n'))
    runner=Mock(side_effect=responses); monkeypatch.setattr(local_audio.subprocess,'run',runner)
    result=local_audio.access_test('abcdefghijk')
    assert result['ok'] and result['connections_tested']==5
    assert result['connections_total']==6 and result['connections_unchecked']==1


def test_deadline_stops_pool_search_and_next_search_prefers_untried_routes(ready, monkeypatch):
    proxies=[f'http://proxy{i}.test' for i in range(10)]
    deadline=transcripts.Deadline(60)
    commands=[]
    def run(command,**kwargs):
        commands.append(command)
        deadline.end=0
        return SimpleNamespace(returncode=1,stderr=b'not a bot')
    monkeypatch.setattr(local_audio.subprocess,'run',run)
    with pytest.raises(transcripts.SourceError) as failure:
        local_audio.download('abcdefghijk','https://youtu.be/abcdefghijk',proxies,deadline,lambda *a:None,[])
    assert len(commands)==1
    assert failure.value.diagnostic['connections_tested']==1
    assert failure.value.diagnostic['connections_available']==10
    assert audio_routes.ordered(proxies)[-1]==proxies[0]


def test_expired_deadline_does_not_start_a_download(ready, monkeypatch):
    runner=Mock();monkeypatch.setattr(local_audio.subprocess,'run',runner)
    with pytest.raises(transcripts.SourceError) as error:
        local_audio.download('abcdefghijk','https://youtu.be/abcdefghijk',['http://proxy.test'],transcripts.Deadline(0),lambda *a:None,[])
    assert error.value.diagnostic['code']=='timeout'
    runner.assert_not_called()


def test_settings_show_exact_saved_proxy_count(authed):
    save_secret('youtube_proxy_urls','\n'.join(f'http://proxy{i}.test' for i in range(100)))
    response=authed.get('/api/settings')
    assert response.json()['transcription_status']['proxy_count']==100
    assert 'proxy99.test' not in response.text
