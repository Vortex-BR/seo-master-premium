import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

from . import db, generation, youtube
from .security import get_secret

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='editorial')
job_lock = threading.RLock()
ACTIVE = {'queued', 'extracting', 'analyzing', 'researching', 'writing', 'reviewing'}


def step(job, status, message):
    job['status'] = status
    job.setdefault('events', []).append({'time': db.now(), 'message': message})
    job['events'] = job['events'][-80:]
    db.save_job(job)


def safe_error(exc):
    if isinstance(exc, AuthenticationError):
        return 'A chave OpenAI não foi aceita. Confira a chave em Integrações.'
    if isinstance(exc, RateLimitError):
        return 'A OpenAI informou limite de uso ou saldo insuficiente. Confira sua conta antes de tentar novamente.'
    if isinstance(exc, APIConnectionError):
        return 'Não foi possível conectar à OpenAI. Tente novamente em alguns minutos.'
    if isinstance(exc, APIStatusError):
        return f'A OpenAI retornou HTTP {exc.status_code}. Confira o modelo configurado e o acesso da sua conta.'
    if isinstance(exc, ValueError):
        return str(exc)[:1000]
    return 'A etapa não pôde ser concluída. Seus dados foram preservados; tente novamente.'


def run(job_id, mode='generate'):
    job = db.get_job(job_id)
    try:
        if mode != 'review':
            step(job, 'extracting', 'Obtendo o conteúdo dos links do YouTube.')
            sources = job.setdefault('sources', [])
            for index, url in enumerate(job['brief']['urls']):
                if index < len(sources) and sources[index].get('status') == 'ok':
                    continue
                vid = youtube.video_id(url)
                try:
                    source = youtube.extract(url, f'v{index+1}', db.get_setting('audio_fallback', False))
                except Exception as exc:
                    source = youtube.metadata(vid) | {'id': f'v{index+1}', 'status': 'error',
                                                      'error': safe_error(exc), 'segments': []}
                if index < len(sources):
                    sources[index] = source
                else:
                    sources.append(source)
                db.save_job(job)
            if any(source['status'] != 'ok' for source in sources):
                raise ValueError('Um ou mais vídeos não puderam ser processados. Consulte a aba Fontes para resolver e tentar novamente.')
            total = sum(len(s['text']) for source in sources for s in source['segments'])
            if total > 180000:
                raise ValueError('O conjunto excede 180 mil caracteres. Divida os vídeos em artigos menores.')
            if mode == 'extract':
                step(job, 'sources_ready', 'Conteúdo dos vídeos extraído. Pronto para gerar o artigo.')
                return
            if not get_secret('openai_api_key'):
                step(job, 'awaiting_key', 'Fontes prontas. Configure a chave OpenAI em Integrações e clique em Gerar artigo.')
                return
            step(job, 'analyzing', 'Organizando as ideias, exemplos e evidências dos vídeos.')
            job['dossier'] = generation.extract_dossier(job)
            db.save_job(job)
            if job['brief']['research']:
                step(job, 'researching', 'Pesquisando lacunas e informações que precisam de atualização.')
                job['research'] = generation.research(job)
            else:
                job['research'] = {'text': '', 'sources': [], 'notice': 'Pesquisa complementar desativada neste artigo.'}
            step(job, 'writing', 'Escrevendo o artigo com referências rastreáveis.')
            db.revision(job)
            job['article'] = generation.write_article(job)
            job['review'] = None
            db.save_job(job)
        step(job, 'reviewing', 'Conferindo afirmações, atribuições e fontes do artigo.')
        job['review'] = generation.review_article(job)
        blocking = sum(f['severity'] == 'blocking' for f in job['review']['findings'])
        step(job, 'needs_review' if blocking else 'ready',
             f'Revisão concluída: {blocking} pendência(s) factual(is).' if blocking else 'Artigo pronto para sua revisão editorial e envio.')
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
        job['error'] = None
        step(job, 'queued', 'Artigo adicionado à fila de processamento.')
        executor.submit(run, job_id, mode)


def recover():
    for job in db.list_jobs():
        if job['status'] in ACTIVE:
            job['error'] = 'O serviço reiniciou durante o processamento. Retome a geração; chamadas de IA podem ser repetidas.'
            step(job, 'interrupted', job['error'])
