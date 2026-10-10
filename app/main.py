import html
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import db, generation, image_generation, image_references, local_audio, media, pipeline, publishing, transcripts, wordpress, youtube
from .editorial import agents, changes, delivery, source_processing, workflow, store as editorial_store
from .editorial.contracts import ChangeDecision, IssueResolution, PlanUpdate, ProfileUpdate
from .seo import knowledge
from .strategy import agents as strategy_agents, engine as strategy_engine, production as strategy_production, store as strategy_store
from .strategy.contracts import OpportunityDecision, OpportunityProduce, StrategyRequest
from .schemas import ArticleEdit, Brief, EditorialDirection, ExportRequest, ImageDetails, ImageGeneration, ImageReferenceSearch, Login, ManualSource, PasswordChange, ReviewDecision, Settings, TranscriptReset
from .security import (SECRET_KEYS, check_password, get_secret, hash_password, init_auth,
                       public_https_url, require_auth, require_readonly_auth, save_secret, session_hash, validate_proxies)

STATIC = Path(__file__).parent / 'static'


@asynccontextmanager
async def lifespan(app):
    db.init()
    editorial_store.init()
    strategy_store.init()
    knowledge.init()
    init_auth()
    # This application runs one worker. Recover uncertain billing before opening
    # its queues; a restart never proves that a provider request was unbilled.
    from .cost_observability import recover_inflight
    recover_inflight()
    pipeline.recover()
    image_generation.recover()
    strategy_engine.recover()
    yield


app = FastAPI(title='SEO MASTER PREMIUM', version='1.5.26', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protections(request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.headers.get('x-requested-with') != 'SEO-Master':
            return JSONResponse({'detail': 'Solicitação inválida.'}, status_code=403)
        origin = request.headers.get('origin')
        expected = os.getenv('APP_URL', '').rstrip('/')
        if origin and origin.rstrip('/') != (expected or str(request.base_url).rstrip('/')):
            return JSONResponse({'detail': 'Origem não permitida.'}, status_code=403)
        raw_length = request.headers.get('content-length', '0') or '0'
        if not raw_length.isdigit():
            return JSONResponse({'detail': 'Tamanho do conteúdo inválido.'}, status_code=400)
        audio_upload = request.method == 'POST' and re.fullmatch(r'/api/jobs/[^/]+/sources/[A-Za-z0-9_-]{11}/audio', request.url.path)
        if not audio_upload and int(raw_length) > 1500000:
            return JSONResponse({'detail': 'O conteúdo excede o limite permitido.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    is_preview = request.url.path.startswith('/api/jobs/') and request.url.path.endswith('/preview')
    response.headers['X-Frame-Options'] = 'SAMEORIGIN' if is_preview else 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    ancestors = "'self'" if is_preview else "'none'"
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://i.ytimg.com https://images.pexels.com https://pixabay.com https://cdn.pixabay.com data:; connect-src 'self'; frame-src 'self'; frame-ancestors " + ancestors + "; form-action 'self'; base-uri 'none'"
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Avoid reflecting request bodies or integration secrets in errors.
    fields = ', '.join('.'.join(str(p) for p in e['loc'][1:]) for e in exc.errors())
    return JSONResponse({'detail': f'Confira os campos informados: {fields}.'}, status_code=422)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': pipeline.safe_error(exc)}, status_code=400)


@app.get('/health')
def health():
    with db.connect() as c:
        c.execute('SELECT 1')
    return {'status': 'ok', 'app': 'SEO MASTER PREMIUM', 'version': app.version, 'editorial_version': generation.EDITORIAL_VERSION}


@app.post('/api/login')
def login(body: Login, request: Request, response: Response):
    ip = request.client.host if request.client else 'unknown'
    current = time.time()
    with db.connect() as c:
        c.execute('DELETE FROM login_attempts WHERE created < ?', (current-900,))
        count = c.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0]
        if count >= 10:
            raise HTTPException(429, 'Muitas tentativas. Aguarde 15 minutos.')
        c.execute('INSERT INTO login_attempts VALUES (?,?)', (ip, current))
    if not check_password(body.password):
        raise HTTPException(401, 'Senha incorreta.')
    token = secrets.token_urlsafe(48)
    with db.connect() as c:
        c.execute('DELETE FROM sessions WHERE expires < ?', (current,))
        c.execute('INSERT INTO sessions VALUES (?,?)', (session_hash(token), current+86400))
        c.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
    response.set_cookie('seo_session', token, httponly=True, secure=os.getenv('COOKIE_SECURE', '0') == '1',
                        samesite='strict', max_age=86400, path='/')
    return {'ok': True}


api = APIRouter(prefix='/api', dependencies=[Depends(require_auth)])
cost_api = APIRouter(prefix='/api', dependencies=[Depends(require_readonly_auth)])


@api.get('/me')
def me():
    return {'name': 'Administrador', 'brand': db.get_setting('brand_name', '')}


@api.post('/logout')
def logout(request: Request, response: Response):
    with db.connect() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (session_hash(request.cookies.get('seo_session', '')),))
    response.delete_cookie('seo_session', path='/')
    return {'ok': True}


@api.post('/password')
def change_password(body: PasswordChange, response: Response):
    if not check_password(body.current_password):
        raise HTTPException(400, 'A senha atual não confere.')
    db.set_setting('password_hash', hash_password(body.new_password))
    with db.connect() as c:
        c.execute('DELETE FROM sessions')
    response.delete_cookie('seo_session', path='/')
    return {'ok': True}


@api.get('/settings')
def settings():
    defaults = Settings().model_dump()
    result = {key: db.get_setting(key, value) for key, value in defaults.items() if key not in SECRET_KEYS}
    result['model'] = db.get_setting('model', os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
    result['image_model'] = db.get_setting('image_model', 'gpt-image-2')
    config = transcripts.configuration()
    result.update(transcript_provider=config['provider'], supadata_mode=config['mode'], transcript_timeout=config['timeout'])
    local_config = local_audio.configuration()
    result.update(whisper_model=local_config['model'], whisper_threads=local_config['threads'],
                  audio_max_minutes=local_config['max_minutes'], audio_max_mb=local_config['max_mb'],
                  local_transcript_timeout=local_config['timeout'])
    result['transcription_status'] = transcripts.readiness()
    result['youtube_connection_mode'] = local_audio.connection_mode()
    result.update({key + '_configured': bool(get_secret(key)) for key in SECRET_KEYS})
    return result


@api.put('/settings')
def update_settings(body: Settings):
    if body.wp_url:
        public_https_url(body.wp_url)
    if body.youtube_proxy_urls:
        body.youtube_proxy_urls = validate_proxies(body.youtube_proxy_urls)
    for key, value in body.model_dump().items():
        if key in SECRET_KEYS:
            if value is not None and value.strip():
                save_secret(key, value.strip())
        else:
            db.set_setting(key, value)
    return settings()


@api.post('/settings/test-openai')
def test_openai():
    from . import cost_observability, spending
    # A button click authorizes one bounded test, outside all article histories.
    # Its reservation survives a timeout and is never silently retried by the SDK.
    request_model = generation.model()
    budget = Settings(connection_test_budget_usd=db.get_setting('connection_test_budget_usd', 0.01)).connection_test_budget_usd
    financial_job = {'id': 'connection-test:' + uuid.uuid4().hex, 'usage': [],
                     'financial_budget_usd': budget}
    request = {'model': request_model, 'input': 'Responda apenas OK.',
               'max_output_tokens': 32, 'store': False}
    try:
        with cost_observability.run(financial_job, scope='connection_test', operation='test_openai',
                                    pipeline_version=app.version,
                                    dependency_fingerprint=generation.article_hash(request)) as execution:
            with generation.client() as client:
                response, receipt = spending.create_response(financial_job, client, request, 'connection_test')
            execution['outcome'] = response.status
        return {'ok': response.status == 'completed', 'model': request_model,
                'run_id': execution['id'], 'reservation_id': receipt['id'],
                'financial_state': receipt['state'], 'calculated_usd': receipt.get('calculated_usd')}
    except Exception as exc:
        raise ValueError(pipeline.safe_error(exc)) from None


@cost_api.get('/costs/report')
def cost_report(job_id: str | None = None, run_id: str | None = None):
    if os.getenv('COST_REPORTS_ENABLED', '1').strip().lower() in ('0', 'false', 'off'):
        raise HTTPException(503, 'O relatório financeiro está desativado; o controle de orçamento permanece ativo.')
    from .cost_observability import read_report
    database = Path(os.getenv('DATA_DIR', './data')) / 'seo.sqlite3'
    return read_report(database, job_id=job_id, run_id=run_id)


@cost_api.get('/jobs/{job_id}/cost-report')
def article_cost_report(job_id: str, run_id: str | None = None):
    from .cost_observability import readonly_snapshot
    database = Path(os.getenv('DATA_DIR', './data')) / 'seo.sqlite3'
    with readonly_snapshot(database) as (c, _):
        exists = c.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not exists:
        raise HTTPException(404, 'Artigo não encontrado.')
    return cost_report(job_id, run_id)


@api.post('/settings/test-wordpress')
def test_wordpress():
    return wordpress.test_connection()


def get_job(job_id):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, 'Artigo não encontrado.')
    return job


@api.post('/jobs/{job_id}/sources/{video_id}/test-access')
def test_source_access(job_id: str, video_id: str):
    job = get_job(job_id)
    inactive(job)
    if video_id not in [youtube.video_id(url) for url in job['brief']['urls']]:
        raise ValueError('O vídeo não pertence a este artigo.')
    return local_audio.access_test(video_id)


@api.get('/jobs/{job_id}/sources/{video_id}/diagnostics')
def source_diagnostics(job_id: str, video_id: str):
    job = get_job(job_id)
    if video_id not in [youtube.video_id(url) for url in job['brief']['urls']]:
        raise HTTPException(404, 'O vídeo não pertence a este artigo.')
    return local_audio.diagnostics(video_id, job.get('audio_uploads', {}).get(video_id))


def inactive(job):
    if job['status'] in pipeline.ACTIVE or media.busy(job):
        raise HTTPException(409, 'Aguarde a etapa atual terminar antes de editar.')


@api.get('/jobs')
def list_jobs():
    result = []
    for job in db.list_jobs():
        article = job.get('article') if isinstance(job.get('article'), dict) else {}
        markdown = article.get('markdown')
        result.append({key: job.get(key) for key in ('id', 'status', 'created_at', 'updated_at', 'wordpress')}
                      | {'error': pipeline.public_error(job.get('error')),
                         'title': article.get('title') or job['brief']['topic'] or 'Artigo a partir de vídeo',
                         'keyword': job['brief']['keyword'], 'source_count': len(job['brief']['urls']),
                         'word_count': len(markdown.split()) if isinstance(markdown, str) else 0}
                      | delivery.describe(job))
    return result


@api.post('/jobs', status_code=201)
def create_job(body: Brief):
    with pipeline.job_lock:
        if sum(j['status'] in pipeline.ACTIVE for j in db.list_jobs()) >= 10:
            raise HTTPException(429, 'A fila está cheia. Aguarde os artigos em andamento.')
        job = {'id': uuid.uuid4().hex, 'status': 'new', 'created_at': db.now(), 'brief': body.model_dump(),
               'sources': [], 'events': [], 'usage': [], 'error': None}
        db.save_job(job)
        pipeline.submit(job['id'], 'extract' if body.extract_only else 'generate')
    return {'id': job['id']}


@api.get('/jobs/{job_id}')
def detail(job_id: str, response: Response):
    job = get_job(job_id)
    job.update(delivery.describe(job))
    from . import spending
    job['spending'] = spending.summary(job, persist=False)
    job['error'] = pipeline.public_error(job.get('error'))
    for event in job.get('events', []):
        event['message'] = pipeline.public_error(event.get('message'))
    job['previous_editorial_version'] = bool(job.get('article') and
        job.get('article_editorial_version', 1) < generation.EDITORIAL_VERSION)
    job['previous_evidence_flow'] = bool(job.get('article') and job.get('article_evidence_version', 0) < workflow.VERSION)
    diagnostic_job = job if job['export_available'] else {**job, 'article': None}
    job['checks'] = generation.seo_checks(diagnostic_job)
    job['article_hash'] = generation.article_hash(job['article']) if job.get('article') else None
    if job['article_hash']:
        response.headers['ETag'] = '"' + job['article_hash'] + '"'
    response.headers['Cache-Control'] = 'private, no-store'
    job['evidence'] = generation.evidence_map(job)
    job['images'] = [media.public_item(diagnostic_job, item) for item in job.get('media', [])]
    job['image_positions'] = media.headings(diagnostic_job)
    job['image_busy'] = media.busy(job)
    job['editorial_flow'] = 'evidence' if workflow.enabled() else 'legacy'
    job['editorial_issues'] = editorial_store.issues(job)
    job['coverage_current'] = bool(job.get('coverage') and job['coverage'].get('article_hash') == job['article_hash'])
    if job.get('review'):
        for claim in job['review'].get('supported_claims', []):
            for evidence in claim['evidence']:
                source = job['evidence'].get(evidence['source_id'])
                evidence['excerpt_verified'] = bool(source and evidence['excerpt'].strip() and
                    generation.normalize(evidence['excerpt']) in generation.normalize(source['text']))
    return job


@api.put('/jobs/{job_id}/brief')
def edit_brief(job_id: str, body: EditorialDirection):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        direction = body.model_dump()
        if EditorialDirection.model_validate(job['brief']).model_dump() == direction:
            return {'ok': True, 'changed': False}
        editorial_store.archive_review(job, 'A direção editorial mudou.')
        job.setdefault('brief_history', []).append({'at': db.now(), 'brief': job['brief'].copy()})
        job['brief'].update(direction)
        for key in ('dossier', 'research', 'research_audit', 'research_requests_completed'):
            job.pop(key, None)
        job['generation_complete'] = False
        job['article_needs_generation'] = bool(job.get('article'))
        job['review'] = None
        editorial_store.invalidate(job, 'A direção editorial mudou.', upstream=True)
        job['error'] = None
        pipeline.step(job, 'brief_updated', 'Direção do artigo atualizada. Clique em Gerar artigo para aplicar ao texto. Salvar não consome a API OpenAI.')
    return {'ok': True, 'changed': True}


@api.post('/jobs/{job_id}/generate')
def generate(job_id: str):
    get_job(job_id)
    pipeline.submit(job_id)
    return {'ok': True}


@api.get('/jobs/{job_id}/estimate')
def editorial_estimate(job_id: str):
    job = get_job(job_id)
    if not job.get('sources') or any(s.get('status') != 'ok' for s in job['sources']):
        return {'available': False, 'notice': 'Obtenha as transcrições para estimar o tamanho e as chamadas.'}
    return {'available': True, **source_processing.estimate(job, editorial_store.profile(persist=False)['profile'])}


@api.post('/jobs/{job_id}/plan')
def plan_article(job_id: str):
    get_job(job_id)
    if not workflow.enabled():
        raise ValueError('Ative o fluxo de evidências para usar o planejamento estruturado.')
    pipeline.submit(job_id, 'plan')
    return {'ok': True}


@api.post('/jobs/{job_id}/write')
def write_article_from_plan(job_id: str):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        plan = job.get('plan') or {}
        if not plan.get('valid') or plan.get('input_version') != editorial_store.inputs_version(job):
            raise ValueError('Planeje este artigo antes de redigir.')
        if not get_secret('openai_api_key'):
            raise ValueError('Configure a chave OpenAI em Integrações.')
        pipeline.submit(job_id, 'write')
    return {'ok': True}


@api.put('/jobs/{job_id}/plan')
def edit_article_plan(job_id: str, body: PlanUpdate):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        plan = job.get('plan') or {}
        if not plan.get('valid') or plan.get('version') != body.base_version or plan.get('input_version') != editorial_store.inputs_version(job):
            raise HTTPException(409, 'O plano ou as fontes mudaram. Atualize a página antes de salvar.')
        workflow.save_plan(job, body.plan.model_dump(), manual=True)
        job['generation_complete'] = False
        job['article_needs_generation'] = bool(job.get('article'))
        editorial_store.invalidate(job, 'O planejamento foi editado. Redija a partir da nova versão.')
        pipeline.step(job, 'plan_ready', 'Plano atualizado. Salvar não chama a OpenAI. Redija para aplicar ao artigo.')
    return {'ok': True, 'version': job['plan']['version']}


@api.get('/jobs/{job_id}/artifacts')
def editorial_artifacts(job_id: str, kind: str | None = None):
    get_job(job_id)
    return editorial_store.artifacts(job_id, kind)


@api.post('/jobs/{job_id}/issues/{issue_id}/resolve')
def resolve_editorial_issue(job_id: str, issue_id: str, body: IssueResolution):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        if len(body.reason.strip()) < 20:
            raise ValueError('Registre uma justificativa com pelo menos 20 caracteres.')
        item = editorial_store.resolve_issue(job, issue_id, body.reason.strip(), body.source_ids)
        editorial_store.archive_review(job, 'Uma observação editorial foi resolvida.')
        job['review'] = None
        if job.get('apuration'):
            job['apuration']['pending'] = [i for i in editorial_store.issues(job) if i['status'] == 'open']
        pipeline.step(job, 'plan_ready' if job.get('plan', {}).get('valid') else 'needs_review',
                      'Resolução editorial registrada. Confira o plano e execute redação ou revisão novamente.')
    return item


@api.post('/jobs/{job_id}/extract')
def retry_extraction(job_id: str):
    get_job(job_id)
    pipeline.submit(job_id, 'extract')
    return {'ok': True}


@api.post('/jobs/{job_id}/sources/{video_id}/reset-transcription')
def reset_transcription(job_id: str, video_id: str, body: TranscriptReset):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        if not body.confirm_new_request:
            raise ValueError('Confirme a criação de uma nova solicitação; o provedor pode cobrar novamente.')
        if not any(source.get('video_id') == video_id and source['status'] != 'ok' for source in job.get('sources', [])):
            raise ValueError('Selecione uma fonte indisponível deste artigo.')
        return transcripts.reset_request(video_id)


@api.post('/jobs/{job_id}/sources/{video_id}/audio', status_code=202)
async def upload_source_audio(job_id: str, video_id: str, request: Request, filename: str):
    # Stream raw media instead of buffering a multipart body before enforcing limits.
    if len(filename) > 200 or Path(filename).suffix.lower() not in local_audio.FORMATS:
        raise ValueError('Envie um arquivo MP3, WAV, M4A, MP4, WebM, OGG, FLAC ou AAC.')
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        if video_id not in [youtube.video_id(url) for url in job['brief']['urls']]:
            raise ValueError('O vídeo não pertence a este artigo.')
    config = local_audio.configuration()
    maximum = config['max_mb'] * 1024 * 1024
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or int(length) > maximum):
        raise HTTPException(413, f'Envie um arquivo de até {config["max_mb"]} MB.')
    identifier = uuid.uuid4().hex
    folder = local_audio.safe_path(identifier)
    suffix = Path(filename).suffix.lower()
    destination = folder / ('original' + suffix)
    saved = False
    try:
        size = 0
        with destination.open('xb') as stream:
            destination.chmod(0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, f'Envie um arquivo de até {config["max_mb"]} MB.')
                stream.write(chunk)
        duration = await run_in_threadpool(local_audio.probe, destination)
        with pipeline.job_lock:
            job = get_job(job_id)
            inactive(job)
            upload = {'id': identifier, 'suffix': suffix, 'bytes': size, 'duration': duration, 'at': db.now()}
            job.setdefault('audio_uploads', {})[video_id] = upload
            sources = job.setdefault('sources', [])
            for index, url in enumerate(job['brief']['urls']):
                if index >= len(sources):
                    vid = youtube.video_id(url)
                    sources.append({'id': f'v{index+1}', 'video_id': vid, 'url': url,
                                    'title': f'Vídeo {index+1}', 'author': '', 'thumbnail': '',
                                    'status': 'error', 'error': 'Áudio ainda não obtido.', 'segments': []})
                if sources[index]['video_id'] == video_id:
                    job.setdefault('source_history', []).append({'at': db.now(), 'source': sources[index].copy()})
                    sources[index] = sources[index] | {'status': 'uploaded', 'segments': [], 'error': None,
                        'extraction': {'phase': 'audio_uploaded', 'message': 'Áudio enviado. Iniciando a transcrição local.', 'attempts': []}}
            editorial_store.archive_review(job, 'Um novo arquivo de áudio foi fornecido para a fonte.')
            job['review'] = None
            job['generation_complete'] = False
            job['article_needs_generation'] = bool(job.get('article'))
            editorial_store.invalidate(job, 'Um novo arquivo de áudio foi fornecido para a fonte.', upstream=True)
            db.save_job(job)
            saved = True
            pipeline.submit(job_id, 'extract')
    finally:
        if not saved:
            destination.unlink(missing_ok=True)
            folder.rmdir()
    return {'ok': True, 'bytes': size, 'duration': duration}


@api.get('/jobs/{job_id}/sources/{video_id}/audio')
def uploaded_audio_file(job_id: str, video_id: str):
    upload = get_job(job_id).get('audio_uploads', {}).get(video_id)
    if not upload:
        raise HTTPException(404, 'Não há arquivo de áudio enviado para esta fonte.')
    return FileResponse(local_audio.uploaded_path(upload), headers={'Cache-Control': 'private, no-store'})


@api.post('/jobs/{job_id}/review')
def review(job_id: str):
    job = get_job(job_id)
    delivery.ensure_exportable(job)
    if not get_secret('openai_api_key'):
        raise ValueError('Configure a chave OpenAI em Integrações.')
    pipeline.submit(job_id, 'review')
    return {'ok': True}


@api.put('/jobs/{job_id}/article')
def edit_article(job_id: str, body: ArticleEdit, request: Request):
    with pipeline.job_lock, db.job_transaction(job_id) as job:
        if job is None:
            raise HTTPException(404, 'Artigo não encontrado.')
        inactive(job)
        header = request.headers.get('if-match')
        header_hash = None
        if header is not None:
            if not re.fullmatch(r'"[a-f0-9]{64}"', header):
                raise HTTPException(400, 'If-Match deve conter o ETag da versão carregada do artigo.')
            header_hash = header[1:-1]
        if body.base_article_hash and header_hash and body.base_article_hash != header_hash:
            raise HTTPException(400, 'A versão do formulário difere do ETag informado.')
        base = body.base_article_hash or header_hash
        if not base:
            raise HTTPException(428, 'Informe a versão carregada do artigo antes de salvar.')
        if not job.get('article') or base != generation.article_hash(job['article']):
            raise HTTPException(409, 'O artigo mudou desde que você abriu o editor. Seu rascunho foi preservado; confira a versão salva antes de aplicar suas alterações.')
        article = delivery.ensure_exportable({'article': body.model_dump(exclude={'base_article_hash'})})
        editorial_store.archive_review(job, 'O artigo foi editado manualmente.')
        db.revision(job)
        job['article'] = article
        job['review'] = None
        # Manual edits promote the visible draft into the user's current version.
        # Invalidation below prevents a resumed writer overwriting that edit.
        draft_delivery = job.pop('draft_delivery', None)
        if (draft_delivery and draft_delivery.get('inputs_version') == editorial_store.inputs_version(job)
                and draft_delivery.get('plan_version') == (job.get('plan') or {}).get('version')):
            job.update(generation_complete=True, article_needs_generation=False)
        editorial_store.invalidate(job, 'O artigo foi editado manualmente.')
        pipeline.step(job, 'ready', 'Artigo salvo e disponível para exportação. A análise editorial é opcional.')
    return {'ok': True, 'article_hash': generation.article_hash(article)}


@api.post('/jobs/{job_id}/review/decision')
def decide_review(job_id: str, body: ReviewDecision):
    if len(body.reason.strip()) < 20:
        raise ValueError('Registre uma justificativa com pelo menos 20 caracteres.')
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        review = job.get('review') or {}
        if not job.get('article') or body.article_hash != generation.article_hash(job['article']) or review.get('article_hash') != body.article_hash:
            raise HTTPException(409, 'O artigo mudou. Execute a revisão da versão atual.')
        if review.get('reviewed_at') != body.review_version:
            raise HTTPException(409, 'A revisão mudou. Atualize a página antes de registrar a decisão.')
        findings = review.get('findings', [])
        if body.finding_index >= len(findings):
            raise ValueError('Apontamento não encontrado.')
        finding = findings[body.finding_index]
        deterministic = generation.deterministic_findings(job)
        if body.dismiss and (finding.get('origin') == 'validation' or any(
                item['reason'] == finding['reason'] and item['passage'] == finding['passage'] for item in deterministic)):
            raise ValueError('Corrija a referência ou a estrutura no artigo; essa validação não pode ser dispensada.')
        decision = {'dismissed': body.dismiss, 'reason': body.reason.strip(), 'at': db.now(),
                    'actor': 'Administrador', 'article_hash': body.article_hash, 'review_version': body.review_version}
        finding['resolution'] = decision
        review.setdefault('decision_history', []).append({'finding_index': body.finding_index, **decision})
        status = 'ready'
        pipeline.step(job, status, 'Decisão editorial registrada no apontamento ' + str(body.finding_index+1) + ': ' + body.reason.strip())
    return {'ok': True, 'status': status}


@api.post('/jobs/{job_id}/source')
def manual_source(job_id: str, body: ManualSource):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        ids = [youtube.video_id(url) for url in job['brief']['urls']]
        if body.video_id not in ids:
            raise ValueError('Este vídeo não pertence ao briefing.')
        index = ids.index(body.video_id)
        if index >= len(job['sources']):
            raise ValueError('Aguarde a primeira tentativa de extração antes de adicionar uma transcrição.')
        # Validate the entire caption document before archiving or changing sources.
        segments = youtube.manual_segments(body.text, f'v{index+1}')
        source = job['sources'][index]
        editorial_store.archive_review(job, 'As fontes do artigo mudaram.')
        job.setdefault('source_history', []).append({'at': db.now(), 'source': json.loads(json.dumps(source))})
        source.update(segments=segments, provider='Transcrição fornecida pelo usuário',
                      status='ok', error=None, language='', generated_captions=None, extracted_at=db.now(),
                      notice='Texto fornecido pelo usuário; não validado contra o vídeo.')
        for key in ('extraction', 'medium', 'transcription_model', 'transcription_warnings',
                    'audio_sha256', 'audio_duration', 'input_origin', 'original_source_id',
                    'reused_from_source_id', 'reused_from_job_id', 'reused_at',
                    'provider_adapter_version', 'provider_cache_legacy'):
            source.pop(key, None)
        source.update(normalization_version=youtube.NORMALIZATION_VERSION,
                      normalization_options=youtube.normalization_options(), input_origin='manual_text')
        job['review'] = None
        job.pop('dossier', None)
        job.pop('research', None)
        job.pop('research_requests_completed', None)
        job['generation_complete'] = False
        job['article_needs_generation'] = bool(job.get('article'))
        editorial_store.invalidate(job, 'As fontes do artigo mudaram.', upstream=True)
        pipeline.step(job, 'needs_review' if job.get('article') else 'sources_ready', 'Transcrição alternativa salva. Gere novamente para usar o novo material.')
    return {'ok': True}


@api.get('/jobs/{job_id}/revisions')
def revisions(job_id: str):
    get_job(job_id)
    return db.revisions(job_id)


@api.get('/jobs/{job_id}/export')
def export(job_id: str, request: Request, format: str = 'html'):
    job = get_job(job_id)
    delivery.ensure_exportable(job)
    if format not in ('html', 'markdown', 'json', 'wordpress', 'wordpress-html'):
        raise ValueError('Formato de exportação inválido.')
    if format == 'wordpress':
        base = (os.getenv('APP_URL') or str(request.base_url)).rstrip('/')
        return Response(publishing.wxr(job, base), media_type='application/xml',
                        headers={'Content-Disposition': f'attachment; filename="wordpress-{job_id[:8]}.xml"'})
    if format == 'wordpress-html':
        synced = {m['id']: m.get('wordpress', {}) for m in media.active_images(job)}
        if any(not item.get('url') or item.get('site') != db.get_setting('wp_url', '').rstrip('/') for item in synced.values()):
            raise ValueError('Para levar as imagens sem links temporários, use o XML WordPress ou envie a postagem ao WordPress antes de baixar os blocos HTML.')
        return Response(publishing.render(job, gutenberg=True, image_urls={k: v['url'] for k, v in synced.items()},
                        image_ids={k: v['id'] for k, v in synced.items()}), media_type='text/html',
                        headers={'Content-Disposition': f'attachment; filename="blocos-wordpress-{job_id[:8]}.html"'})
    if format == 'json':
        return Response(json.dumps({'article': job['article'], 'evidence': generation.evidence_map(job), 'review': job.get('review'),
                                    'brief': job['brief'], 'plan': job.get('plan'), 'apuration': job.get('apuration'),
                                    'coverage': job.get('coverage'), 'draft_delivery': job.get('draft_delivery'),
                                    'editorial_issues': editorial_store.issues(job),
                                    'images': [media.public_item(job, m) for m in job.get('media', [])], 'yoast_meta': publishing.yoast_meta(job),
                                    **delivery.describe(job)},
                                   ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="artigo-{job_id[:8]}.json"'})
    if format == 'markdown':
        return Response('# ' + job['article']['title'] + '\n\n' + job['article']['markdown'], media_type='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename="artigo-{job_id[:8]}.md"'})
    article = job['article']
    document = ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>' + html.escape(article['seo_title']) +
                '</title><meta name="description" content="' + html.escape(article['meta_description'], quote=True) +
                '"><style>img{max-width:100%;height:auto}figure{margin:1.5em 0}</style></head><body><article><h1>' + html.escape(article['title']) + '</h1>' + publishing.render(job, embedded=True) + '</article></body></html>')
    return Response(document, media_type='text/html', headers={'Content-Disposition': f'attachment; filename="artigo-{job_id[:8]}.html"'})


@api.get('/jobs/{job_id}/preview')
def preview(job_id: str):
    job = get_job(job_id)
    if not job.get('article'):
        return Response('Artigo ainda não gerado.', media_type='text/html')
    delivery.ensure_exportable(job)
    return Response('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><link rel="stylesheet" href="/static/preview.css"></head><body><article><h1>'
                    + html.escape(job['article']['title']) + '</h1>' + generation.render_article(job) + '</article></body></html>', media_type='text/html')


@api.post('/jobs/{job_id}/wordpress')
def send_wordpress(job_id: str, body: ExportRequest | None = None):
    with pipeline.job_lock:
        job = get_job(job_id)
        if job['status'] in pipeline.ACTIVE or media.busy(job):
            raise HTTPException(409, 'Uma atualização do artigo ou das imagens está em andamento. Aguarde a gravação terminar para sincronizar a versão salva com o WordPress.')
        return wordpress.send_for_review(job)


@api.post('/jobs/{job_id}/images/references')
def search_image_references(job_id: str, body: ImageReferenceSearch):
    with pipeline.job_lock:
        inactive(get_job(job_id))
    result = image_references.search(body.query)
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        job['image_reference_results'] = result
        db.save_job(job)
    return result


@api.post('/jobs/{job_id}/images/generate', status_code=202)
def generate_image(job_id: str, body: ImageGeneration):
    with pipeline.job_lock:
        return image_generation.submit(get_job(job_id), body)


@api.get('/jobs/{job_id}/images/{image_id}/file')
def image_file(job_id: str, image_id: str):
    job = get_job(job_id)
    return FileResponse(media.path(job, media.find(job, image_id)), media_type='image/webp')


@api.put('/jobs/{job_id}/images/{image_id}')
def edit_image(job_id: str, image_id: str, body: ImageDetails):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        media.validate_position(job, body.position)
        item = media.find(job, image_id)
        if body.featured:
            for other in job.get('media', []):
                other['featured'] = False
        item.update(body.model_dump(), updated_at=db.now())
        db.save_job(job)
        return media.public_item(job, item)


@api.delete('/jobs/{job_id}/images/{image_id}')
def delete_image(job_id: str, image_id: str):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        item = media.find(job, image_id)
        job['media'].remove(item)
        db.save_job(job)
        media.path(job, item).unlink(missing_ok=True)
        return {'ok': True}


@app.get('/media-export/{job_id}/{image_id}/{token}/{filename}')
def import_image(job_id: str, image_id: str, token: str, filename: str):
    if not media.valid_token(job_id, image_id, token):
        raise HTTPException(404, 'Imagem indisponível ou link expirado.')
    job = db.get_job(job_id)
    try:
        item = media.find(job or {}, image_id)
    except ValueError:
        raise HTTPException(404, 'Imagem indisponível.') from None
    return FileResponse(media.path(job, item), media_type='image/webp',
                        headers={'Cache-Control': 'private, no-store', 'X-Robots-Tag': 'noindex, nofollow'})


@api.get('/editorial/profile')
def editorial_profile():
    return editorial_store.profile(persist=False)


@api.put('/editorial/profile')
def update_editorial_profile(body: ProfileUpdate):
    with pipeline.job_lock:
        if body.base_version != editorial_store.profile()['version']:
            raise HTTPException(409, 'O perfil mudou. Atualize a página antes de salvar.')
        db.set_setting('editorial_profile', body.profile.model_dump())
        return editorial_store.profile()


@api.get('/knowledge')
def seo_knowledge():
    return knowledge.status()


@api.get('/knowledge/search')
def search_knowledge(q: str = ''):
    if len(q) > 300:
        raise ValueError('Use até 300 caracteres na consulta.')
    return knowledge.retrieve('search', q)


@api.get('/jobs/{job_id}/team')
def editorial_team(job_id: str):
    from . import spending
    job = get_job(job_id)
    return editorial_store.report(job) | {'roster': agents.roster(), 'spending': spending.summary(job, persist=False)}


@api.post('/jobs/{job_id}/optimize')
def optimize_article(job_id: str):
    job = get_job(job_id)
    if not job.get('article') or job.get('article_needs_generation'):
        raise ValueError('Gere o artigo com a direção atual antes de otimizar.')
    if not get_secret('openai_api_key'):
        raise ValueError('Configure a chave OpenAI em Integrações.')
    pipeline.submit(job_id, 'optimize')
    return {'ok': True}


@api.post('/jobs/{job_id}/changes/{change_id}')
def decide_changes(job_id: str, change_id: str, body: ChangeDecision):
    with pipeline.job_lock:
        job = get_job(job_id)
        inactive(job)
        item = editorial_store.get_changes(job_id, change_id)
        if not item:
            raise HTTPException(404, 'Proposta não encontrada.')
        if body.article_hash != generation.article_hash(job['article']):
            raise HTTPException(409, 'O artigo mudou. Atualize a página antes de decidir.')
        result = changes.decide(job, item, body.action, body.article_hash)
        pipeline.step(job, job['status'], 'Decisão sobre a proposta editorial: ' + {'apply': 'aplicada', 'reject': 'rejeitada', 'undo': 'desfeita'}[body.action] + '.')
        return {'ok': True, 'status': result['status']}




@api.get('/strategy/roster')
def strategy_roster():
    return strategy_agents.roster()


@api.get('/strategy/cycles')
def list_strategy_cycles(project_id: str = 'default', limit: int = 20):
    return strategy_store.list_cycles(project_id=project_id, limit=limit)


@api.post('/strategy/cycles', status_code=201)
def create_strategy_cycle(body: StrategyRequest):
    cycle = strategy_engine.submit(project_id=body.project_id, focus=body.focus, budget=body.budget)
    return {'id': cycle['id'], 'status': cycle['status']}


@api.get('/strategy/cycles/{cycle_id}')
def get_strategy_cycle(cycle_id: str):
    report = strategy_store.cycle_report(cycle_id)
    if not report:
        raise HTTPException(404, 'Ciclo estratégico não encontrado.')
    return report


@api.post('/strategy/cycles/{cycle_id}/resume')
def resume_strategy_cycle(cycle_id: str):
    cycle = strategy_engine.resume(cycle_id)
    return {'ok': True, 'status': cycle['status']}


@api.get('/strategy/opportunities')
def list_strategy_opportunities(project_id: str = 'default', status: str | None = None, limit: int = 50):
    return strategy_store.list_opportunities(project_id=project_id, status=status, limit=limit)


@api.get('/strategy/opportunities/{opportunity_id}')
def get_strategy_opportunity(opportunity_id: str):
    opp = strategy_store.get_opportunity(opportunity_id)
    if not opp:
        raise HTTPException(404, 'Oportunidade não encontrada.')
    return opp


@api.post('/strategy/opportunities/{opportunity_id}/decision')
def decide_strategy_opportunity(opportunity_id: str, body: OpportunityDecision):
    try:
        opp = strategy_store.decide_opportunity(opportunity_id, body.action, body.reason)
    except strategy_store.OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except strategy_store.OpportunityConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {'ok': True, 'status': opp['status']}


@api.post('/strategy/opportunities/{opportunity_id}/produce')
def produce_opportunity(opportunity_id: str, body: OpportunityProduce | None = None):
    with pipeline.job_lock:
        try:
            result = strategy_store.create_production(
                opportunity_id, lambda opp: strategy_production.build_job(opp, body.urls if body else None),
                active_states=pipeline.ACTIVE)
        except strategy_store.OpportunityNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except strategy_store.ProductionQueueFull as exc:
            raise HTTPException(429, str(exc)) from None
        except strategy_store.OpportunityConflict as exc:
            raise HTTPException(409, str(exc)) from None
        if result['created']:
            pipeline.submit(result['job_id'], 'generate')
    return result

app.include_router(cost_api)
app.include_router(api)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')
