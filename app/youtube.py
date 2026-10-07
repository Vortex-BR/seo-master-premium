import html
import re
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from openai import OpenAI
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

from .security import get_secret


class SourceError(ValueError):
    pass


def video_id(value):
    url = urlsplit(value.strip())
    if url.scheme not in ('http', 'https') or url.username or url.password or url.port not in (None, 80, 443):
        raise ValueError('Informe um link válido do YouTube.')
    host = (url.hostname or '').lower()
    if host in ('youtu.be', 'www.youtu.be'):
        candidate = url.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if url.path == '/watch':
            candidate = parse_qs(url.query).get('v', [''])[0]
        elif re.fullmatch(r'/(shorts|embed|live)/[^/]+/?', url.path):
            candidate = url.path.split('/')[2]
        else:
            candidate = ''
    else:
        candidate = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', candidate):
        raise ValueError('Use um link de vídeo do YouTube (watch, youtu.be, shorts ou live).')
    return candidate


class TimeoutSession(Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', 25)
        return super().request(*args, **kwargs)


def metadata(vid):
    info = {'video_id': vid, 'url': f'https://www.youtube.com/watch?v={vid}',
            'title': f'Vídeo {vid}', 'author': '', 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}
    try:
        r = httpx.get('https://www.youtube.com/oembed', params={'url': info['url'], 'format': 'json'}, timeout=12)
        if r.is_success:
            value = r.json()
            info.update(title=value.get('title', info['title']), author=value.get('author_name', ''))
    except (httpx.HTTPError, ValueError):
        pass
    return info


def segment_rows(rows, prefix):
    segments = []
    text, start, end = '', None, None
    for row in rows:
        content = html.unescape(re.sub(r'<[^>]*>', '', str(row.get('text', '')))).strip()
        if not content:
            continue
        offset = row.get('start')
        if not text:
            start = offset
        text += (' ' if text else '') + content
        end = offset + row.get('duration', 0) if offset is not None else None
        if len(text) >= 900:
            segments.append({'id': f'{prefix}s{len(segments)+1}', 'text': text, 'start': start, 'end': end})
            text = ''
    if text:
        segments.append({'id': f'{prefix}s{len(segments)+1}', 'text': text, 'start': start, 'end': end})
    if sum(len(s['text']) for s in segments) < 80:
        raise SourceError('O vídeo não contém fala suficiente para fundamentar um artigo.')
    if sum(len(s['text']) for s in segments) > 120000:
        raise SourceError('Este vídeo excede o limite de 120 mil caracteres. Use um vídeo mais curto.')
    return segments


def fetch_supadata(url, key):
    with httpx.Client(timeout=60, headers={'x-api-key': key}) as client:
        response = client.get('https://api.supadata.ai/v1/transcript',
                              params={'url': url, 'text': 'false', 'mode': 'auto'})
        response.raise_for_status()
        data = response.json()
        if response.status_code == 202:
            job_id = data.get('jobId', '')
            if not re.fullmatch(r'[a-zA-Z0-9-]+', job_id):
                raise SourceError('O provedor retornou um identificador inválido.')
            for _ in range(40):
                time.sleep(3)
                response = client.get(f'https://api.supadata.ai/v1/transcript/{job_id}')
                response.raise_for_status()
                data = response.json()
                if data.get('status') == 'completed':
                    break
                if data.get('status') == 'failed':
                    raise SourceError('O provedor não conseguiu transcrever este vídeo.')
            else:
                raise SourceError('A transcrição demorou além do limite. Tente novamente em alguns minutos.')
        content = data.get('content', [])
        if isinstance(content, str):
            return [{'text': content, 'start': None, 'duration': 0}], data.get('lang', '')
        return [{'text': x['text'], 'start': x['offset'] / 1000 if 'offset' in x else None,
                 'duration': x.get('duration', 0) / 1000} for x in content], data.get('lang', '')


def transcribe_audio(url, key):
    with tempfile.TemporaryDirectory(prefix='seo-audio-') as folder:
        command = [sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '--quiet',
                   '--socket-timeout', '20', '--retries', '1', '--max-filesize', '100M',
                   '--match-filter', 'duration <= 2700 & !is_live', '-f', 'bestaudio', '-x',
                   '--audio-format', 'mp3', '--audio-quality', '48K', '-o', str(Path(folder) / 'audio.%(ext)s'), url]
        try:
            result = subprocess.run(command, capture_output=True, timeout=240)
        except (subprocess.TimeoutExpired, OSError):
            raise SourceError('Não foi possível obter o áudio no tempo disponível.') from None
        file = Path(folder) / 'audio.mp3'
        if result.returncode or not file.exists() or file.stat().st_size > 24 * 1024 * 1024:
            raise SourceError('Áudio indisponível, restrito ou acima de 45 minutos/24 MB.')
        with OpenAI(api_key=key, timeout=180, max_retries=1) as client, file.open('rb') as audio:
            result = client.audio.transcriptions.create(model='whisper-1', file=audio,
                                                        response_format='verbose_json', timestamp_granularities=['segment'])
        return [{'text': x.text, 'start': x.start, 'duration': x.end - x.start} for x in result.segments], result.language


def extract(url, prefix, audio_fallback=False):
    vid = video_id(url)
    info = metadata(vid)
    errors = []
    proxies = [p.strip() for p in get_secret('youtube_proxy_urls').replace(',', '\n').splitlines() if p.strip()]
    random.shuffle(proxies)
    rows = None
    generated_captions = None
    for proxy in proxies[:3] if proxies else [None]:
        try:
            with TimeoutSession() as session:
                config = GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None
                api = YouTubeTranscriptApi(http_client=session, proxy_config=config)
                available = api.list(vid)
                try:
                    transcript = available.find_transcript(['pt', 'pt-BR', 'en', 'en-US', 'es'])
                except Exception:
                    transcript = next(iter(available))
                result = transcript.fetch()
            rows = result.to_raw_data()
            language, provider = result.language_code, 'Legendas do YouTube' + (' via proxy' if proxy else '')
            caption_kind = getattr(transcript, 'is_generated', None)
            generated_captions = caption_kind if isinstance(caption_kind, bool) else None
            break
        except Exception as exc:
            errors.append(type(exc).__name__)
    supadata = get_secret('supadata_api_key')
    if rows is None and supadata:
        try:
            rows, language = fetch_supadata(info['url'], supadata)
            provider = 'Supadata'
        except Exception as exc:
            errors.append(type(exc).__name__)
    key = get_secret('openai_api_key')
    if rows is None and audio_fallback and key:
        try:
            rows, language = transcribe_audio(info['url'], key)
            provider = 'Transcrição de áudio OpenAI'
        except Exception as exc:
            errors.append(type(exc).__name__)
    if rows is None:
        reason = ('O YouTube bloqueou as conexões usadas para obter as legendas.'
                  if errors and all(error in {'RequestBlocked', 'IpBlocked'} for error in errors)
                  else 'Não foi possível obter o conteúdo deste vídeo. O acesso pode estar bloqueado ou o vídeo pode não ter legendas.')
        raise SourceError(reason + ' Confira os proxies ou configure Supadata em '
                          'Integrações, ative a transcrição de áudio ou adicione a transcrição como alternativa. '
                          f'Diagnóstico: {", ".join(errors)}.')
    return info | {'id': prefix, 'language': language, 'provider': provider,
                   'generated_captions': generated_captions,
                   'segments': segment_rows(rows, prefix), 'status': 'ok',
                   'notice': 'Base textual: elementos exibidos apenas na tela não foram analisados.'}


def manual_segments(text, prefix):
    # Preserve timestamps from SRT/VTT. Plain text never receives invented timestamps.
    pattern = re.compile(r'(?:(\d{2}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->[^\n]*\n(.*?)(?=\n\s*\n|\Z)', re.S)
    rows = []
    for match in pattern.finditer(text):
        hours, minutes, seconds, millis, content = match.groups()
        start = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis)/1000
        rows.append({'text': re.sub(r'\s+', ' ', content), 'start': start, 'duration': 0})
    if not rows:
        # A single plain-text row is partitioned later on explanation boundaries.
        rows = [{'text': text, 'start': None, 'duration': 0}]
    return segment_rows(rows, prefix)
