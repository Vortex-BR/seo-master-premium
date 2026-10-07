from copy import deepcopy
import re
from openai import APIConnectionError

from .. import db, generation
from ..schemas import Dossier
from ..seo import checks, knowledge
from . import agents, changes, store


def inputs_hash(job):
    return generation.article_hash({'brief': job['brief'], 'sources': job['sources']})


def article_passages(article, blocks=False):
    """Constrain reviewer quotations to actual article spans, not free-form paraphrases."""
    result = ['']
    for key, value in article.items():
        if not isinstance(value, str):
            continue
        if key != 'markdown':
            result.extend(value.splitlines() or [''])
            continue
        for line in (re.split(r'\n\s*\n', value) if blocks else value.splitlines()):
            line = line.strip()
            if not line:
                continue
            # Sentence spans are shorter than paragraphs and economical in the response schema.
            result.extend([line] if blocks else (s.strip() for s in re.split(r'(?<=[.!?])\s+', line) if s.strip()))
    # Pinned bounded list; every candidate remains a literal span of its original field.
    selected, size = [], 0
    for value in dict.fromkeys(result):
        if len(value) > 8000 or size + len(value) > 35000 or len(selected) >= 180:
            continue
        selected.append(value)
        size += len(value)
    return selected


def start(job, mode):
    previous = job.get('editorial') or {}
    if mode == 'write' and previous and previous.get('mode') in ('generate', 'plan') and not previous.get('initial_complete'):
        from . import workflow
        current = store.profile()
        plan = job.get('plan') or {}
        if (workflow.enabled() and plan.get('valid') and plan.get('input_version') == store.inputs_version(job)
                and previous.get('input_hash') == inputs_hash(job) and previous.get('agents_version') == agents.VERSION
                and previous.get('knowledge_version') == knowledge.package()['version']
                and previous.get('model') == generation.model() and store.voice(current) == store.voice(previous['profile'])
                and workflow.compatible(job)):
            # Planning and writing belong to the same unfinished budget, even
            # when the plan received a recorded editorial correction.
            previous.update(mode='write', stale=False, current_role=None)
            previous['profile']['profile']['max_calls'] = max(current['profile']['max_calls'], previous['profile']['profile']['max_calls'])
            previous['profile']['profile']['context_chars'] = max(current['profile']['context_chars'], previous['profile']['profile']['context_chars'])
            previous.pop('stale_reason', None)
            previous.pop('finished_at', None)
            db.save_job(job)
            return 'write'
    if mode == 'resume' and previous and not previous.get('stale') and previous.get('input_hash') == inputs_hash(job):
        if previous.get('agents_version') == agents.VERSION:
            # Budget increases after an interruption do not alter the frozen editorial voice.
            current = store.profile()['profile']
            previous['profile']['profile']['max_calls'] = max(current['max_calls'], previous['profile']['profile']['max_calls'])
            previous['profile']['profile']['context_chars'] = max(current['context_chars'], previous['profile']['profile']['context_chars'])
            previous.pop('budget_pending', None)
            return previous['mode']
    actual_mode = mode if mode in ('review', 'optimize', 'plan', 'write') else 'generate'
    job['editorial'] = {'cycle_id': store.new_id(), 'mode': actual_mode, 'started_at': db.now(),
                        'profile': store.profile(), 'knowledge_version': knowledge.package()['version'],
                        'agents_version': agents.VERSION, 'model': generation.model(),
                        'input_hash': inputs_hash(job), 'completed': {}, 'calls': 0,
                        'current_role': None, 'round': 0, 'stale': False}
    job['review'] = None
    db.save_job(job)
    return actual_mode


def validate_output(job, role, output, docs, payload):
    source_ids = set(generation.evidence_map(job))
    rule_ids = {r['id'] for r in docs['rules']}
    article = payload.get('article')
    def passage_text(value):
        return generation.normalize(re.sub(r'\[\[[\w-]+\]\]', '', value))
    article_text = passage_text('\n'.join(v for v in (article or {}).values() if isinstance(v, str)))
    for item in output.get('findings', []) + output.get('changes', []):
        if set(item.get('source_ids', [])) - source_ids or set(item.get('rule_ids', [])) - rule_ids:
            raise ValueError(f'{agents.ROLES[role]["name"]} citou uma fonte ou regra ausente. Retome para executar esta etapa novamente.')
        passage = item.get('passage', '')
        if article and passage and passage_text(passage) not in article_text:
            raise ValueError(f'{agents.ROLES[role]["name"]} citou um trecho que não está nesta versão do artigo.')
    if output.get('changes') and article:
        candidate = changes.preview(article, output['changes'])
        changes.validate_numbers(job, candidate)


def invoke(job, role, payload=None, callback=None, slot=None):
    from ..pipeline import safe_error, step
    payload = payload or {}
    slot = slot or role
    state = job['editorial']
    spec = agents.ROLES[role]
    docs = knowledge.retrieve(spec['sector'], job['brief'].get('keyword', ''), state['knowledge_version'])
    scope = {'role': role, 'name': spec['name'], 'sector': spec['sector'], 'profile': state['profile'],
             'knowledge': docs, 'model': state['model'],
             'research_requests': payload.get('findings', payload.get('checks', {}).get('findings', [])),
             'correction_of_invalid_delivery': state.get('invalid_deliveries', {}).get(slot),
             'max_output_tokens': 8000 if role in ('extractor', 'planner', 'writer', 'fact_reviewer') else 4500}
    if '_context_sources' in payload:
        scope['context_sources'] = payload['_context_sources']
    elif job.get('apuration', {}).get('valid'):
        from .workflow import context_sources
        scope['context_sources'] = context_sources(job, payload)
    recovery = state.get('response_recoveries', {}).get(slot)
    if recovery:
        scope['response_recovery'] = recovery
        scope['max_output_tokens'] = recovery['max_output_tokens']
    if payload.get('article'):
        scope['article_passages'] = article_passages(payload['article'])
        scope['article_title'] = payload['article']['title']
        if role in ('voice_editor', 'seo_editor'):
            blocks = {}
            for field in ('title', 'seo_title', 'slug', 'meta_description', 'excerpt', 'markdown'):
                value = payload['article'][field]
                texts = re.split(r'\n\s*\n', value) if field == 'markdown' else [value]
                for text in dict.fromkeys(texts):
                    if len(text) <= 12000 and len(blocks) < 160:
                        blocks[f'b{len(blocks)+1}'] = {'field': field, 'text': text}
            scope['edit_blocks'] = blocks
    if role == 'fact_reviewer' and callback is generation.review_article:
        mapping = generation.evidence_map(job)
        cited = set(re.findall(r'\[\[([\w-]+)\]\]', job['article']['markdown']))
        relevant = [key for key in mapping if key in cited] or list(mapping)
        options = {}
        for key in relevant:
            excerpts = []
            source = mapping[key]
            for claim in job.get('dossier', {}).get('claims', []):
                for evidence in claim.get('evidence', []):
                    text = evidence.get('excerpt', '')
                    if evidence.get('source_id') == key and text.strip() and '\n' not in text and generation.normalize(text) in generation.normalize(source['text']):
                        excerpts.append(text)
            for sentence in re.split(r'(?<=[.!?])\s+', source['text']):
                words = sentence.split()
                excerpts.extend(' '.join(words[i:i+12]) for i in range(0, len(words), 12) if len(words[i:i+12]) >= 3)
            options[key] = list(dict.fromkeys(excerpts))[:max(3, 240 // max(1, len(relevant)))]
        scope['source_excerpts_by_id'] = {key: values for key, values in options.items() if values}
    fingerprint_scope = {**scope, 'profile': store.voice(scope['profile'])}
    fingerprint = generation.article_hash({'scope': fingerprint_scope, 'payload': payload, 'input': state['input_hash'],
                                            'agent_version': agents.VERSION,
                                            'plan_version': (job.get('plan') or {}).get('version'),
                                            'knowledge_version': (job.get('apuration') or {}).get('version'),
                                            'sources': scope.get('context_sources', generation.evidence_map(job))})
    existing_id = state['completed'].get(slot)
    existing = store.get_run(existing_id) if existing_id else None
    if existing and existing.get('input_hash') == fingerprint:
        return deepcopy(existing['output']), existing_id
    cached = store.cached(job, role, fingerprint)
    if cached:
        state['completed'][slot] = cached['run_id']
        db.save_job(job)
        return deepcopy(cached['output']), cached['run_id']
    if state['calls'] >= state['profile']['profile']['max_calls']:
        from .workflow import BudgetExceeded
        raise BudgetExceeded('O ciclo atingiu o número de chamadas configurado para a equipe. O trabalho foi salvo. Revise os resultados ou ajuste o orçamento no Perfil editorial e retome o ciclo.')
    preflight = generation.agent_scope.set(scope)
    try:
        generation.context(job, payload)
    finally:
        generation.agent_scope.reset(preflight)
    state['calls'] += 1
    state['current_role'] = role
    status = {'apuration': 'analyzing', 'writing': 'writing', 'seo': 'optimizing', 'quality': 'reviewing'}[spec['sector']]
    step(job, status, spec['name'] + ': trabalhando na ' + ('nova rodada de correção.' if state['round'] else 'entrega editorial.'))
    run_id = store.new_id()
    run = {'run_id': run_id, 'name': spec['name'], 'sector': spec['sector'], 'slot': slot,
           'model': state['model'], 'profile_version': state['profile']['version'], 'knowledge_version': docs['version'],
           'rule_ids': [r['id'] for r in docs['rules']], 'input_hash': fingerprint,
           'article_hash': generation.article_hash(job['article']) if job.get('article') else None,
           'started_at': db.now()}
    store.save_run(job, role, fingerprint, run, run_id)
    token = generation.agent_scope.set(scope)
    usage_start = len(job.get('usage', []))
    try:
        output = callback(job) if callback else generation.structured(job, spec['schema'], spec['prompt'], role, payload)
        if not callback:
            output = spec['schema'].model_validate(output).model_dump()
            try:
                validate_output(job, role, output, docs, payload)
            except changes.EditConflict as exc:
                attempts = state.get('invalid_deliveries', {}).get(slot, {}).get('attempts', 0)
                if attempts == 0 and state['calls'] < state['profile']['profile']['max_calls']:
                    raise
                # A rejected optional edit does not invalidate the preserved article.
                # propose() records it as invalid; the quality team reviews the unchanged text.
                run['proposal_warning'] = str(exc)
            if spec['schema'] is Dossier:
                output = generation.validate_dossier(output, generation.evidence_map(job))
        run.update(output=output, finished_at=db.now(), usage=job.get('usage', [])[usage_start:])
        store.save_run(job, role, fingerprint, run, run_id, 'completed')
        state['completed'][slot] = run_id
        for finding in output.get('findings', []):
            store.message(job, role, finding.get('recipient', 'quality'), 'finding', {'finding': finding, 'run_id': run_id})
        store.message(job, role, 'coordinator', 'delivery',
                      {'summary': output.get('summary', output.get('title', spec['name'] + ' concluído.')), 'run_id': run_id})
        db.save_job(job)
        return deepcopy(output), run_id
    except Exception as exc:
        # Never persist provider exception bodies, which can contain credentials or input content.
        run.update(error_type=type(exc).__name__, finished_at=db.now(), usage=job.get('usage', [])[usage_start:])
        if isinstance(exc, (generation.GenerationResponseError, APIConnectionError)):
            run['error_reason'] = 'connection' if isinstance(exc, APIConnectionError) else exc.reason
            if isinstance(exc, generation.GenerationResponseError) and exc.diagnostics:
                run['validation_errors'] = exc.diagnostics
        if 'output' in locals() and isinstance(output, dict):
            run['rejected_output'] = output
        store.save_run(job, role, fingerprint, run, run_id, 'failed')
        if isinstance(exc, (generation.GenerationResponseError, APIConnectionError)):
            # One automatic transport/format recovery per slot per cycle, including
            # callbacks such as writer. Re-enter invoke so it is durable and budgeted.
            retryable = isinstance(exc, APIConnectionError) or exc.retryable
            reason = 'connection' if isinstance(exc, APIConnectionError) else exc.reason
            if retryable and not recovery and state['calls'] < state['profile']['profile']['max_calls']:
                state.setdefault('response_recoveries', {})[slot] = {
                    'reason': reason, 'max_output_tokens': min(16000, scope['max_output_tokens'] * 3 // 2)
                    if reason == 'max_output_tokens' else scope['max_output_tokens']}
                db.save_job(job)
                store.message(job, role, 'coordinator', 'recovery', {
                    'summary': 'A resposta não foi concluída no formato esperado. A etapa terá uma nova tentativa automática.',
                    'run_id': run_id, 'reason': reason})
                return invoke(job, role, payload, callback, slot)
            raise
        if isinstance(exc, ValueError) and 'output' in locals() and not callback:
            feedback = state.setdefault('invalid_deliveries', {})
            previous = feedback.get(slot, {}).get('attempts', 0)
            feedback[slot] = {'attempts': previous + 1, 'error': safe_error(exc)[:500],
                              'rejected_findings': output.get('findings', []),
                              'instruction': 'Corrija a entrega inválida. Use cada ID de bloco no máximo uma vez e reúna suas correções numa única substituição. Não edite trechos sobrepostos. Copie trechos literalmente e use somente IDs recebidos. Se a observação não corresponde ao artigo real, remova-a.'}
            db.save_job(job)
            if previous == 0:
                return invoke(job, role, payload, callback, slot)
        raise
    finally:
        generation.agent_scope.reset(token)


def edit(job, role, payload, slot):
    # A recovered proposal must not be recalculated against its own already-applied result.
    existing_id = job['editorial']['completed'].get(slot)
    existing = store.get_run(existing_id) if existing_id else None
    proposal = store.get_changes(job['id'], generation.article_hash({'run': existing_id, 'role': role})[:32]) if existing else None
    if proposal and proposal['status'] in ('applied', 'unchanged', 'invalid', 'rejected'):
        return proposal
    result, run_id = (deepcopy(existing['output']), existing_id) if existing and proposal else invoke(
        job, role, {'article': job['article'], **payload}, slot=slot)
    job['editorial'].setdefault('sector_requests', {})[role] = {
        'article_hash': generation.article_hash(job['article']), 'findings': result.get('findings', [])}
    item = changes.propose(job, role, result, run_id)
    if item['status'] == 'invalid':
        job['editorial'].setdefault('unapplied_proposals', {})[item['id']] = {
            'role': role, 'reason': item['error'], 'summary': item['summary']}
        db.save_job(job)
    if item['status'] == 'pending' and job['editorial']['profile']['profile']['auto_apply']:
        changes.decide(job, item, 'apply', generation.article_hash(job['article']), automatic=True)
        job['editorial']['stale'] = False
        job['editorial'].pop('stale_reason', None)
        db.save_job(job)
    store.message(job, role, 'quality', 'proposal',
                  {'summary': item['summary'], 'change_id': item['id'], 'status': item['status']})
    return item


def seo_team(job, round_index):
    payload = {'article': job['article'], 'local_checks': checks.analyze(job)}
    strategy, _ = invoke(job, 'strategist', payload, slot=f'strategist:{round_index}')
    analysis, _ = invoke(job, 'yoast_analyst', payload, slot=f'yoast_analyst:{round_index}')
    item = edit(job, 'seo_editor', {'strategy': strategy, 'yoast_analysis': analysis,
                                   'local_checks': payload['local_checks']}, f'seo_editor:{round_index}')
    job['editorial']['seo'] = {'strategy': strategy, 'analysis': analysis, 'changes': item['id']}
    db.save_job(job)


def final_review(job, round_index):
    from . import workflow
    if workflow.enabled() and job.get('apuration', {}).get('valid'):
        factual = workflow.factual_review(job, round_index)
    else:
        factual, _ = invoke(job, 'fact_reviewer', {'article': job['article']}, generation.review_article, f'fact_reviewer:{round_index}')
    reading, _ = invoke(job, 'readability_reviewer', {'article': job['article']}, slot=f'readability_reviewer:{round_index}')
    chief, _ = invoke(job, 'chief', {'article': job['article'], 'factual_review': factual,
                                    'reading_review': reading, 'local_checks': checks.analyze(job),
                                    'earlier_sector_requests': job['editorial'].get('sector_requests', {}),
                                    'unapplied_proposals': job['editorial'].get('unapplied_proposals', {}),
                                    'request_notice': 'Confira se os pedidos anteriores ainda se aplicam ao artigo atual; não copie trechos de versões anteriores.'}, slot=f'chief:{round_index}')
    review = deepcopy(factual)
    for item in job['editorial'].get('unapplied_proposals', {}).values():
        review['findings'].append({'severity': 'warning', 'passage': '',
            'reason': 'Uma sugestão editorial não foi aplicada: ' + item['reason'],
            'suggestion': 'Confira a proposta na aba Equipe editorial. O texto anterior foi preservado.',
            'source_ids': [], 'origin': 'proposal_validation'})
    for role, result in [('readability_reviewer', reading), ('chief', chief)]:
        for finding in result.get('findings', []):
            key = (finding['severity'], finding['passage'], finding['reason'])
            if not any((f['severity'], f['passage'], f['reason']) == key for f in review['findings']):
                review['findings'].append({**finding, 'origin': role})
    if chief['decision'] != 'ready' and not any(f['severity'] == 'blocking' for f in review['findings']):
        review['findings'].append({'severity': 'blocking', 'passage': '', 'reason': chief['summary'],
                                   'suggestion': 'Confira as pendências da equipe editorial.', 'source_ids': [], 'origin': 'chief'})
    review.update(article_hash=generation.article_hash(job['article']), reviewed_at=db.now())
    job['review'] = review
    job['editorial']['decision'] = chief
    job['editorial']['reading'] = reading
    job['editorial']['reviewed_hash'] = review['article_hash']
    db.save_job(job)
    return chief


def run(job, mode):
    mode = start(job, mode)
    state = job['editorial']
    from . import workflow
    if workflow.enabled() and mode in ('generate', 'plan', 'write') and not state.get('initial_complete'):
        if mode == 'write' and not workflow.compatible(job):
            raise ValueError('O plano depende de outra versão das fontes ou do perfil. Planeje novamente antes de redigir.')
        if not workflow.compatible(job):
            workflow.extract(job)
        plan = job.get('plan') or {}
        if mode == 'plan' or not plan.get('valid') or plan.get('input_version') != store.inputs_version(job):
            workflow.plan(job)
        if mode == 'plan' or mode == 'generate' and not state['profile']['profile']['auto_write']:
            state.update(current_role=None, finished_at=db.now(), planned=True)
            db.save_job(job)
            return None
        if not state.get('draft_installed'):
            article = workflow.write(job)
            if article is None:
                return None
            db.revision(job)
            job['article'] = article
            state['draft_installed'] = True
            job.update(generation_complete=True, article_needs_generation=False,
                       article_editorial_version=generation.EDITORIAL_VERSION,
                       article_evidence_version=workflow.VERSION, review=None)
            store.artifact(job, 'article', 'draft', article,
                           {**workflow.dependencies(job), 'plan': job['plan']['version']})
            db.save_job(job)
    elif mode == 'generate' and not state.get('initial_complete'):
        extracted, _ = invoke(job, 'extractor', callback=generation.extract_dossier)
        checked, _ = invoke(job, 'source_checker', {'dossier': extracted})
        job['dossier'] = extracted
        if job['brief']['research']:
            research, _ = invoke(job, 'source_checker', {'dossier': extracted, 'checks': checked},
                                 generation.research, 'research:initial')
            job['research'] = research
        else:
            job['research'] = {'text': '', 'sources': [], 'notice': 'Pesquisa complementar desativada neste artigo.'}
        planned, _ = invoke(job, 'planner', {'dossier': extracted, 'source_review': checked})
        job['dossier'] = planned
        article, _ = invoke(job, 'writer', {'dossier': planned}, generation.write_article)
        # Do not replace a later edited version when resuming after this checkpoint.
        if not state.get('draft_installed'):
            db.revision(job)
            job['article'] = article
            state['draft_installed'] = True
            job.update(generation_complete=True, article_needs_generation=False,
                       article_editorial_version=generation.EDITORIAL_VERSION, review=None)
            db.save_job(job)
    if mode != 'review' and not state.get('initial_complete'):
        previous_reading = store.get_run(state['completed'].get('reader', ''))
        reading = deepcopy(previous_reading['output']) if previous_reading else invoke(job, 'reader', {'article': job['article']})[0]
        edit(job, 'voice_editor', {'reading_review': reading}, 'voice_editor:0')
        seo_team(job, 0)
    state['initial_complete'] = True
    db.save_job(job)
    max_rounds = state['profile']['profile']['max_rounds'] if mode != 'review' and state['profile']['profile']['auto_apply'] else 0
    while True:
        pending = state.get('correction_pending')
        if pending:
            round_index = pending['round']
            state['round'] = round_index
            blocking, chief = pending['findings'], pending['chief']
            requires_sources = any(f.get('recipient') == 'apuration' for f in blocking)
            if requires_sources and job['brief']['research'] and not pending.get('research_added'):
                if workflow.enabled() and job.get('apuration', {}).get('valid'):
                    from . import research as research_flow
                    research_flow.run(job, [f['reason'] for f in blocking if f.get('recipient') == 'apuration'])
                    workflow.plan(job)
                    pending['research_added'] = True
                    db.save_job(job)
                else:
                    additional, _ = invoke(job, 'source_checker', {'findings': blocking}, generation.research,
                                       f'research:correction:{round_index}')
                    research = job.setdefault('research', {'sources': []})
                    for source in additional.get('sources', []):
                        research['sources'].append(dict(source, id=f'w{len(research["sources"])+1}'))
                    research['notice'] = additional.get('notice', research.get('notice', ''))
                    pending['research_added'] = True
                    db.save_job(job)
            edit(job, 'voice_editor', {'correction_requests': blocking, 'chief': chief}, f'voice_editor:{round_index}')
            seo_team(job, round_index)
            state['review_round'] = round_index
            state.pop('correction_pending', None)
            db.save_job(job)
        round_index = state.get('review_round', 0)
        state['round'] = round_index
        chief = final_review(job, round_index)
        blocking = generation.unresolved_findings(job)
        if not blocking or round_index >= max_rounds or chief['decision'] == 'needs_input':
            break
        store.message(job, 'chief', 'writing', 'request', {'summary': chief['summary'], 'findings': blocking})
        state['correction_pending'] = {'round': round_index + 1, 'findings': blocking, 'chief': chief}
        db.save_job(job)
    state['current_role'] = None
    state['finished_at'] = db.now()
    state['stale'] = False
    db.save_job(job)
    return job['review']
