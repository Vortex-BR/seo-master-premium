"""Durable transcript requests and connection health, independent of editorial runs."""
from copy import deepcopy
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import db
from .security import get_secret


SUPADATA_ROWS_VERSION = 'supadata-rows-v2'


REQUEST_TTL = 24 * 3600
POLL_INTERVAL = 2


class SourceError(ValueError):
    def __init__(self, message, *, code='unavailable', provider='', retryable=False,
                 retry_at=None, pending=False, uncertain=False):
        super().__init__(message)
        self.diagnostic = {'code': code, 'provider': provider, 'message': message,
                           'retryable': retryable, 'retry_at': retry_at,
                           'pending': pending, 'uncertain': uncertain}
        self.attempts = []
        self.info = {}


class Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.end - time.monotonic())

    def require(self):
        if self.remaining() <= 0:
            raise SourceError('O tempo desta consulta terminou. As entregas foram preservadas.',
                              code='timeout', retryable=True)
        return self.remaining()


def option(name, default):
    return db.get_setting(name, os.getenv(name.upper(), default))


def configuration():
    # Validate environment values as well as settings saved through the API.
    provider = option('transcript_provider', 'local')
    mode = option('supadata_mode', 'native')
    try:
        timeout = max(60, min(600, int(option('transcript_timeout', 180))))
    except (ValueError, TypeError):
        timeout = 180
    return {'provider': provider if provider in ('local', 'supadata', 'youtube') else 'local',
            'mode': mode if mode in ('native', 'auto') else 'native', 'timeout': timeout}


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_key(video_id, key, mode):
    return fingerprint('supadata:' + video_id + ':' + mode + ':' + fingerprint(key))


def route_key(provider, credential=''):
    return fingerprint(provider + ':' + credential)


def route_health(key):
    with db.connect() as conn:
        row = conn.execute('SELECT data, until_at FROM transcript_routes WHERE key=?', (key,)).fetchone()
    if row and row['until_at'] > time.time():
        return json.loads(row['data']) | {'retry_at': row['until_at']}
    return None


def pause_route(key, failure, seconds):
    until = time.time() + seconds
    data = failure.diagnostic | {'retry_at': until}
    with db.connect() as conn:
        conn.execute('INSERT INTO transcript_routes VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET '
                     'until_at=excluded.until_at, data=excluded.data',
                     (key, until, json.dumps(data, ensure_ascii=False)))
    return data


def healthy_route(key):
    with db.connect() as conn:
        conn.execute('DELETE FROM transcript_routes WHERE key=?', (key,))


def request_record(key):
    with db.connect() as conn:
        row = conn.execute('SELECT data FROM transcript_requests WHERE key=?', (key,)).fetchone()
    return json.loads(row['data']) if row else None


def save_request(key, record):
    record['updated_at'] = time.time()
    with db.connect() as conn:
        conn.execute('UPDATE transcript_requests SET data=? WHERE key=?',
                     (json.dumps(record, ensure_ascii=False), key))


def claim_request(key, video_id, mode):
    record = {'video_id': video_id, 'mode': mode, 'state': 'submitting',
              'created_at': time.time(), 'updated_at': time.time(), 'ticket': None}
    with db.connect() as conn:
        result = conn.execute('INSERT OR IGNORE INTO transcript_requests VALUES (?,?)',
                              (key, json.dumps(record)))
        claimed = result.rowcount == 1
    return claimed, record if claimed else request_record(key)


def retry_after(response, default=60):
    value = response.headers.get('retry-after', '')
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            seconds = default
    return max(1, min(3600, seconds)) if math.isfinite(seconds) else default


def provider_failure(status, *, polling=False, response=None):
    retryable = False
    if status == 401:
        code, message = 'credentials', 'A chave Supadata não foi aceita. Corrija a chave em Integrações.'
    elif status == 402:
        code, message = 'quota', 'A Supadata exige saldo ou um plano compatível. Confira a conta do provedor.'
    elif status == 429:
        code, message = 'rate_limit', 'A Supadata informou limite de uso. Aguarde antes de consultar novamente.'
        retryable = True
    elif status == 206:
        code, message = 'no_captions', 'A Supadata não encontrou legendas existentes. Permita transcrição por IA ou forneça a transcrição.'
    elif status == 403:
        code, message = 'restricted', 'O provedor não conseguiu acessar o vídeo. Confira restrições de acesso.'
    elif status == 404:
        code, message = ('expired', 'O pedido de transcrição expirou no provedor. Uma nova solicitação precisa ser iniciada explicitamente.') if polling else ('not_found', 'O vídeo não foi encontrado ou não está disponível publicamente.')
    elif status >= 500:
        code, message = 'provider_unavailable', 'A Supadata está temporariamente indisponível.'
        retryable = polling
    else:
        code, message = 'invalid_request', 'A Supadata recusou a solicitação. Confira o vídeo e a configuração.'
    retry_at = time.time() + retry_after(response) if response is not None and status == 429 else None
    return SourceError(message, code=code, provider='Supadata', retryable=retryable, retry_at=retry_at)


def normalize(data):
    if not isinstance(data, dict):
        raise SourceError('O provedor retornou uma resposta inválida.', code='invalid_response', provider='Supadata')
    # REST returns content at the top level; some compatible responses wrap result.
    data = data.get('result', data)
    if not isinstance(data, dict):
        raise SourceError('O provedor retornou uma resposta inválida.', code='invalid_response', provider='Supadata')
    content = data.get('content')
    if isinstance(content, str):
        rows = [{'text': content, 'start': None, 'duration': None}]
    elif isinstance(content, list):
        rows = []
        for item in content:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                raise SourceError('O provedor retornou trechos inválidos.', code='invalid_response', provider='Supadata')
            offset, duration = item.get('offset'), item.get('duration')
            if (offset is not None and (isinstance(offset, bool) or not isinstance(offset, (float, int)) or not math.isfinite(offset) or offset < 0)) or (duration is not None and (isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0)):
                raise SourceError('O provedor retornou timestamps inválidos.', code='invalid_response', provider='Supadata')
            rows.append({'text': item['text'], 'start': offset / 1000 if offset is not None else None,
                         'duration': duration / 1000 if duration is not None else None,
                         **{key: deepcopy(item[key]) for key in ('id', 'cue_id', 'original_id', 'speaker',
                             'speaker_id', 'confidence', 'origin', 'language', 'quality') if key in item}})
    else:
        raise SourceError('O provedor retornou uma resposta sem transcrição.', code='invalid_response', provider='Supadata')
    length = sum(len(row['text'].strip()) for row in rows)
    if length < 80:
        raise SourceError('O vídeo não contém fala suficiente para fundamentar um artigo.', code='insufficient_speech', provider='Supadata')
    if length > 120000:
        raise SourceError('Este vídeo excede o limite de 120 mil caracteres.', code='source_limit', provider='Supadata')
    language = data.get('lang', '')
    if not isinstance(language, str) or len(language) > 50:
        raise SourceError('O provedor retornou um idioma inválido.', code='invalid_response', provider='Supadata')
    return {'rows': rows, 'language': language, 'provider_adapter_version': SUPADATA_ROWS_VERSION}


def cached_result(result):
    """Read old paid results conservatively without resubmitting or rewriting.

    Previous adapters defaulted a missing duration to zero and discarded cue,
    speaker and confidence metadata. No reader can recover that information.
    Known positive intervals and the original text remain usable; ambiguous
    zero durations stay unknown, with an explicit provenance warning.
    """
    result = deepcopy(result)
    if result.get('provider_adapter_version') == SUPADATA_ROWS_VERSION:
        return result
    result.update(provider_adapter_version='supadata-rows-legacy', provider_cache_legacy=True)
    for row in result.get('rows', []):
        if row.get('duration') == 0:
            row['duration'] = None
        row.setdefault('normalization_warnings', []).append({
            'code': 'legacy_provider_metadata',
            'reason': 'Cache Supadata antigo: IDs de cue, locutor e confiança não foram preservados; '
                      'durações zero podem representar tempo desconhecido. Nada foi reconstruído por suposição.'})
    return result


def uncertain_failure():
    return SourceError('O envio à Supadata ficou sem confirmação. Confira o pedido na conta do provedor antes de autorizar uma nova solicitação.',
                       code='uncertain', provider='Supadata', uncertain=True)


def pending_failure(record):
    return SourceError('A transcrição ainda está em processamento na Supadata. Consulte novamente para continuar o mesmo pedido.',
                       code='pending', provider='Supadata', pending=True, retryable=True,
                       retry_at=time.time() + POLL_INTERVAL)


def supadata(video_id, url, key, mode, deadline, progress):
    deadline.require()
    identifier = request_key(video_id, key, mode)
    claimed, record = claim_request(identifier, video_id, mode)
    route = route_key('supadata', key)
    if not claimed:
        state = record['state']
        if state == 'completed' and time.time() - record['completed_at'] <= REQUEST_TTL:
            return cached_result(record['result']) | {'extracted_at': datetime.fromtimestamp(record['completed_at'], timezone.utc).isoformat()}
        if state in ('submitting', 'uncertain'):
            raise uncertain_failure()
        if state == 'failed':
            failure = record['failure']
            # Definite transient rejections can be retried once their cooldown ends.
            if failure['code'] in ('rate_limit',) and (failure.get('retry_at') or 0) <= time.time():
                with db.connect() as conn:
                    conn.execute('DELETE FROM transcript_requests WHERE key=? AND data=?', (identifier, json.dumps(record, ensure_ascii=False)))
                return supadata(video_id, url, key, mode, deadline, progress)
            raise SourceError(failure['message'], **{k: failure[k] for k in ('code', 'provider', 'retryable', 'retry_at', 'pending', 'uncertain')})
        if state == 'completed':
            # Completed requests expire locally after 24 h; safe to request fresh data.
            with db.connect() as conn:
                conn.execute('DELETE FROM transcript_requests WHERE key=?', (identifier,))
            return supadata(video_id, url, key, mode, deadline, progress)
    paused = route_health(route)
    if paused:
        if claimed:
            # No network request has been sent; release the unused claim.
            with db.connect() as conn:
                conn.execute('DELETE FROM transcript_requests WHERE key=?', (identifier,))
        raise SourceError(paused['message'], code=paused['code'], provider='Supadata',
                          retryable=paused['retryable'], retry_at=paused['retry_at'], pending=bool(record.get('ticket')))
    with httpx.Client(headers={'x-api-key': key}, follow_redirects=False, trust_env=False) as client:
        if claimed:
            progress('supadata_request', 'Solicitando a transcrição à Supadata.')
            try:
                remaining = deadline.require()
                response = client.get('https://api.supadata.ai/v1/transcript',
                                      params={'url': url, 'text': 'false', 'mode': mode},
                                      timeout=httpx.Timeout(min(105, remaining), connect=min(5, remaining)))
            except (httpx.HTTPError, SourceError):
                record['state'] = 'uncertain'; save_request(identifier, record)
                raise uncertain_failure() from None
            if response.status_code not in (200, 202):
                failure = provider_failure(response.status_code, response=response)
                record['state'] = 'uncertain' if response.status_code >= 500 or response.is_redirect else 'failed'
                record['failure'] = failure.diagnostic; save_request(identifier, record)
                if response.status_code in (401, 402, 429) or response.status_code >= 500:
                    pause_route(route, failure, retry_after(response) if response.status_code == 429 else 300)
                raise uncertain_failure() if record['state'] == 'uncertain' else failure
            try:
                data = response.json()
            except (ValueError, TypeError):
                record['state'] = 'uncertain'; save_request(identifier, record)
                raise uncertain_failure() from None
            if response.status_code == 202:
                ticket = data.get('jobId') if isinstance(data, dict) else None
                if not isinstance(ticket, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,200}', ticket):
                    record['state'] = 'uncertain'; save_request(identifier, record)
                    raise uncertain_failure()
                record.update(state='pending', ticket=ticket); save_request(identifier, record)
                progress('supadata_pending', 'Transcrição iniciada. O pedido foi salvo para retomada.')
            else:
                return complete_request(identifier, record, data, route)
        failures = 0
        while deadline.remaining() > POLL_INTERVAL:
            try:
                remaining = deadline.require()
                response = client.get('https://api.supadata.ai/v1/transcript/' + record['ticket'],
                                      timeout=httpx.Timeout(min(20, remaining), connect=min(5, remaining)))
            except httpx.HTTPError:
                failures += 1
                if failures >= 2:
                    raise pending_failure(record) from None
                time.sleep(min(POLL_INTERVAL, deadline.remaining()))
                continue
            if response.status_code != 200:
                failure = provider_failure(response.status_code, polling=True, response=response)
                if response.status_code == 429:
                    pause_route(route, failure, retry_after(response))
                    failure.diagnostic['pending'] = True
                    raise failure
                if response.status_code >= 500:
                    failures += 1
                    if failures >= 2:
                        raise pending_failure(record)
                    time.sleep(min(POLL_INTERVAL, deadline.remaining()))
                    continue
                record.update(state='failed', failure=failure.diagnostic); save_request(identifier, record)
                if response.status_code in (401, 402):
                    pause_route(route, failure, 300)
                raise failure
            try:
                data = response.json()
            except ValueError:
                raise pending_failure(record) from None
            status = data.get('status') if isinstance(data, dict) else None
            if status == 'completed':
                return complete_request(identifier, record, data, route)
            if status == 'failed':
                failure = SourceError('A Supadata não conseguiu transcrever o vídeo. Confira a disponibilidade do conteúdo.', code='provider_failed', provider='Supadata')
                record.update(state='failed', failure=failure.diagnostic); save_request(identifier, record)
                raise failure
            if status not in ('queued', 'active'):
                raise pending_failure(record)
            time.sleep(min(POLL_INTERVAL, deadline.remaining()))
        raise pending_failure(record)


def complete_request(identifier, record, data, route):
    try:
        result = normalize(data)
    except SourceError as failure:
        record.update(state='failed', failure=failure.diagnostic); save_request(identifier, record)
        raise
    record.update(state='completed', result=result, completed_at=time.time())
    save_request(identifier, record); healthy_route(route)
    return result | {'extracted_at': datetime.fromtimestamp(record['completed_at'], timezone.utc).isoformat()}


def reset_request(video_id):
    config = configuration()
    key = get_secret('supadata_api_key')
    identifier = request_key(video_id, key, config['mode'])
    with db.connect() as conn:
        row = conn.execute('SELECT data FROM transcript_requests WHERE key=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Não há solicitação Supadata para reiniciar nesta configuração.')
        record = json.loads(row['data'])
        if record['state'] in ('pending', 'completed'):
            raise ValueError('Consulte o pedido existente. Não é necessário criar outra solicitação.')
        conn.execute('DELETE FROM transcript_requests WHERE key=?', (identifier,))
        conn.execute('DELETE FROM transcript_routes WHERE key=?', (route_key('supadata', key),))
    return {'ok': True}


def readiness():
    from . import local_audio
    config = configuration()
    configured = bool(get_secret('supadata_api_key'))
    proxies = bool(get_secret('youtube_proxy_urls'))
    primary = 'Whisper local · áudio' if config['provider'] == 'local' else 'Supadata' if config['provider'] == 'supadata' else 'YouTube via proxy' if proxies else 'YouTube direto'
    warnings = []
    try:
        proxy_count = len(local_audio.configured_proxies())
    except SourceError as error:
        proxy_count = 0
        warnings.append(str(error))
    if config['provider'] == 'supadata' and not configured:
        warnings.append('Configure a chave Supadata para o provedor selecionado.')
    if config['provider'] == 'local':
        warnings.extend(local_audio.readiness()['warnings'])
    if not configured and not proxies:
        warnings.append('O download do áudio depende do acesso ao YouTube. Se a conexão for bloqueada, envie o áudio na aba Fontes ou configure um serviço de proxies compatível.')
    with db.connect() as conn:
        paused = conn.execute('SELECT COUNT(*) FROM transcript_routes WHERE until_at>?', (time.time(),)).fetchone()[0]
    return {'primary': primary, 'supadata_configured': configured, 'proxies_configured': proxies,
            'proxy_count': proxy_count, 'paused_routes': paused, 'local': local_audio.readiness(), 'warnings': list(dict.fromkeys(warnings))}
