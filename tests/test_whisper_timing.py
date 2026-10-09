import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import local_audio, whisper_worker
from test_transcripts import wav_bytes


def segment(text='Explicação do vídeo.', start=0, end=1):
    return SimpleNamespace(text=text, start=start, end=end, avg_logprob=-.2, no_speech_prob=.1)


def output(*segments):
    return iter(segments), SimpleNamespace(language='pt')


def test_invalid_final_time_realigns_only_block_without_duplicating_rows(tmp_path):
    model = Mock()
    model.transcribe.side_effect = [output(segment('Primeira tentativa.'), segment(start=1, end=4)),
                                     output(segment('Texto alinhado.', start=.1, end=1.9))]
    rows, warnings, language, aligned = whisper_worker.transcribe_block(
        model, tmp_path/'block.wav', 2, 600, 602, tmp_path, 120000)
    assert rows == [{'text': 'Texto alinhado.', 'start': 600.1, 'duration': 1.7999999999999998}]
    assert language == 'pt' and aligned is True and warnings == []
    assert model.transcribe.call_count == 2
    assert 'word_timestamps' not in model.transcribe.call_args_list[0].kwargs
    assert model.transcribe.call_args_list[1].kwargs['word_timestamps'] is True


@pytest.mark.parametrize('start,end', [(math.nan, 1), (0, math.inf), (-2, 1), (1, .5), (3, 4), (1, 5)])
def test_invalid_alignment_stops_after_one_retry_and_keeps_checkpoint(tmp_path, start, end):
    checkpoint = {'rows': [{'text': 'Bloco salvo.', 'start': 0, 'duration': 5}], 'completed_blocks': 1}
    local_audio.atomic_json(tmp_path/'checkpoint.json', checkpoint)
    model = Mock()
    model.transcribe.side_effect = [output(segment(start=start, end=end)), output(segment(start=start, end=end))]
    assert whisper_worker.transcribe_block(model, tmp_path/'block.wav', 2, 600, 602, tmp_path, 120000) is None
    assert model.transcribe.call_count == 2
    assert local_audio.load_json(tmp_path/'error.json')['code'] == 'audio_timestamps'
    assert local_audio.load_json(tmp_path/'checkpoint.json') == checkpoint
    assert not (tmp_path/'result.json').exists()


def test_timestamp_rounding_stays_inside_audio_without_retry(tmp_path):
    model = Mock()
    model.transcribe.return_value = output(segment(start=-.02, end=2.02))
    rows, _, _, aligned = whisper_worker.transcribe_block(model, tmp_path/'block.wav', 2, 0, 2, tmp_path, 100)
    assert rows[0]['start'] == 0 and rows[0]['duration'] == 2
    assert aligned is False and model.transcribe.call_count == 1


def test_character_limit_does_not_start_alignment_retry(tmp_path):
    model = Mock()
    model.transcribe.return_value = output(segment('Texto além do limite.'))
    assert whisper_worker.transcribe_block(model, tmp_path/'block.wav', 2, 0, 2, tmp_path, 1) is None
    assert model.transcribe.call_count == 1
    assert local_audio.load_json(tmp_path/'error.json')['code'] == 'source_limit'


def test_worker_resumes_and_commits_successfully_realigned_block(tmp_path, monkeypatch):
    folder = tmp_path/'work'
    folder.mkdir()
    saved = {'text': 'Bloco anterior.', 'start': 0, 'duration': 5}
    local_audio.atomic_json(folder/'checkpoint.json', {'rows': [saved], 'completed_blocks': 1, 'languages': ['pt'], 'warnings': []})
    model = Mock()
    model.transcribe.side_effect = [output(segment(start=1, end=5)), output(segment(start=.2, end=1.8))]
    import faster_whisper
    monkeypatch.setattr(faster_whisper, 'WhisperModel', Mock(return_value=model))
    def decode(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(wav_bytes(2))
        return SimpleNamespace(returncode=0)
    decoder = Mock(side_effect=decode)
    monkeypatch.setattr(whisper_worker.subprocess, 'run', decoder)
    request = {'directory': str(folder), 'audio': str(tmp_path/'audio.wav'), 'format': 'wav', 'models': str(tmp_path/'models'),
               'duration': 602, 'model': 'small', 'device': 'cpu', 'compute_type': 'int8', 'threads': 2}
    assert whisper_worker.run(request) == 0
    result = local_audio.load_json(folder/'result.json')
    assert result['rows'][0] == saved
    assert len(result['rows']) == 2
    assert result['rows'][1]['start'] == 600.2
    assert result['realigned_blocks'] == [1]
    assert decoder.call_count == 1
    assert local_audio.load_json(folder/'checkpoint.json')['completed_blocks'] == 2


def test_supervisor_clears_old_error_and_progress_before_start(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, 'data_dir', lambda: tmp_path)
    audio = tmp_path/'audio.wav'
    audio.write_bytes(wav_bytes(2))
    folder = tmp_path/'worker'
    folder.mkdir()
    monkeypatch.setattr(local_audio, 'safe_path', lambda _: folder)
    monkeypatch.setattr(local_audio, 'readiness', lambda: {'installed': True, 'ffmpeg': True})
    monkeypatch.setattr(local_audio, 'probe', lambda _: 2)
    monkeypatch.setattr(local_audio, 'configuration', lambda: {'model':'small', 'device':'cpu', 'compute_type':'int8', 'revision':None, 'threads':2, 'timeout':300})
    local_audio.atomic_json(folder/'error.json', {'code': 'local_engine'})
    local_audio.atomic_json(folder/'progress.json', {'phase': 'transcribing', 'percent': 98})
    local_audio.atomic_json(folder/'checkpoint.json', {'completed_blocks': 1})
    def spawn(*args, **kwargs):
        assert not (folder/'error.json').exists()
        assert local_audio.load_json(folder/'progress.json') == {'phase': 'loading', 'percent': 0}
        assert local_audio.load_json(folder/'checkpoint.json') == {'completed_blocks': 1}
        local_audio.atomic_json(folder/'result.json', {'rows': [{'text': 'Fonte concluída.'}]})
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(local_audio.subprocess, 'Popen', spawn)
    assert local_audio.transcribe(audio, Mock())['rows'][0]['text'] == 'Fonte concluída.'
