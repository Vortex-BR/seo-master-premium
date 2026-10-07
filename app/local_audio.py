"""Bounded audio acquisition and restartable self-hosted Whisper transcription."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

from . import db, transcripts
from .transcripts import SourceError


MODEL_NAMES = ('tiny', 'base', 'small', 'medium', 'large-v3')
FORMATS = {'.mp3': 'mp3', '.wav': 'wav', '.m4a': 'mov', '.mp4': 'mov',
           '.webm': 'matroska', '.ogg': 'ogg', '.flac': 'flac', '.aac': 'aac'}


def bounded_option(name, default, low, high):
    try:
        return max(low, min(high, int(transcripts.option(name, default))))
    except (ValueError, TypeError):
        return default


def configuration():
    model = transcripts.option('whisper_model', 'small')
    return {'model': model if model in MODEL_NAMES else 'small',
            'threads': bounded_option('whisper_threads', 2, 1, 16),
            'max_minutes': bounded_option('audio_max_minutes', 180, 15, 360),
            'max_mb': bounded_option('audio_max_mb', 256, 16, 1024),
            'timeout': bounded_option('local_transcript_timeout', 10800, 300, 21600),
            'device': os.getenv('WHISPER_DEVICE', 'cpu'),
            'compute_type': os.getenv('WHISPER_COMPUTE_TYPE', 'int8'),
            'revision': os.getenv('WHISPER_MODEL_REVISION') or None}


def readiness():
    installed = importlib.util.find_spec('faster_whisper') is not None
    ffmpeg = bool(shutil.which('ffmpeg'))
    runtime = bool(shutil.which('node') or shutil.which('deno'))
    warnings = []
    if not installed:
        warnings.append('O componente Whisper local ainda não está instalado. Reconstrua a imagem Docker atualizada.')
    if not ffmpeg:
        warnings.append('FFmpeg não está disponível para preparar o áudio.')
    if not runtime:
        warnings.append('Um runtime JavaScript é necessário para obter áudio do YouTube. A imagem Docker inclui Node.js.')
    return {'installed': installed, 'ffmpeg': ffmpeg, 'javascript': runtime,
            'model': configuration()['model'], 'warnings': warnings}


def root():
    directory = db.data_dir() / 'audio'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_path(identifier):
    if not re.fullmatch(r'[a-f0-9]{32,64}', identifier):
        raise ValueError('Identificador de áudio inválido.')
    path = (root() / identifier).resolve()
    if path.parent != root().resolve():
        raise ValueError('Caminho de áudio inválido.')
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp-' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def clean_cache():
    """Discard unreferenced downloads after 24 h; uploaded originals stay with their jobs."""
    if not root().exists():
        return
    active = {item['id'] for job in db.list_jobs() for item in job.get('audio_uploads', {}).values()}
    for directory in root().iterdir():
        if not directory.is_dir() or not re.fullmatch(r'[a-f0-9]{32,64}', directory.name) or directory.name in active:
            continue
        # Only remove our known files, leaving unknown files untouched.
        if time.time() - directory.stat().st_mtime <= 86400:
            continue
        for file in directory.iterdir():
            if file.is_file() and file.name in {'audio.m4a', 'audio.webm', 'audio.mp3', 'audio.mp4', 'audio.ogg',
                                              'audio.flac', 'audio.wav', 'audio.aac', 'audio.part', 'download.json', 'block.wav', 'worker.log'}:
                file.unlink(missing_ok=True)
            elif file.is_file() and re.fullmatch(r'audio\.[a-z0-9]+\.(part|ytdl)(-Frag\d+)?', file.name):
                file.unlink(missing_ok=True)


def classify_download(stderr, provider):
    text = stderr.lower()
    if '407' in text or 'proxy authentication required' in text:
        code, message = 'proxy_credentials', 'O proxy recusou a autenticação. Confira as credenciais e o plano do serviço.'
    elif 'sign in to confirm' in text or 'not a bot' in text or '429' in text:
        code, message = 'ip_blocked', 'O YouTube bloqueou o download nesta conexão. Confira os proxies ou envie o áudio na aba Fontes.'
    elif 'private video' in text or 'video unavailable' in text or 'age-restricted' in text or 'members-only' in text:
        code, message = 'restricted', 'O áudio não está disponível publicamente. Use um arquivo ao qual você tenha acesso.'
    elif 'javascript runtime' in text or 'ejs' in text:
        code, message = 'tools', 'O extrator precisa de Node.js e dos componentes JavaScript. Reconstrua a imagem Docker atualizada.'
    elif 'filesize' in text or 'match filter' in text or 'does not pass filter' in text:
        code, message = 'audio_limit', 'O áudio excede os limites configurados de duração ou tamanho.'
    elif '403' in text:
        code, message = 'access_denied', 'O YouTube recusou o acesso ao áudio. Confira o serviço de proxies ou envie o arquivo.'
    elif 'requested format' in text:
        code, message = 'format_unavailable', 'O extrator não encontrou um formato de áudio utilizável. Confira sua atualização e o runtime JavaScript.'
    else:
        code, message = 'connection', 'Não foi possível obter o áudio nesta conexão. Confira os proxies ou envie o arquivo na aba Fontes.'
    return SourceError(message, code=code, provider=provider,
                       retryable=code in ('connection', 'access_denied', 'ip_blocked'))


def download(video_id, url, proxies, deadline, progress, attempts):
    config = configuration()
    folder = safe_path(transcripts.fingerprint('youtube-audio-v1:' + video_id))
    saved = load_json(folder / 'download.json', {})
    if saved and time.time() - saved.get('at', 0) < 86400:
        path = folder / saved.get('name', '')
        if path.parent == folder and path.is_file() and path.suffix in FORMATS and path.stat().st_size <= config['max_mb'] * 1024 * 1024:
            return path
    # Remove expired media before a fresh download; yt-dlp must not renew its age by skipping it.
    for file in folder.iterdir():
        if file.is_file() and file.name.startswith('audio.') and file.suffix in FORMATS:
            file.unlink(missing_ok=True)
    readiness_info = readiness()
    if not readiness_info['javascript']:
        raise SourceError('Instale o runtime JavaScript incluído na nova imagem Docker para obter o áudio.', code='tools', provider='Áudio do YouTube')
    routes = proxies if proxies else [None]
    eligible = [p for p in routes if not transcripts.route_health(transcripts.route_key('youtube_audio', p or 'direct'))]
    if not eligible:
        paused = [transcripts.route_health(transcripts.route_key('youtube_audio', p or 'direct')) for p in routes]
        next_route = min(paused, key=lambda item: item['retry_at'])
        raise SourceError(next_route['message'], code=next_route['code'], provider='Áudio do YouTube',
                          retryable=next_route['retryable'], retry_at=next_route['retry_at'])
    for index, proxy in enumerate(eligible[:3], 1):
        provider = 'Áudio do YouTube via proxy' if proxy else 'Áudio do YouTube direto'
        progress('audio_download', f'Obtendo áudio: conexão {index}.')
        route = transcripts.route_key('youtube_audio', proxy or 'direct')
        command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-warnings', '--quiet',
                   '--no-progress', '--socket-timeout', '15', '--retries', '0', '--fragment-retries', '0',
                   '--extractor-retries', '0', '--max-filesize', str(config['max_mb']) + 'M',
                   '--match-filter', f'duration <= {config["max_minutes"] * 60} & !is_live',
                   '--js-runtimes', 'node' if shutil.which('node') else 'deno',
                   '-f', 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
                   '-o', str(folder / 'audio.%(ext)s'), '--proxy', proxy or '', url]
        try:
            result = subprocess.run(command, capture_output=True, timeout=min(120, deadline.require()))
            candidates = [f for f in folder.iterdir() if f.name.startswith('audio.') and f.suffix in FORMATS and f.is_file()]
            if result.returncode == 0 and len(candidates) == 1:
                path = candidates[0]
                if not 0 < path.stat().st_size <= config['max_mb'] * 1024 * 1024:
                    raise SourceError('O arquivo de áudio excede o tamanho configurado.', code='audio_limit', provider=provider)
                atomic_json(folder / 'download.json', {'at': time.time(), 'name': path.name})
                transcripts.healthy_route(route)
                return path
            failure = classify_download(result.stderr.decode('utf-8', errors='replace'), provider)
        except subprocess.TimeoutExpired:
            failure = SourceError('O download não terminou no tempo disponível.', code='connection', provider=provider, retryable=True)
        except OSError:
            failure = SourceError('Não foi possível iniciar o extrator de áudio.', code='tools', provider=provider)
        attempts.append(failure.diagnostic | {'outcome': 'failed', 'route': index})
        code = failure.diagnostic['code']
        if code in ('ip_blocked', 'access_denied', 'proxy_credentials', 'connection'):
            attempts[-1].update(transcripts.pause_route(route, failure, 3600 if code == 'proxy_credentials' else 300 if code in ('ip_blocked', 'access_denied') else 30))
        if code in ('restricted', 'audio_limit', 'tools', 'format_unavailable') or deadline.remaining() <= 0:
            raise failure
    raise failure


def probe(path):
    config = configuration()
    if path.suffix not in FORMATS or not 0 < path.stat().st_size <= config['max_mb'] * 1024 * 1024:
        raise SourceError('Use um formato de áudio aceito dentro do tamanho configurado.', code='audio_limit', provider='Whisper local')
    try:
        import av
        # Pin the demuxer: a disguised playlist cannot resolve files or URLs.
        with av.open(str(path), format=FORMATS[path.suffix], options={'protocol_whitelist': 'file,pipe', 'enable_drefs': '0'}) as container:
            stream = next(iter(container.streams.audio), None)
            duration = float(stream.duration * stream.time_base) if stream and stream.duration is not None else float(container.duration / av.time_base) if container.duration else 0
            if not stream or duration <= 0 or not math.isfinite(duration):
                raise ValueError('Invalid audio')
    except (ImportError, OSError, ValueError, StopIteration):
        raise SourceError('O arquivo não contém um áudio válido com duração identificável.', code='invalid_audio', provider='Whisper local') from None
    if duration > config['max_minutes'] * 60:
        raise SourceError('O áudio excede a duração configurada. Divida o arquivo ou aumente o limite de acordo com seu servidor.', code='audio_limit', provider='Whisper local')
    return duration


def uploaded_path(upload):
    folder = safe_path(upload['id'])
    suffix = upload.get('suffix')
    if suffix not in FORMATS:
        raise SourceError('Formato do áudio enviado inválido.', code='invalid_audio', provider='Whisper local')
    path = folder / ('original' + suffix)
    if not path.is_file():
        raise SourceError('O arquivo de áudio não está mais disponível. Envie-o novamente.', code='missing_audio', provider='Whisper local')
    return path


def stop_worker(worker):
    if worker.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(worker.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        else:
            os.killpg(worker.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if worker.poll() is None:
        worker.kill()
    worker.wait()


def transcribe(path, progress):
    ready = readiness()
    if not ready['installed'] or not ready['ffmpeg']:
        raise SourceError('O motor de transcrição local precisa ser instalado. Reconstrua a imagem Docker atualizada.', code='tools', provider='Whisper local')
    config = configuration()
    duration = probe(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    semantic = {key: config[key] for key in ('model', 'device', 'compute_type', 'revision')}
    identifier = transcripts.fingerprint('whisper-v1:' + digest.hexdigest() + ':' + json.dumps(semantic, sort_keys=True))
    folder = safe_path(identifier)
    result = load_json(folder / 'result.json')
    if result:
        return result
    request = {'audio': str(path.resolve()), 'format': FORMATS[path.suffix], 'directory': str(folder),
               'models': str((db.data_dir() / 'whisper-models').resolve()), 'duration': duration,
               'audio_sha256': digest.hexdigest(), **config}
    atomic_json(folder / 'request.json', request)
    progress('whisper_loading', 'Carregando o Whisper local. O primeiro uso pode precisar baixar o modelo.')
    deadline = transcripts.Deadline(config['timeout'])
    env = os.environ.copy()
    for name in ('OPENAI_API_KEY', 'SUPADATA_API_KEY', 'YOUTUBE_PROXY_URLS', 'ADMIN_PASSWORD'):
        env.pop(name, None)
    env.update(OMP_NUM_THREADS=str(config['threads']), HF_HUB_DISABLE_TELEMETRY='1')
    # A separate process bounds inference time and releases its model memory afterward.
    with (folder / 'worker.log').open('wb') as log:
        worker = subprocess.Popen([sys.executable, '-m', 'app.whisper_worker', str(folder / 'request.json')],
                                  stdout=log, stderr=log, env=env, cwd=str(Path(__file__).resolve().parents[1]),
                                  start_new_session=os.name != 'nt',
                                  creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        previous = None
        try:
            while worker.poll() is None:
                deadline.require()
                update = load_json(folder / 'progress.json', {})
                current = (update.get('phase'), update.get('percent'))
                if update and current != previous:
                    percent = update.get('percent', 0)
                    progress('whisper_transcribing', f'Transcrevendo áudio localmente: {percent}% concluído.')
                    previous = current
                time.sleep(min(1, deadline.remaining()))
        except BaseException:
            stop_worker(worker)
            raise
    result = load_json(folder / 'result.json')
    if worker.returncode or not result:
        error = load_json(folder / 'error.json', {})
        code = error.get('code', 'local_engine')
        message = {'source_limit': 'A transcrição excede 120 mil caracteres. Use fontes menores.',
                   'model_download': 'O modelo Whisper não pôde ser carregado. Confira a rede e o espaço do servidor.',
                   'audio_decode': 'O áudio não pôde ser decodificado integralmente.',
                   'local_engine': 'O motor local interrompeu a transcrição. Confira memória, CPU e bibliotecas do servidor.'}.get(code, 'A transcrição local não foi concluída.')
        raise SourceError(message, code=code, provider='Whisper local', retryable=True)
    return result
