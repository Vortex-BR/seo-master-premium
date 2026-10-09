"""Bounded audio acquisition and restartable self-hosted Whisper transcription."""
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from . import audio_routes, db, transcripts
from .transcripts import SourceError
from .security import get_secret, normalize_proxies


MODEL_NAMES = ('tiny', 'base', 'small', 'medium', 'large-v3')
FORMATS = {'.mp3': 'mp3', '.wav': 'wav', '.m4a': 'mov', '.mp4': 'mov',
           '.webm': 'matroska', '.ogg': 'ogg', '.flac': 'flac', '.aac': 'aac'}
ACCESS_TEST_LOCK = threading.Lock()


def configured_proxies():
    try:
        return normalize_proxies(get_secret('youtube_proxy_urls')).splitlines()
    except ValueError:
        raise SourceError('Corrija o formato dos proxies em Integrações ou em YOUTUBE_PROXY_URLS.',
                          code='proxy_configuration', provider='Áudio do YouTube') from None


def route_label(proxy):
    return 'Proxy ' + transcripts.fingerprint(proxy)[:8] if proxy else 'Conexão direta do servidor'


def connection_mode():
    mode = transcripts.option('youtube_connection_mode', 'auto')
    return mode if mode in ('auto', 'direct', 'proxy') else 'auto'


def connection_routes(proxies):
    mode = connection_mode()
    if mode == 'proxy':
        if not proxies:
            raise SourceError('O modo Somente proxies exige pelo menos um proxy salvo em Integrações.',
                              code='proxy_configuration', provider='Áudio do YouTube')
        return proxies
    return [None] + (proxies if mode == 'auto' else [])


def extractor_command(proxy):
    """One transport configuration for both the real download and its access test."""
    return [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-warnings', '--quiet',
            '--no-progress', '--socket-timeout', '10', '--retries', '0', '--fragment-retries', '0',
            '--extractor-retries', '0', '--js-runtimes', 'node' if shutil.which('node') else 'deno',
            '-f', 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[acodec!=none]',
            '--proxy', proxy or '']


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
    elif any(term in text for term in ('confirm your age', 'age-restricted', 'age restricted', 'inappropriate for some users')):
        code, message = 'age_restricted', 'O YouTube exige confirmação de idade para este vídeo. Trocar o proxy não confirma a idade. Envie o áudio de uma cópia à qual você tenha acesso.'
    elif 'not a bot' in text:
        code, message = 'ip_blocked', 'O YouTube exigiu verificação antirobô nesta conexão antes de liberar o áudio. Teste o acesso na aba Fontes e confira com o provedor se o proxy permite downloads do YouTube.'
    elif re.search(r'\b429\b', text) or "this content isn't available, try again later" in text:
        code, message = 'rate_limit', 'O YouTube limitou as consultas desta conexão. Aguarde o prazo indicado antes de tentar novamente.'
    elif 'sign in' in text or 'login required' in text:
        code, message = 'login_required', 'O YouTube exige uma sessão autenticada para este vídeo. O proxy não fornece uma sessão. Envie o áudio de uma cópia à qual você tenha acesso.'
    elif 'private video' in text or 'video unavailable' in text or 'video is unavailable' in text or 'members-only' in text:
        code, message = 'restricted', 'O áudio não está disponível publicamente. Use um arquivo ao qual você tenha acesso.'
    elif 'po token' in text or 'po_token' in text:
        code, message = 'token_required', 'O YouTube exige um token de reprodução que o extrator não conseguiu obter. Confira a instalação do extrator; repetir o mesmo proxy não fornece esse token.'
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
                       retryable=code in ('connection', 'access_denied', 'ip_blocked', 'rate_limit'))


def access_test(video_id):
    """Read a small media sample with the saved routes; never transcribe or write an article."""
    proxies = configured_proxies()
    cache_key = 'youtube_access_test'
    signature = transcripts.fingerprint(video_id + '\n' + connection_mode() + '\n' + '\n'.join(proxies))
    previous = db.get_setting(cache_key, {})
    if previous.get('signature') == signature and time.time() - previous.get('at', 0) < 60:
        return previous['result'] | {'cached': True}
    if not ACCESS_TEST_LOCK.acquire(blocking=False):
        raise SourceError('Já existe um teste de acesso em andamento. Aguarde sua conclusão.', code='busy', retryable=True)
    try:
        try:
            version = importlib.metadata.version('yt-dlp')
        except importlib.metadata.PackageNotFoundError:
            version = 'não instalado'
        result = {'video_id': video_id, 'checked_at': db.now(), 'ok': False, 'cached': False,
                  'extractor_version': version, 'proxies_configured': len(proxies), 'routes': [],
                  'scope': 'O teste verifica uma amostra de áudio. Não transcreve nem inicia a redação; o download integral ainda pode falhar.'}
        if not readiness()['javascript']:
            result['message'] = 'O runtime JavaScript não está instalado. Reconstrua a imagem Docker atualizada.'
            return result
        deadline = transcripts.Deadline(45)
        routes = audio_routes.ordered(connection_routes(proxies))
        result['connection_mode'] = connection_mode()
        paused_routes = []
        # Stable labels correspond to saved routes even when extraction shuffles them.
        for proxy in routes:
            if deadline.remaining() <= 0:
                break
            key = transcripts.route_key('youtube_audio', proxy or 'direct')
            paused = transcripts.route_health(key)
            if paused:
                paused_routes.append(paused | {'label': route_label(proxy), 'ok': False, 'outcome': 'paused'})
                continue
            command = extractor_command(proxy) + ['--skip-download', '--check-formats', '--print', '%(id)s',
                                                  '--', 'https://www.youtube.com/watch?v=' + video_id]
            audio_routes.record(proxy, 'attempt')
            try:
                with tempfile.TemporaryDirectory(prefix='access-', dir=root()) as temporary:
                    run = subprocess.run(command, capture_output=True, timeout=min(15, deadline.require()), cwd=temporary)
                if run.returncode == 0 and video_id in run.stdout.decode('utf-8', errors='replace').splitlines():
                    transcripts.healthy_route(key)
                    audio_routes.record(proxy, 'success')
                    result['routes'].append({'label': route_label(proxy), 'ok': True, 'code': 'accessible',
                                             'message': 'Amostra de áudio acessível nesta conexão.'})
                    result['ok'] = True
                    break
                failure = classify_download(run.stderr.decode('utf-8', errors='replace'), route_label(proxy))
            except subprocess.TimeoutExpired:
                failure = SourceError('O teste excedeu o tempo disponível nesta conexão.', code='connection', retryable=True)
            except OSError:
                failure = SourceError('Não foi possível iniciar o extrator.', code='tools')
            diagnostic = failure.diagnostic
            if diagnostic['code'] in ('proxy_credentials', 'connection', 'ip_blocked', 'access_denied', 'rate_limit'):
                audio_routes.record(proxy, 'failure')
                diagnostic = transcripts.pause_route(key, failure, 3600 if diagnostic['code'] == 'proxy_credentials' else 30 if diagnostic['code'] == 'connection' else 300)
            result['routes'].append(diagnostic | {'label': route_label(proxy), 'ok': False})
            if diagnostic['code'] in ('restricted', 'age_restricted', 'login_required', 'tools', 'token_required'):
                break
        result['paused_routes'] = len(paused_routes)
        result['paused_details'] = paused_routes[:3]
        result['connections_total'] = len(routes)
        result['connections_tested'] = len(result['routes'])
        result['connections_unchecked'] = len(routes) - len(result['routes']) - len(paused_routes)
        result['message'] = ('O servidor conseguiu acessar uma amostra do áudio. Você pode repetir a extração.' if result['ok'] else
                             'Nenhuma conexão testada liberou o áudio. Consulte o motivo de cada conexão abaixo.' if result['routes'] else
                             'As conexões estão em pausa por falhas recentes. Aguarde o prazo indicado na fonte antes de testar novamente.')
        db.set_setting(cache_key, {'signature': signature, 'at': time.time(), 'result': result})
        return result
    finally:
        ACCESS_TEST_LOCK.release()


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
    routes = audio_routes.ordered(connection_routes(proxies))
    eligible = [p for p in routes if not transcripts.route_health(transcripts.route_key('youtube_audio', p or 'direct'))]
    if not eligible:
        paused = [transcripts.route_health(transcripts.route_key('youtube_audio', p or 'direct')) for p in routes]
        next_route = min(paused, key=lambda item: item['retry_at'])
        raise SourceError(next_route['message'], code=next_route['code'], provider='Áudio do YouTube',
                          retryable=next_route['retryable'], retry_at=next_route['retry_at'])
    failure = None
    for index, proxy in enumerate(eligible, 1):
        if deadline.remaining() <= 0:
            break
        provider = route_label(proxy)
        progress('audio_download', f'Obtendo áudio: conexão {index} de {len(eligible)} disponíveis · {provider}.')
        route = transcripts.route_key('youtube_audio', proxy or 'direct')
        command = extractor_command(proxy) + ['--max-filesize', str(config['max_mb']) + 'M',
                   '--match-filter', f'duration <= {config["max_minutes"] * 60} & !is_live',
                   '-o', str(folder / 'audio.%(ext)s'), '--', url]
        try:
            audio_routes.record(proxy, 'attempt')
            result = subprocess.run(command, capture_output=True, timeout=min(120, deadline.require()))
            candidates = [f for f in folder.iterdir() if f.name.startswith('audio.') and f.suffix in FORMATS and f.is_file()]
            if result.returncode == 0 and len(candidates) == 1:
                path = candidates[0]
                if not 0 < path.stat().st_size <= config['max_mb'] * 1024 * 1024:
                    raise SourceError('O arquivo de áudio excede o tamanho configurado.', code='audio_limit', provider=provider)
                atomic_json(folder / 'download.json', {'at': time.time(), 'name': path.name})
                transcripts.healthy_route(route)
                audio_routes.record(proxy, 'success')
                attempts.append({'provider': provider, 'outcome': 'ok', 'message': 'Áudio obtido nesta conexão.'})
                return path
            failure = classify_download(result.stderr.decode('utf-8', errors='replace'), provider)
        except subprocess.TimeoutExpired:
            failure = SourceError('O download não terminou no tempo disponível.', code='connection', provider=provider, retryable=True)
        except OSError:
            failure = SourceError('Não foi possível iniciar o extrator de áudio.', code='tools', provider=provider)
        attempts.append(failure.diagnostic | {'outcome': 'failed', 'route': index})
        code = failure.diagnostic['code']
        if code in ('ip_blocked', 'access_denied', 'proxy_credentials', 'connection', 'rate_limit'):
            audio_routes.record(proxy, 'failure')
            paused = transcripts.pause_route(route, failure, 3600 if code == 'proxy_credentials' else 30 if code == 'connection' else 300)
            attempts[-1].update(paused)
            failure.diagnostic.update(paused)
        if code in ('restricted', 'age_restricted', 'login_required', 'audio_limit', 'tools', 'format_unavailable', 'token_required'):
            raise failure
    if failure is None:
        raise SourceError('O prazo de obtenção do áudio terminou antes de iniciar outra conexão.', code='timeout', retryable=True)
    failure.diagnostic['connections_tested'] = sum(a.get('outcome') == 'failed' for a in attempts)
    failure.diagnostic['connections_available'] = len(eligible)
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


def diagnostics(video_id, upload=None):
    """Return only safe worker metadata for this source, never audio/text or logs."""
    directory = root() / (upload['id'] if upload else transcripts.fingerprint('youtube-audio-v1:' + video_id))
    attempts = []
    for path in root().glob('*/request.json'):
        request = load_json(path, {})
        if not request.get('audio') or Path(request['audio']).resolve().parent != directory.resolve():
            continue
        folder = path.parent
        error = load_json(folder / 'error.json', {})
        checkpoint = load_json(folder / 'checkpoint.json', {})
        frames = []
        for frame in error.get('frames', [])[-8:]:
            name = Path(str(frame.get('file', ''))).name
            function = str(frame.get('function', ''))
            if re.fullmatch(r'[\w.-]{1,80}', name) and re.fullmatch(r'[\w<>]{1,80}', function):
                frames.append({'file': name, 'function': function, 'line': frame.get('line')})
        attempts.append({'model': request.get('model'), 'duration': request.get('duration'),
                         'completed_blocks': checkpoint.get('completed_blocks', 0),
                         'saved_segments': len(checkpoint.get('rows', [])),
                         'result_available': (folder / 'result.json').is_file(),
                         'error_code': error.get('code') if error.get('code') in
                             ('local_engine', 'model_download', 'source_limit', 'audio_decode', 'audio_timestamps') else None,
                         'exception': error.get('exception') if re.fullmatch(r'\w{1,80}', str(error.get('exception', ''))) else None,
                         'frames': frames})
    return {'video_id': video_id, 'attempts': attempts}


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
