import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

from . import db, generation, source_cache, youtube
from .security import get_secret
from .editorial import engine

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='editorial')
job_lock = threading.RLock()
ACTIVE = {'queued', 'extracting', 'analyzing', 'researching', 'writing', 'optimizing', 'reviewing'}


def step(job, status, message):
    job['status'] = status
    job.setdefault('events', []).append({'time': db.now(), 'message': message})
    job['events'] = job['events'][-80:]
    db.save_job(job)


def safe_error(exc):
    if isinstance(exc, ValidationError):
        return generation.INVALID_RESPONSE_MESSAGE
    if isinstance(exc, AuthenticationError):
        return 'A chave OpenAI não foi aceita. Confira a chave em Integrações.'
    if isinstance(exc, RateLimitError):
        return 'A OpenAI informou limite de uso ou saldo insuficiente. Confira sua conta antes de tentar novamente.'
    if isinstance(exc, APIConnectionError):
        return 'Não foi possível conectar à OpenAI. Tente novamente em alguns minutos.'
    if isinstance(exc, APIStatusError):
        if getattr(exc, 'code', None) == 'invalid_json_schema':
            return 'A OpenAI não aceitou o contrato de resposta desta etapa. O texto foi preservado; o formato precisa ser corrigido no aplicativo.'
        return f'A OpenAI retornou HTTP {exc.status_code}. Confira o modelo configurado e o acesso da sua conta.'
    if isinstance(exc, ValueError):
        return public_error(str(exc))[:1000]
    return 'A etapa não pôde ser concluída. Seus dados foram preservados; tente novamente.'


def public_error(message):
    """Redact legacy validation dumps on read without mutating saved jobs."""
    if message and re.search(r'\b\d+ validation errors? for ', message) and (
            'input_value=' in message or 'errors.pydantic.dev' in message):
        return generation.INVALID_RESPONSE_MESSAGE
    return message


def run(job_id, mode='generate'):
    job = db.get_job(job_id)
    try:
        if mode not in ('review', 'optimize'):
            step(job, 'extracting', 'Obtendo o conteúdo dos links do YouTube.')
            sources = job.setdefault('sources', [])
            for index, url in enumerate(job['brief']['urls']):
                if index < len(sources) and sources[index].get('status') == 'ok':
                    continue
                vid = youtube.video_id(url)
                try:
                    source = source_cache.find_recent(vid, f'v{index+1}', job_id)
                    if source:
                        step(job, 'extracting', f'Vídeo {index+1}: transcrição automática recente reaproveitada do estúdio.')
                    else:
                        source = youtube.extract(url, f'v{index+1}', db.get_setting('audio_fallback', False))
                        source['extracted_at'] = db.now()
                except Exception as exc:
                    source = youtube.metadata(vid) | {'id': f'v{index+1}', 'status': 'error',
                                                      'error': safe_error(exc), 'segments': []}
                if index < len(sources):
                    sources[index] = source
                else:
                    sources.append(source)
                db.save_job(job)
            if any(source['status'] != 'ok' for source in sources):
                details = ' '.join(f'Vídeo {i+1}: {source.get("error", "extração não concluída")}'
                                   for i, source in enumerate(sources) if source['status'] != 'ok')
                raise ValueError('A extração não foi concluída. ' + details)
            total = sum(len(s['text']) for source in sources for s in source['segments'])
            if total > 180000:
                raise ValueError('O conjunto excede 180 mil caracteres. Divida os vídeos em artigos menores.')
            if mode == 'extract':
                step(job, 'sources_ready', 'Conteúdo dos vídeos extraído. Pronto para gerar o artigo.')
                return
            if not get_secret('openai_api_key'):
                step(job, 'awaiting_key', 'Fontes prontas. Configure a chave OpenAI em Integrações e clique em Gerar artigo.')
                return
        job['review'] = engine.run(job, mode)
        blocking = sum(f['severity'] == 'blocking' for f in job['review']['findings'])
        step(job, 'needs_review' if blocking else 'ready',
             f'Revisão concluída: {blocking} pendência(s) editorial(is).' if blocking else 'Artigo pronto para sua revisão editorial e envio.')
        job['error'] = None
        db.save_job(job)
    except Exception as exc:
        logger.warning('Pipeline %s failed: %s', job_id, type(exc).__name__)
        job['error'] = safe_error(exc)
        step(job, 'error', job['error'])


def submit(job_id, mode='generate'):
    with job_lock:
        job = db.get_job(job_id)
        if job['status'] in ACTIVE:
            raise ValueError('Este artigo já está em processamento.')
        if sum(j['status'] in ACTIVE for j in db.list_jobs()) >= 10:
            raise ValueError('A fila está cheia. Aguarde os artigos em andamento.')
        if mode == 'generate' and job['status'] in {'error', 'interrupted'} and job.get('pipeline_editorial_version', 1) == generation.EDITORIAL_VERSION:
            mode = 'resume'
        elif mode == 'generate':
            job['generation_complete'] = False
            job.pop('dossier', None)
            job.pop('research', None)
            job.pop('research_audit', None)
        job['pipeline_editorial_version'] = generation.EDITORIAL_VERSION
        job['error'] = None
        step(job, 'queued', 'Artigo adicionado à fila de processamento.')
        executor.submit(run, job_id, mode)


def recover():
    for job in db.list_jobs():
        if job['status'] in ACTIVE:
            job['error'] = 'O serviço reiniciou durante o processamento. Retome a geração; chamadas de IA podem ser repetidas.'
            step(job, 'interrupted', job['error'])
