import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

from . import db, generation, local_audio, media, source_cache, transcripts, youtube
from .security import get_secret
from .editorial import engine

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='editorial')
job_lock = threading.RLock()
ACTIVE = {'queued', 'extracting', 'analyzing', 'researching', 'writing', 'optimizing', 'reviewing'}


class ExtractionIncomplete(ValueError):
    def __init__(self, sources):
        failed = [s for s in sources if s['status'] != 'ok']
        self.pending = any(s['status'] == 'pending' for s in failed)
        # Group identical messages instead of repeating a proxy error for every video.
        groups = {}
        for index, source in enumerate(sources, 1):
            if source['status'] != 'ok':
                groups.setdefault(source.get('error') or 'Extração não concluída.', []).append(str(index))
        details = ' '.join(f'Vídeo(s) {", ".join(ids)}: {message}' for message, ids in groups.items())
        super().__init__('As fontes ainda não estão completas. ' + details + ' Confira a aba Fontes.')


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
    from . import cost_observability
    job = db.get_job(job_id)
    if not job:
        raise ValueError('Artigo não encontrado.')
    with cost_observability.run(job, scope='article', operation=mode,
                                pipeline_version=generation.EDITORIAL_VERSION,
                                dependency_fingerprint=generation.article_hash(
                                    {'brief': job.get('brief'), 'sources': job.get('sources')})) as execution:
        try:
            _run(job_id, mode)
        finally:
            from .editorial.human_knowledge_runtime import observe
            try:
                current = db.get_job(job_id) or job
            except Exception as exc:
                logger.warning('Human Knowledge source reload unavailable: %s', type(exc).__name__)
                current = job
            observe(current, phase='pipeline_finished')
        execution['outcome'] = current.get('status', 'unknown')


def _run(job_id, mode='generate'):
    job = db.get_job(job_id)
    try:
        if mode not in ('review', 'optimize'):
            local_audio.clean_cache()
            step(job, 'extracting', 'Obtendo o conteúdo dos links do YouTube.')
            sources = job.setdefault('sources', [])
            audio_only = transcripts.configuration()['provider'] == 'local'
            for index, url in enumerate(job['brief']['urls']):
                old = sources[index] if index < len(sources) else None
                captions_only = old and old.get('provider') in ('Legendas do YouTube', 'Legendas do YouTube via proxy', 'Supadata') and old.get('medium') != 'audio'
                if old and old.get('status') == 'ok' and not (audio_only and captions_only):
                    from .cost_observability import record_cache
                    record_cache(job, 'source_extraction', origin='job_source_cache',
                                 dependency_fingerprint=generation.article_hash(old),
                                 metadata={'source_id': old.get('id')})
                    continue
                vid = youtube.video_id(url)
                try:
                    upload = job.get('audio_uploads', {}).get(vid)
                    source = None if upload else source_cache.find_recent(
                        vid, f'v{index+1}', job_id, audio_only=audio_only, require_current=True)
                    if source:
                        from .cost_observability import record_cache
                        record_cache(job, 'source_extraction', origin='application_cache',
                                     dependency_fingerprint=generation.article_hash(source),
                                     metadata={'source_id': source.get('id')})
                        step(job, 'extracting', f'Vídeo {index+1}: transcrição automática recente reaproveitada do estúdio.')
                    else:
                        def progress(update):
                            if index < len(sources):
                                sources[index] = update
                            else:
                                sources.append(update)
                            step(job, 'extracting', f'Vídeo {index+1}: {update["extraction"]["message"]}')
                        source = youtube.extract(url, f'v{index+1}', db.get_setting('audio_fallback', False), progress=progress, uploaded=upload, financial_job=job)
                        if upload and old:
                            source.update({key: old[key] for key in ('title', 'author', 'thumbnail') if old.get(key)})
                        source.setdefault('extracted_at', db.now())
                except Exception as exc:
                    info = exc.info if isinstance(exc, youtube.SourceError) and exc.info else youtube.metadata(vid)
                    pending = isinstance(exc, youtube.SourceError) and exc.diagnostic['pending']
                    source = info | {'id': f'v{index+1}', 'status': 'pending' if pending else 'error',
                                     'error': safe_error(exc), 'segments': []}
                    if isinstance(exc, youtube.SourceError):
                        source['extraction'] = {'phase': 'pending' if pending else 'unavailable',
                                                'message': safe_error(exc), 'diagnostic': exc.diagnostic,
                                                'attempts': exc.attempts}
                if index < len(sources):
                    sources[index] = source
                else:
                    sources.append(source)
                if old and old.get('status') == 'ok' and old.get('segments') != source.get('segments'):
                    from .editorial import store
                    job.setdefault('source_history', []).append({'at': db.now(), 'source': old})
                    job['article_needs_generation'] = bool(job.get('article'))
                    job['generation_complete'] = False
                    store.archive_review(job, 'A transcrição do áudio mudou; a análise anterior permanece no histórico.')
                    job['review'] = None
                    store.invalidate(job, 'A base textual mudou com a transcrição do áudio.', upstream=True)
                db.save_job(job)
            if any(source['status'] != 'ok' for source in sources):
                raise ExtractionIncomplete(sources)
            total = sum(len(s['text']) for source in sources for s in source['segments'])
            if total > 180000:
                raise ValueError('O conjunto excede 180 mil caracteres. Divida os vídeos em artigos menores.')
            if mode == 'extract':
                job['error'] = None
                step(job, 'sources_ready', 'Conteúdo dos vídeos extraído. Pronto para gerar o artigo.')
                return
            if not get_secret('openai_api_key'):
                step(job, 'awaiting_key', 'Fontes prontas. Configure a chave OpenAI em Integrações e clique em Gerar artigo.')
                return
        job['review'] = engine.run(job, mode)
        if job['review'] is None:
            needs_input = (job.get('editorial', {}).get('decision') or {}).get('decision') == 'needs_input'
            step(job, 'needs_input' if needs_input else 'plan_ready',
                 'Plano preservado. Resolva as informações indispensáveis pendentes antes da redação.' if needs_input else
                 'Planejamento concluído. Confira as seções e clique em Redigir a partir do plano.')
            job['error'] = None
            db.save_job(job)
            return
        blocking = sum(f['severity'] == 'blocking' for f in job['review']['findings'])
        step(job, 'needs_review' if blocking else 'ready',
             'Artigo disponível para exportação. A análise editorial não foi concluída; o diagnóstico foi salvo.' if job['review'].get('review_incomplete') else
             f'Artigo disponível para exportação com {blocking} observação(ões) editorial(is).' if blocking else
             'Artigo pronto para exportação e envio ao WordPress.')
        job['error'] = None
        db.save_job(job)
    except Exception as exc:
        logger.warning('Pipeline %s failed: %s', job_id, type(exc).__name__)
        job['error'] = safe_error(exc)
        from .editorial.workflow import BudgetExceeded, NeedsInput
        from .spending import SpendLimitExceeded
        status = ('transcription_pending' if exc.pending else 'sources_unavailable') if isinstance(exc, ExtractionIncomplete) else 'budget_exhausted' if isinstance(exc, (BudgetExceeded, SpendLimitExceeded)) else 'needs_input' if isinstance(exc, NeedsInput) else 'error'
        step(job, status, job['error'])


def submit(job_id, mode='generate'):
    with job_lock:
        job = db.get_job(job_id)
        if job['status'] in ACTIVE or media.busy(job):
            raise ValueError('Este artigo já está em processamento.')
        if sum(j['status'] in ACTIVE for j in db.list_jobs()) >= 10:
            raise ValueError('A fila está cheia. Aguarde os artigos em andamento.')
        if mode in ('generate', 'plan', 'write') and job['status'] in {'error', 'interrupted', 'budget_exhausted'} and job.get('pipeline_editorial_version', 1) == generation.EDITORIAL_VERSION and job.get('editorial', {}).get('mode', 'generate') == mode:
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
