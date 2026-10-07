import html
import re
import random
import math
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from openai import OpenAI
from requests import Session
from requests.exceptions import ConnectionError, HTTPError, ProxyError, Timeout
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound
from youtube_transcript_api.proxies import GenericProxyConfig

from .security import get_secret
from . import db, local_audio, transcripts
from .transcripts import SourceError


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
    def __init__(self, deadline=None):
        super().__init__()
        self.deadline = deadline
        self.trust_env = False

    def request(self, *args, **kwargs):
        remaining = self.deadline.require() if self.deadline else 25
        kwargs['timeout'] = (min(5, remaining), min(20, remaining))
        return super().request(*args, **kwargs)


def base_metadata(vid):
    return {'video_id': vid, 'url': f'https://www.youtube.com/watch?v={vid}',
            'title': f'Vídeo {vid}', 'author': '', 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}


def metadata(vid):
    info = base_metadata(vid)
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
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            raise SourceError('O provedor retornou trechos inválidos.', code='invalid_response')
        content = html.unescape(re.sub(r'<[^>]*>', '', str(row.get('text', '')))).strip()
        if not content:
            continue
        offset = row.get('start')
        duration = row.get('duration', 0)
        if (offset is not None and (isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0)) or isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise SourceError('O provedor retornou timestamps inválidos.', code='invalid_response')
        if not text:
            start = offset
        text += (' ' if text else '') + content
        end = offset + duration if offset is not None else None
        if len(text) >= 900:
            segments.append({'id': f'{prefix}s{len(segments)+1}', 'text': text, 'start': start, 'end': end})
            text = ''
    if text:
        segments.append({'id': f'{prefix}s{len(segments)+1}', 'text': text, 'start': start, 'end': end})
    if sum(len(s['text']) for s in segments) < 80:
        raise SourceError('O vídeo não contém fala suficiente para fundamentar um artigo.', code='insufficient_speech')
    if sum(len(s['text']) for s in segments) > 120000:
        raise SourceError('Este vídeo excede o limite de 120 mil caracteres. Use um vídeo mais curto.', code='source_limit')
    return segments


def transcribe_audio(url, key, proxy=None, deadline=None):
    with tempfile.TemporaryDirectory(prefix='seo-audio-') as folder:
        command = [sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '--quiet',
                   '--socket-timeout', '20', '--retries', '1', '--max-filesize', '100M',
                   '--match-filter', 'duration <= 2700 & !is_live', '-f', 'bestaudio', '-x',
                   '--audio-format', 'mp3', '--audio-quality', '48K', '-o', str(Path(folder) / 'audio.%(ext)s'), url]
        if proxy:
            command[4:4] = ['--proxy', proxy]
        try:
            result = subprocess.run(command, capture_output=True, timeout=min(240, deadline.require()) if deadline else 240)
        except (subprocess.TimeoutExpired, OSError):
            raise SourceError('Não foi possível obter o áudio no tempo disponível.') from None
        file = Path(folder) / 'audio.mp3'
        if result.returncode or not file.exists() or file.stat().st_size > 24 * 1024 * 1024:
            raise SourceError('Áudio indisponível, restrito ou acima de 45 minutos/24 MB.')
        with OpenAI(api_key=key, timeout=min(180, deadline.require()) if deadline else 180, max_retries=0) as client, file.open('rb') as audio:
            result = client.audio.transcriptions.create(model='whisper-1', file=audio,
                                                        response_format='verbose_json', timestamp_granularities=['segment'])
        return [{'text': x.text, 'start': x.start, 'duration': x.end - x.start} for x in result.segments], result.language


def youtube_failure(exc, provider):
    name = type(exc).__name__
    if isinstance(exc, SourceError):
        exc.diagnostic['provider'] = provider
        return exc
    if name in ('RequestBlocked', 'IpBlocked'):
        return SourceError('O YouTube bloqueou esta conexão. Use um provedor independente ou confira o serviço de proxies.', code='ip_blocked', provider=provider, retryable=True)
    if name in ('TranscriptsDisabled', 'NoTranscriptFound', 'StopIteration'):
        return SourceError('Não há legendas acessíveis neste vídeo. Permita transcrição por IA ou forneça o texto.', code='no_captions', provider=provider)
    if name in ('AgeRestricted', 'VideoUnplayable', 'PoTokenRequired'):
        return SourceError('O vídeo tem uma restrição de acesso que impede obter seu conteúdo.', code='restricted', provider=provider)
    if name in ('VideoUnavailable', 'InvalidVideoId'):
        return SourceError('O vídeo não está disponível publicamente.', code='not_found', provider=provider)
    response = getattr(exc, 'response', None)
    if isinstance(exc, HTTPError) and response is not None and response.status_code == 407:
        return SourceError('O proxy recusou a autenticação. Confira usuário, senha e plano do serviço de proxies.', code='proxy_credentials', provider=provider)
    if isinstance(exc, ProxyError):
        return SourceError('Não foi possível conectar ao proxy. Confira o endereço e as credenciais.', code='proxy_connection', provider=provider, retryable=True)
    if isinstance(exc, (Timeout, ConnectionError)) or name in ('YouTubeRequestFailed', 'YouTubeDataUnparsable'):
        return SourceError('A conexão com o YouTube falhou temporariamente.', code='connection', provider=provider, retryable=True)
    return SourceError('O extrator de legendas não concluiu esta tentativa.', code='extractor_error', provider=provider)


def extract(url, prefix, audio_fallback=False, progress=None, uploaded=None):
    vid = video_id(url)
    config = transcripts.configuration()
    deadline = transcripts.Deadline(config['timeout'])
    info = base_metadata(vid) if uploaded else metadata(vid)
    attempts = []
    proxies = [p.strip() for p in get_secret('youtube_proxy_urls').replace(',', '\n').splitlines() if p.strip()]
    random.shuffle(proxies)
    notify = progress or (lambda source: None)

    def report(phase, message):
        notify(info | {'id': prefix, 'status': 'processing', 'segments': [],
                       'extraction': {'phase': phase, 'message': message, 'attempts': list(attempts)}})

    def success(rows, language, provider, generated=None, extracted_at=None):
        segments = segment_rows(rows, prefix)
        return info | {'id': prefix, 'language': language, 'provider': provider,
                       'generated_captions': generated, 'segments': segments, 'status': 'ok',
                       'extracted_at': extracted_at or db.now(),
                       'extraction': {'phase': 'completed', 'message': 'Transcrição obtida e validada.', 'attempts': attempts + [{'provider': provider, 'outcome': 'ok'}]},
                       'notice': 'Base textual: elementos exibidos apenas na tela não foram analisados.' +
                                 (' O modo automático Supadata pode obter legendas ou gerar uma transcrição por IA.' if provider == 'Supadata' and config['mode'] == 'auto' else '')}

    def failure(exc, provider):
        item = exc if isinstance(exc, SourceError) else youtube_failure(exc, provider)
        item.diagnostic['provider'] = item.diagnostic['provider'] or provider
        attempts.append(item.diagnostic | {'outcome': 'failed'})
        if item.diagnostic['pending'] or item.diagnostic['code'] in ('source_limit', 'insufficient_speech'):
            item.attempts = list(attempts); item.info = info
            raise item
        return item

    def native():
        for index, proxy in enumerate(proxies[:3] if proxies else [None], 1):
            provider = 'YouTube via proxy' if proxy else 'YouTube direto'
            route = transcripts.route_key('youtube', proxy or 'direct')
            paused = transcripts.route_health(route)
            if paused:
                attempts.append(paused | {'provider': provider, 'outcome': 'paused', 'route': index})
                continue
            report('youtube', f'Obtendo legendas: {provider}, conexão {index}.')
            try:
                deadline.require()
                with TimeoutSession(deadline) as session:
                    proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None
                    api = YouTubeTranscriptApi(http_client=session, proxy_config=proxy_config)
                    available = api.list(vid)
                    try:
                        transcript = available.find_transcript(['pt', 'pt-BR', 'en', 'en-US', 'es'])
                    except NoTranscriptFound:
                        transcript = next(iter(available))
                    result = transcript.fetch()
                caption_kind = getattr(transcript, 'is_generated', None)
                source = success(result.to_raw_data(), result.language_code,
                                 'Legendas do YouTube' + (' via proxy' if proxy else ''),
                                 caption_kind if isinstance(caption_kind, bool) else None)
                transcripts.healthy_route(route)
                return source
            except Exception as exc:
                item = failure(exc, provider)
                code = item.diagnostic['code']
                if code in ('ip_blocked', 'proxy_credentials', 'proxy_connection', 'connection'):
                    attempts[-1].update(transcripts.pause_route(route, item, 300 if code == 'ip_blocked' else 3600 if code == 'proxy_credentials' else 30))
                if code in ('no_captions', 'restricted', 'not_found'):
                    break
        return None

    def external():
        key = get_secret('supadata_api_key')
        if not key:
            attempts.append({'provider': 'Supadata', 'outcome': 'not_configured', 'code': 'not_configured',
                             'message': 'Configure uma chave Supadata em Integrações para usar o provedor independente.',
                             'retryable': False, 'retry_at': None, 'pending': False, 'uncertain': False})
            return None
        try:
            result = transcripts.supadata(vid, info['url'], key, config['mode'], deadline, report)
            return success(result['rows'], result['language'], 'Supadata', extracted_at=result['extracted_at'])
        except Exception as exc:
            failure(exc, 'Supadata')
            return None

    if config['provider'] == 'local' or uploaded:
        try:
            path = local_audio.uploaded_path(uploaded) if uploaded else local_audio.download(vid, info['url'], proxies, deadline, report, attempts)
            result = local_audio.transcribe(path, report)
            provider = 'Whisper local · arquivo enviado' if uploaded else 'Whisper local · YouTube'
            source = success(result['rows'], result['language'], provider,
                             extracted_at=datetime.fromtimestamp(result['completed_at'], timezone.utc).isoformat())
            source.update(medium='audio', transcription_model=result['model'],
                          audio_sha256=result['audio_sha256'], transcription_warnings=result['warnings'],
                          audio_duration=result['duration'], input_origin='uploaded_audio' if uploaded else 'youtube_audio')
            source['notice'] = ('Transcrição automática do áudio com Whisper local; confira termos, nomes e trechos sinalizados. ' +
                                ('Arquivo enviado pelo usuário; o vínculo com o vídeo não foi verificado. ' if uploaded else '') +
                                'Elementos exibidos apenas na tela não foram analisados.')
            return source
        except Exception as exc:
            error = exc if isinstance(exc, SourceError) else SourceError('A transcrição local não foi concluída. Confira o motor e os recursos do servidor.', code='local_engine', provider='Whisper local')
            error.attempts = attempts + [error.diagnostic | {'outcome': 'failed'}]
            error.info = info
            raise error from None

    order = [native, external]
    if config['provider'] == 'supadata':
        order = [external]
    for obtain in order:
        if deadline.remaining() <= 0:
            break
        source = obtain()
        if source:
            return source
    key = get_secret('openai_api_key')
    if audio_fallback and key and deadline.remaining() > 0:
        healthy = [p for p in (proxies[:3] if proxies else [None]) if not transcripts.route_health(transcripts.route_key('youtube', p or 'direct'))]
        # Audio download uses the same access routes; don't reuse a known blocked route.
        if healthy:
            report('audio', 'Obtendo áudio para transcrição autorizada com OpenAI.')
            try:
                rows, language = transcribe_audio(info['url'], key, healthy[0], deadline)
                return success(rows, language, 'Transcrição de áudio OpenAI')
            except Exception as exc:
                failure(exc, 'Transcrição de áudio OpenAI')
    priority = ('uncertain', 'credentials', 'quota', 'proxy_credentials', 'rate_limit', 'source_limit', 'restricted', 'not_found', 'no_captions', 'ip_blocked', 'connection', 'not_configured')
    selected = next((a for code in priority for a in attempts if a['code'] == code), attempts[-1] if attempts else None)
    if selected:
        error = SourceError(selected['message'], **{k: selected[k] for k in ('code', 'provider', 'retryable', 'retry_at', 'pending', 'uncertain')})
    else:
        error = SourceError('O tempo da extração terminou. Consulte as fontes para retomar.', code='timeout', retryable=True)
    error.attempts = attempts
    error.info = info
    raise error


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
