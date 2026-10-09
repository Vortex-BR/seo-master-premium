"""Isolated CPU/GPU worker; persist every completed audio block before proceeding."""
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import wave

from .local_audio import atomic_json, load_json


BLOCK_SECONDS = 600


class TimestampError(ValueError):
    pass


def transcribe_block(model, wav, actual, offset, duration, folder, character_budget):
    """Commit a block only when all segments have valid audio-backed timing."""
    for aligned in (False, True):
        rows, warnings, characters = [], [], 0
        if aligned:
            atomic_json(folder / 'progress.json', {'phase': 'aligning', 'percent': int(offset / duration * 100)})
        options = {'word_timestamps': True} if aligned else {}
        segments, info = model.transcribe(str(wav), beam_size=5, vad_filter=True,
                                         vad_parameters={'min_silence_duration_ms': 500},
                                         condition_on_previous_text=False, **options)
        last_progress = time.monotonic()
        try:
            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue
                start, end = float(segment.start), float(segment.end)
                # Timestamp tokens have 20 ms precision. Allow boundary rounding,
                # but never silently clamp a material overrun or accept NaN.
                if (not math.isfinite(start) or not math.isfinite(end) or start < -.05
                        or end <= start or start >= actual or end > actual + .05):
                    raise TimestampError('Invalid audio timestamps')
                start, end = max(0, start), min(actual, end)
                if end <= start:
                    raise TimestampError('Empty audio interval')
                rows.append({'text': text, 'start': offset + start, 'duration': end - start})
                characters += len(text)
                if characters > character_budget:
                    atomic_json(folder / 'error.json', {'code': 'source_limit'})
                    return None
                if segment.avg_logprob < -1 or segment.no_speech_prob > .6:
                    warnings.append({'start': offset + start, 'end': offset + end,
                                     'reason': 'Trecho com baixa confiança; confira o áudio original.'})
                if time.monotonic() - last_progress >= 5:
                    atomic_json(folder / 'progress.json', {'phase': 'aligning' if aligned else 'transcribing',
                        'percent': min(99, int((offset + end) / duration * 100))})
                    last_progress = time.monotonic()
            return rows, warnings, info.language, aligned
        except TimestampError:
            if aligned:
                atomic_json(folder / 'error.json', {'code': 'audio_timestamps', 'exception': 'TimestampError'})
                return None
        finally:
            close = getattr(segments, 'close', None)
            if close:
                close()


def run(request):
    folder = Path(request['directory'])
    state = load_json(folder / 'checkpoint.json', {'rows': [], 'completed_blocks': 0, 'languages': [], 'warnings': []})
    total = math.ceil(request['duration'] / BLOCK_SECONDS)
    characters = sum(len(row['text']) for row in state['rows'])
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(request['model'], device=request['device'], compute_type=request['compute_type'],
                             cpu_threads=request['threads'], num_workers=1, download_root=request['models'],
                             revision=request.get('revision'))
    except Exception as exc:
        atomic_json(folder / 'error.json', {'code': 'model_download', 'exception': type(exc).__name__})
        return 1
    for index in range(state['completed_blocks'], total):
        offset = index * BLOCK_SECONDS
        wav = folder / 'block.wav'
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-protocol_whitelist', 'file,pipe', '-f', request['format'], '-ss', str(offset),
                   '-i', request['audio'], '-t', str(min(BLOCK_SECONDS, request['duration'] - offset)),
                   '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)]
        try:
            decoded = subprocess.run(command, capture_output=True, timeout=120)
            if decoded.returncode:
                raise ValueError('Audio decode failed')
            with wave.open(str(wav), 'rb') as stream:
                actual = stream.getnframes() / stream.getframerate()
            expected = min(BLOCK_SECONDS, request['duration'] - offset)
            if actual <= 0 or abs(actual - expected) > max(.5, expected * .01):
                raise ValueError('Audio duration does not match')
        except (OSError, ValueError, wave.Error, subprocess.TimeoutExpired):
            atomic_json(folder / 'error.json', {'code': 'audio_decode'})
            return 1
        block = transcribe_block(model, wav, actual, offset, request['duration'], folder, 120000 - characters)
        if block is None:
            return 1
        rows, warnings, language, aligned = block
        state['rows'].extend(rows)
        state['warnings'].extend(warnings)
        state['languages'].append(language)
        characters += sum(len(row['text']) for row in rows)
        if aligned:
            state.setdefault('realigned_blocks', []).append(index)
        state['completed_blocks'] = index + 1
        atomic_json(folder / 'checkpoint.json', state)
        atomic_json(folder / 'progress.json', {'phase': 'transcribing', 'percent': min(99, int((index + 1) / total * 100))})
        wav.unlink(missing_ok=True)
    result = {'rows': state['rows'], 'language': Counter(state['languages']).most_common(1)[0][0] if state['languages'] else '',
              'warnings': state['warnings'], 'duration': request['duration'], 'model': request['model'],
              'completed_at': time.time(), 'audio_sha256': request.get('audio_sha256', ''),
              'realigned_blocks': state.get('realigned_blocks', [])}
    atomic_json(folder / 'result.json', result)
    atomic_json(folder / 'progress.json', {'phase': 'completed', 'percent': 100})
    return 0


def main():
    path = Path(sys.argv[1])
    request = json.loads(path.read_text(encoding='utf-8'))
    try:
        return run(request)
    except Exception as exc:
        import traceback
        frames = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                  for frame in traceback.extract_tb(exc.__traceback__)]
        atomic_json(Path(request['directory']) / 'error.json', {'code': 'local_engine', 'exception': type(exc).__name__, 'frames': frames})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
