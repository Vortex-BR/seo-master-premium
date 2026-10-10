"""Optional article-wide critical reading, with durable costs and localized edits.

Default observations are local only. Opt-in work uses the article's existing
ledger, a frozen request, exclusive claims and narrow writes; provider calls
never hold the SQLite writer lock or save an obsolete article snapshot.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
import time

from .. import cost_observability, db, generation, spending
from . import changes, delivery, intelligence as core, intelligence_research, store
from .intelligence_contracts import IECDiagnosis, IECProposalBatch, IECRequest, IECValidation


VERSION = 1
logger = logging.getLogger(__name__)


def mode():
    value = os.getenv('EDITORIAL_INTELLIGENCE_MODE', 'shadow').strip().lower()
    return value if value in ('off', 'shadow') else 'off'


def observe(job, *, phase='pipeline_finished'):
    try:
        if mode() == 'off' or not job.get('article'):
            return None
        with cost_observability.external_attempt(job, 'iec_shadow', provider='application',
                origin='local_processing', metadata={'phase': phase, 'incremental_ai_requests': 0,
                    'incremental_input_tokens': 0, 'incremental_output_tokens': 0,
                    'cost_basis': 'no_provider_operation_in_shadow', 'export_blocking': False}) as event:
            event.update(calculated_usd=0.0, infrastructure_usd=None)
            report = core.shadow(job)
            report.update(article_hash=generation.article_hash(job['article']),
                          context_hash=core.context_hash(job), schema_version='iec.v1',
                          costs={'calculated_usd': 0.0, 'attempts': 0, 'invoice_usd': None,
                                 'infrastructure_usd': None})
            artifact = store.artifact(job, 'editorial_intelligence', 'shadow', report,
                {'article': report['article_hash'], 'context': report['context_hash'], 'iec_version': VERSION})
            event['state'] = 'completed'
            return artifact
    except Exception as exc:
        logger.warning('Editorial intelligence diagnostic unavailable: %s', type(exc).__name__)
        return None


def _row(ident):
    with db.connect() as connection:
        row = connection.execute('SELECT status,data FROM agent_runs WHERE id=?', (ident,)).fetchone()
    return {'status': row['status'], **json.loads(row['data'])} if row else None


def _save(job, ident, fingerprint, data, status, *, role='editorial_intelligence'):
    store.save_run(job, role, fingerprint, data, ident, status)


def _claim(job, ident, fingerprint, frozen_model):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT status,data FROM agent_runs WHERE id=?', (ident,)).fetchone()
        saved = json.loads(row['data']) if row else {}
        if row and row['status'] in ('completed', 'running'):
            return row['status'], saved
        # One optional enrichment per article at a time. Other article edits
        # remain available; this claim prevents overlapping paid operations.
        active = connection.execute("SELECT id FROM agent_runs WHERE job_id=? AND role='editorial_intelligence' AND status='running' LIMIT 1",
                                    (job['id'],)).fetchone()
        if active:
            return 'running', {'run_id': active['id']}
        data = {**saved, 'run_id': ident, 'name': 'Inteligência editorial', 'model': frozen_model,
                'input_hash': fingerprint, 'started_at': db.now(), 'calls': saved.get('calls', 0),
                'iec_version': VERSION, 'article_hash': generation.article_hash(job['article'])}
        connection.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data',
            (ident, job['id'], job['editorial']['cycle_id'], 'editorial_intelligence',
             fingerprint, 'running', db.now(), json.dumps(data, ensure_ascii=False, allow_nan=False)))
        return 'claimed', data


def _merge_usage(job, rows):
    if not rows:
        return
    with db.job_transaction(job['id']) as current:
        if current is None:
            return
        saved = current.setdefault('usage', [])
        identities = {(row.get('reservation_id'), row.get('response_id')) for row in saved}
        for row in rows:
            ident = row.get('reservation_id'), row.get('response_id')
            if ident not in identities:
                saved.append(deepcopy(row))
                identities.add(ident)
        db.save_job(current)


def _scope(snapshot, stage, request, profile, frozen_model):
    return {'role': stage, 'model': frozen_model, 'profile': profile,
            # IEC payloads already contain every original video. Resending the
            # same transcript through legacy context would waste input tokens.
            'context_sources': {},
            'max_output_tokens': request.max_output_tokens}


def _structured(snapshot, schema, instruction, stage, payload, request, master, profile, frozen_model):
    scope = _scope(snapshot, stage, request, profile, frozen_model)
    token = generation.agent_scope.set(scope)
    try:
        prepared = generation.prepare_structured(snapshot, schema, instruction, stage,
                                                  {'_local_context': True, **payload})
        fingerprint = generation.article_hash({'iec_version': VERSION, 'request': prepared[0]})
        ident = 'iec-call-' + generation.article_hash({'job': snapshot['id'], 'request': fingerprint})[:32]
        cached = _row(ident)
        if cached and cached['status'] == 'completed':
            cost_observability.record_cache(snapshot, stage, dependency_fingerprint=fingerprint,
                                           metadata={'agent_run_id': ident})
            return schema.model_validate(cached['output']).model_dump()
        with db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT status FROM agent_runs WHERE id=?', (ident,)).fetchone()
            if existing and existing['status'] == 'running':
                raise ValueError('Esta chamada já está em execução; nenhuma chamada duplicada foi enviada.')
            call = {'run_id': ident, 'name': 'Inteligência editorial: ' + stage,
                    'model': frozen_model, 'input_hash': fingerprint, 'started_at': db.now()}
            connection.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data',
                (ident, snapshot['id'], snapshot['editorial']['cycle_id'], 'iec_call',
                 fingerprint, 'running', db.now(), json.dumps(call, ensure_ascii=False)))
        if master['calls'] >= request.max_calls:
            _save(snapshot, ident, fingerprint, call, 'not_sent', role='iec_call')
            raise spending.SpendLimitExceeded('O limite de chamadas desta análise foi atingido; o artigo foi preservado.')
        master['calls'] += 1
        _save(snapshot, master['run_id'], master['input_hash'], master, 'running')
        usage_start = len(snapshot.get('usage', []))
        try:
            output = generation.structured(snapshot, schema, instruction, stage,
                {'_local_context': True, **payload}, prepared=prepared)
            output = schema.model_validate(output).model_dump()
            call.update(output=output, finished_at=db.now())
            _save(snapshot, ident, fingerprint, call, 'completed', role='iec_call')
            return output
        except BaseException as exc:
            call.update(error_type=type(exc).__name__, finished_at=db.now())
            _save(snapshot, ident, fingerprint, call, 'failed', role='iec_call')
            raise
        finally:
            _merge_usage(snapshot, snapshot.get('usage', [])[usage_start:])
    finally:
        generation.agent_scope.reset(token)


def _video_foundations(snapshot):
    return core.video_foundations(snapshot)


def _search(snapshot, questions, request, master, profile, frozen_model, epoch):
    identity = generation.article_hash({'questions': questions, 'domains': request.trusted_domains,
        'epoch': epoch, 'model': frozen_model, 'version': VERSION, 'tokens': request.max_output_tokens})
    ident = 'iec-search-' + generation.article_hash({'job': snapshot['id'], 'identity': identity})[:32]
    saved = _row(ident)
    if saved and saved['status'] == 'completed':
        cost_observability.record_cache(snapshot, 'iec_search', dependency_fingerprint=identity)
        return saved['output']
    if master['calls'] >= request.max_calls:
        raise spending.SpendLimitExceeded('O orçamento de chamadas não comporta pesquisa; o artigo foi preservado.')
    master['calls'] += 1
    _save(snapshot, master['run_id'], master['input_hash'], master, 'running')
    token = generation.agent_scope.set(_scope(snapshot, 'iec_search', request, profile, frozen_model))
    start = len(snapshot.get('usage', []))
    try:
        urls = intelligence_research.discover(snapshot, questions, trusted_domains=request.trusted_domains,
                    max_output_tokens=request.max_output_tokens, max_tool_calls=1)
        _save(snapshot, ident, identity, {'run_id': ident, 'name': 'Pesquisa complementar',
              'input_hash': identity, 'output': urls, 'model': frozen_model}, 'completed', role='iec_call')
        return urls
    finally:
        _merge_usage(snapshot, snapshot.get('usage', [])[start:])
        generation.agent_scope.reset(token)


def _proof_artifact(job, version):
    proof = next((row for row in store.artifacts(job['id'], 'iec_proof') if row['version'] == version), None)
    if proof is None or generation.article_hash({key: proof[key] for key in ('kind', 'scope', 'data', 'dependencies')}) != version:
        raise changes.EditConflict('A prova desta proposta não está disponível ou foi alterada.')
    return proof


def validate_change(job, item):
    proof = _proof_artifact(job, item.get('iec_proof_version'))['data']
    if proof.get('iec_version') != VERSION or proof['changes'] != item['changes']:
        raise changes.EditConflict('A proposta não corresponde à sua prova versionada.')
    if proof['context_hash'] != core.context_hash(job):
        raise changes.EditConflict('A fonte ou a direção mudou; gere uma nova análise.')
    if generation.article_hash(changes.preview(job['article'], item['changes'])) != item['result_hash']:
        raise changes.EditConflict('O resultado da proposta mudou; o artigo foi preservado.')
    if (generation.article_hash(item.get('after_article')) != item['result_hash']
            or generation.article_hash(item.get('before_article')) != item['base_hash']):
        raise changes.EditConflict('O histórico da proposta não corresponde às versões verificadas.')
    prepared = core.prepare_proposals(job, proof['diagnosis'], proof['batch'], proof['foundations'])
    candidates = {candidate['opportunity_id']: candidate for candidate in prepared['accepted_candidates']}
    if any(ident not in candidates for ident in proof['accepted_ids']):
        raise changes.EditConflict('Uma fonte não sustenta mais esta proposta.')
    current_changes = [candidates[ident]['change'] for ident in proof['accepted_ids']]
    if current_changes != item['changes']:
        raise changes.EditConflict('A fonte ou o complemento não corresponde à prova original.')
    approved = {row['opportunity_id'] for row in IECValidation.model_validate(proof['validation']).model_dump()['assessments']
                if row['status'] == 'accept'}
    if not set(proof['accepted_ids']).issubset(approved):
        raise changes.EditConflict('A revisão semântica não aprovou este complemento.')
    selected = {support['reference_id'] for candidate in proof['candidates'] for support in candidate['supports']}
    for key, foundation in proof['foundations'].items():
        if key not in selected:
            continue
        if foundation.get('origin') == 'external_verified':
            expires = datetime.fromisoformat(foundation['expires_at'])
            if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                raise changes.EditConflict('A fonte complementar venceu; faça uma nova análise.')


def _proposal(snapshot, master, diagnosis, batch, foundations, prepared, validation):
    approved = {row['opportunity_id'] for row in validation['assessments'] if row['status'] == 'accept'}
    expected = {candidate['opportunity_id'] for candidate in prepared['accepted_candidates']}
    received = [row['opportunity_id'] for row in validation['assessments']]
    if set(received) != expected or len(received) != len(set(received)):
        raise ValueError('A revisão não cobriu exatamente as propostas recebidas.')
    candidates = [candidate for candidate in prepared['accepted_candidates'] if candidate['opportunity_id'] in approved]
    if not candidates:
        return None, []
    edits = [candidate['change'] for candidate in candidates]
    after = changes.preview(snapshot['article'], edits)
    proof = {'iec_version': VERSION, 'context_hash': core.context_hash(snapshot),
             'execution_id': master['run_id'], 'diagnosis': diagnosis, 'batch': batch,
             'foundations': foundations, 'validation': validation, 'changes': edits,
             'accepted_ids': [candidate['opportunity_id'] for candidate in candidates],
             'candidates': candidates}
    artifact = store.artifact(snapshot, 'iec_proof', master['run_id'], proof,
        {'article': generation.article_hash(snapshot['article']), 'context': proof['context_hash']})
    item = {'id': generation.article_hash({'iec': master['run_id'], 'proof': artifact['version']})[:32],
            'role': 'editorial_intelligence', 'summary': batch['summary'], 'changes': edits,
            'base_hash': generation.article_hash(snapshot['article']), 'before_article': snapshot['article'],
            'after_article': after, 'result_hash': generation.article_hash(after),
            'context_kind': 'iec.v1', 'context_hash': proof['context_hash'], 'status': 'pending',
            'automatic_eligible': False, 'iec_proof_version': artifact['version'],
            'iec_execution_id': master['run_id'], 'iec_accepted_count': len(candidates),
            'iec_candidates': candidates}
    with db.job_transaction(snapshot['id']) as current:
        if (current is None or generation.article_hash(current.get('article')) != item['base_hash']
                or core.context_hash(current) != item['context_hash']):
            raise changes.EditConflict('O artigo ou a fonte mudou durante a análise; a edição nova foi preservada.')
        from ..pipeline import ACTIVE
        if current.get('status') in ACTIVE:
            raise changes.EditConflict('A geração do artigo começou durante a análise; o artigo foi preservado.')
        store.save_changes(snapshot, item)
    return item, candidates


def _public_changes(job_id, ident):
    with db.connect() as connection:
        rows = connection.execute('SELECT data FROM change_sets WHERE job_id=?', (job_id,)).fetchall()
    result = []
    for row in rows:
        item = json.loads(row['data'])
        if item.get('iec_execution_id') == ident:
            result.append({key: value for key, value in item.items() if key not in ('before_article', 'after_article')})
    return result


def _expired_proofs(snapshot, ident):
    """A completed paid cache cannot extend the validity of its originals."""
    expired = []
    for item in _public_changes(snapshot['id'], ident):
        if item['status'] != 'pending':
            continue
        proof = _proof_artifact(snapshot, item['iec_proof_version'])['data']
        selected = {support['reference_id'] for candidate in proof['candidates']
                    for support in candidate['supports']}
        for key in selected:
            source = proof['foundations'][key]
            if source['origin'] != 'external_verified':
                continue
            try:
                expires = datetime.fromisoformat(source['expires_at'])
                fresh = expires.tzinfo is not None and expires > datetime.now(timezone.utc)
            except (KeyError, TypeError, ValueError):
                fresh = False
            if not fresh:
                expired.append(item['iec_proof_version'])
                break
    return sorted(set(expired))


def _costs(job_id, ident, budget, accepted, applied):
    if budget is None:
        return {'calculated_usd': None, 'limit_usd': None, 'remaining_usd': None,
                'invoice_usd': None, 'infrastructure_usd': None,
                'cost_per_accepted_improvement_usd': None,
                'cost_per_applied_improvement_usd': None,
                'accounting_notice': 'O orçamento desta execução antiga não foi registrado.'}
    values = spending.incremental_summary(job_id, ident, budget)
    values['cost_per_accepted_improvement_usd'] = (values['calculated_usd'] / accepted
        if accepted and values['calculated_usd'] is not None else None)
    values['cost_per_applied_improvement_usd'] = (values['calculated_usd'] / applied
        if applied and values['calculated_usd'] is not None else None)
    values['accepted_improvements'] = accepted
    values['applied_improvements'] = applied
    values['infrastructure_usd'] = None
    return values


def _reply(job_id, master, request):
    edits = _public_changes(job_id, master['run_id'])
    if request.mode == 'apply':
        for edit in edits:
            if edit['status'] == 'pending':
                current = db.get_job(job_id)
                item = store.get_changes(job_id, edit['id'])
                try:
                    changes.decide(current, item, 'apply', request.article_hash, automatic=True)
                except changes.EditConflict:
                    raise
        edits = _public_changes(job_id, master['run_id'])
    report = deepcopy(master.get('report', {}))
    applied = sum(edit.get('iec_accepted_count', 0) for edit in edits if edit['status'] == 'applied')
    accepted = report.get('accepted_count', 0)
    values = _costs(job_id, master.get('budget_namespace', master['run_id']),
                    request.budget_usd, accepted, applied)
    values['cost_per_semantically_accepted_improvement_usd'] = (
        values['calculated_usd'] / accepted if accepted and values['calculated_usd'] is not None else None)
    report.update(applied_count=applied, costs=values)
    if applied:
        report['status'] = 'applied'
    return {'execution_id': master['run_id'], 'status': report.get('status', 'running'),
            'report': report, 'changes': edits}


def execute(job_id, request):
    request = IECRequest.model_validate(request)
    if mode() == 'off':
        raise ValueError('A análise complementar está desativada neste ambiente.')
    saved = db.get_job(job_id)
    if saved is None:
        raise ValueError('Artigo não encontrado.')
    delivery.ensure_exportable(saved)
    if request.article_hash != generation.article_hash(saved['article']):
        raise changes.EditConflict('O artigo mudou. Atualize a página antes de analisar.')
    if request.mode == 'shadow':
        artifact = observe(saved, phase='explicit_request')
        return {'execution_id': artifact['version'] if artifact else None,
                'status': 'shadow' if artifact else 'unavailable',
                'report': artifact['data'] if artifact else {'notice': 'A análise não ficou disponível; o artigo foi preservado.'},
                'changes': []}
    from ..pipeline import ACTIVE
    if saved.get('status') in ACTIVE:
        raise changes.EditConflict('A geração do artigo está em execução; espere sua conclusão.')
    if saved.get('article_needs_generation') or saved.get('draft_delivery', {}).get('complete') is False:
        raise ValueError('Use um artigo completo com a direção atual antes de gerar complementos.')
    profile, frozen_model = store.profile(persist=False), generation.model()
    snapshot = deepcopy(saved)
    # record_usage can now only append to this private copy. Its narrow merge
    # later reloads the database, preserving any concurrent article/context edit.
    snapshot.pop('created_at', None)
    snapshot.pop('status', None)
    epoch = int(time.time() // (request.freshness_hours * 3600)) if request.allow_external else None
    options = request.model_dump(mode='json', exclude={'mode', 'budget_usd', 'max_calls', 'article_hash'})
    token = generation.agent_scope.set(_scope(snapshot, 'iec_detect', request, profile, frozen_model))
    try:
        prepared = generation.prepare_structured(snapshot, IECDiagnosis, core.DETECT, 'iec_detect', core.diagnostic_payload(snapshot))
    finally:
        generation.agent_scope.reset(token)
    fingerprint = generation.article_hash({'request': prepared[0], 'options': options, 'iec_version': VERSION,
        'context': core.context_hash(snapshot), 'freshness_epoch': epoch,
        'policies': [core.IEC_POLICY, core.DETECT, core.COMPOSE, core.VALIDATE]})
    budget_namespace = 'iec-' + generation.article_hash({'job': job_id, 'input': fingerprint})[:32]
    # A page cached by another analysis may expire within this freshness epoch.
    # Follow immutable proof versions to reuse a refreshed run, or create one
    # explicit refresh. Detection can still hit its exact paid request cache.
    for _ in range(100):
        ident = 'iec-' + generation.article_hash({'job': job_id, 'input': fingerprint})[:32]
        cached_master = _row(ident)
        if not cached_master or cached_master['status'] != 'completed':
            break
        expired = _expired_proofs(snapshot, ident)
        if not expired:
            break
        fingerprint = generation.article_hash({'previous_request': fingerprint,
                                               'expired_proof_versions': expired})
    else:
        raise changes.EditConflict('O histórico de fontes exige uma nova versão de análise; o artigo foi preservado.')
    snapshot['editorial'] = {**snapshot.get('editorial', {}), 'cycle_id': ident}
    disposition, master = _claim(snapshot, ident, fingerprint, frozen_model)
    if disposition == 'running':
        return {'execution_id': master['run_id'], 'status': 'running', 'report': {'notice': 'Já existe uma análise em execução para este artigo.'}, 'changes': []}
    if disposition == 'completed':
        return _reply(job_id, master, request)
    master.update(input_hash=fingerprint, budget_usd=str(request.budget_usd), budget_namespace=budget_namespace)
    report = {'status': 'no_change', 'summary': '', 'opportunities': [], 'proposed_count': 0,
              'accepted_count': 0, 'applied_count': 0, 'rejections': [], 'limitations': [],
              'article_hash': request.article_hash, 'context_hash': core.context_hash(snapshot),
              'schema_version': 'iec.v1', 'human_quality_evaluation': 'not_performed',
              'notice': 'Propostas verificadas contra fontes; a avaliação semântica não certifica verdade independente.'}
    with cost_observability.run(snapshot, operation='editorial_enrichment', pipeline_version=VERSION,
            dependency_fingerprint=fingerprint, metadata={'iec_execution_id': ident,
                'base_article_hash': request.article_hash, 'incremental_budget_usd': float(request.budget_usd),
                'incremental_budget_namespace': budget_namespace}) as run:
        try:
            with spending.incremental_budget(job_id, budget_namespace, request.budget_usd):
                diagnosis = _structured(snapshot, IECDiagnosis, core.DETECT, 'iec_detect',
                    core.diagnostic_payload(snapshot), request, master, profile, frozen_model)
                report.update(summary=diagnosis['summary'], opportunities=diagnosis['opportunities'])
                if diagnosis['status'] != 'no_change':
                    foundations = _video_foundations(snapshot)
                    block_ids = {block['id'] for block in core.article_blocks(snapshot['article'])}
                    useful = []
                    for opportunity in diagnosis['opportunities']:
                        if opportunity['already_explained']:
                            continue
                        if (opportunity['block_id'] not in block_ids
                                or set(opportunity['evidence_ids']) - foundations.keys()):
                            report['rejections'].append({'opportunity_id': opportunity['id'],
                                'code': 'invalid_diagnosis_reference',
                                'reason': 'O diagnóstico não corresponde aos blocos ou às fontes originais disponíveis.'})
                            continue
                        useful.append(opportunity)
                    diagnosis = {**diagnosis, 'opportunities': useful,
                                 'status': 'opportunities' if useful else 'no_change'}
                    queries = list(dict.fromkeys(opportunity['external_query'] for opportunity in useful
                                                 if opportunity['external_query']))
                    if useful and queries and request.allow_external:
                        if not request.external_urls and master['calls'] + 3 > request.max_calls:
                            raise spending.SpendLimitExceeded('O limite deve comportar pesquisa, composição e validação; nenhuma pesquisa foi enviada.')
                        urls = list(request.external_urls) or _search(snapshot, queries, request, master, profile, frozen_model, epoch)
                        foundations.update(intelligence_research.read_pages(snapshot, urls,
                            trusted_domains=request.trusted_domains, freshness_hours=request.freshness_hours, execution_id=ident))
                    elif queries:
                        report['limitations'].append('Pesquisa externa não autorizada; explicações sem fundamento serão descartadas.')
                    if useful:
                        store.artifact(snapshot, 'iec_foundations', ident, foundations,
                                       {'article': request.article_hash, 'context': report['context_hash']})
                        payload = {**core.diagnostic_payload(snapshot), 'diagnosis': diagnosis, 'foundations': foundations}
                        batch = _structured(snapshot, IECProposalBatch, core.COMPOSE, 'iec_compose', payload,
                                            request, master, profile, frozen_model)
                        candidates = core.prepare_proposals(snapshot, diagnosis, batch, foundations)
                        report['rejections'].extend(candidates['rejections'])
                        report['proposed_count'] = len(batch['proposals'])
                        if candidates['accepted_candidates']:
                            validation = _structured(snapshot, IECValidation, core.VALIDATE, 'iec_validate',
                                {**payload, 'proposals': candidates['accepted_candidates'],
                                 'after_article': changes.preview(snapshot['article'], candidates['changes'])},
                                request, master, profile, frozen_model)
                            report['rejections'].extend({'opportunity_id': row['opportunity_id'],
                                'code': row['status'], 'reason': row['reason']} for row in validation['assessments']
                                if row['status'] != 'accept')
                            item, accepted = _proposal(snapshot, master, diagnosis, batch, foundations, candidates, validation)
                            report['accepted_count'] = len(accepted)
                            if item:
                                report['status'] = 'suggested'
            master.update(report=report, finished_at=db.now())
            _save(snapshot, ident, fingerprint, master, 'completed')
            result = _reply(job_id, master, request)
            run.update(outcome=result['status'], metadata={**run['metadata'],
                'accepted_count': report['accepted_count'], 'applied_count': result['report']['applied_count']})
            store.artifact(snapshot, 'editorial_intelligence', ident, result['report'],
                           {'article': request.article_hash, 'context': report['context_hash'], 'iec_version': VERSION})
            return result
        except Exception as exc:
            status = 'conflict' if isinstance(exc, changes.EditConflict) else (
                'budget' if isinstance(exc, spending.SpendLimitExceeded) else 'unavailable')
            report.update(status=status, notice='A análise não foi concluída; a versão atual do artigo foi preservada.',
                          error_type=type(exc).__name__)
            master.update(report=report, finished_at=db.now())
            _save(snapshot, ident, fingerprint, master, 'failed')
            run['outcome'] = 'failed'
            logger.warning('Editorial intelligence unavailable: %s', type(exc).__name__)
            return _reply(job_id, master, request.model_copy(update={'mode': 'suggest'}))
        except BaseException as exc:
            master.update(report={**report, 'status': 'cancelled', 'error_type': type(exc).__name__}, finished_at=db.now())
            _save(snapshot, ident, fingerprint, master, 'interrupted')
            raise


def report(job_id):
    saved = db.get_job(job_id)
    if saved is None:
        raise ValueError('Artigo não encontrado.')
    with db.connect() as connection:
        rows = connection.execute('SELECT status,data FROM agent_runs WHERE job_id=? '
            "AND role='editorial_intelligence' ORDER BY created_at DESC LIMIT 20", (job_id,)).fetchall()
    runs = [{'status': row['status'], **json.loads(row['data'])} for row in rows]
    reporting_run = next((row for row in runs if row.get('report')), None)
    last = reporting_run['report'] if reporting_run else None
    if last is None:
        artifacts = store.artifacts(job_id, 'editorial_intelligence')
        last = artifacts[0]['data'] if artifacts else None
    if reporting_run is not None:
        ident = reporting_run['run_id']
        edits = _public_changes(job_id, ident)
        applied = sum(edit.get('iec_accepted_count', 0) for edit in edits if edit['status'] == 'applied')
        last = {**last, 'applied_count': applied,
                'costs': _costs(job_id, reporting_run.get('budget_namespace', ident), reporting_run.get('budget_usd'),
                                last.get('accepted_count', 0), applied)}
    return {'mode': mode(), 'status': runs[0]['status'] if runs else 'idle', 'last_result': last, 'runs': runs}


def recover():
    with db.connect() as connection:
        rows = connection.execute("SELECT id,data FROM agent_runs WHERE role IN ('editorial_intelligence','iec_call') AND status='running'").fetchall()
        for row in rows:
            data = json.loads(row['data'])
            data.update(interrupted_at=db.now(), recovery_notice='Execução interrompida; reservas incertas permanecem no ledger.')
            connection.execute('UPDATE agent_runs SET status=?,data=? WHERE id=?',
                               ('interrupted', json.dumps(data, ensure_ascii=False), row['id']))
