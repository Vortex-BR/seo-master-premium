from copy import deepcopy
import re
from openai import APIConnectionError

from .. import db, generation, spending
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
                and knowledge.continuation_compatible(previous.get('knowledge_version'))
                and previous.get('model') == generation.model() and previous.get('video_first')
                and store.voice(current) == store.voice(previous['profile'])
                and workflow.compatible(job)):
            # Planning and writing belong to the same unfinished budget, even
            # when the plan received a recorded editorial correction.
            previous.update(mode='write', stale=False, current_role=None)
            previous['profile']['profile'] = store.bounded_profile(previous['profile']['profile'])
            previous['profile']['profile']['max_calls'] = min(24, max(current['profile']['max_calls'], previous['profile']['profile']['max_calls']))
            previous['profile']['profile']['context_chars'] = max(current['profile']['context_chars'], previous['profile']['profile']['context_chars'])
            previous.pop('stale_reason', None)
            previous.pop('finished_at', None)
            db.save_job(job)
            return 'write'
    if mode == 'resume' and previous and not previous.get('stale') and previous.get('input_hash') == inputs_hash(job):
        if previous.get('agents_version') == agents.VERSION and previous.get('video_first'):
            # Budget increases after an interruption do not alter the frozen editorial voice.
            current = store.profile()['profile']
            previous['profile']['profile'] = store.bounded_profile(previous['profile']['profile'])
            previous['profile']['profile']['max_calls'] = min(24, max(current['max_calls'], previous['profile']['profile']['max_calls']))
            previous['profile']['profile']['context_chars'] = max(current['context_chars'], previous['profile']['profile']['context_chars'])
            previous.pop('budget_pending', None)
            return previous['mode']
    actual_mode = mode if mode in ('review', 'optimize', 'plan', 'write') else 'generate'
    job['editorial'] = {'cycle_id': store.new_id(), 'mode': actual_mode, 'started_at': db.now(),
                        'profile': store.profile(), 'knowledge_version': knowledge.package()['version'],
                        'agents_version': agents.VERSION, 'model': generation.model(),
                        'input_hash': inputs_hash(job), 'completed': {}, 'calls': 0,
                        'current_role': None, 'round': 0, 'stale': False}
    # All new cycles use one full composition and a single factual review.
    job['editorial'].update(video_first=True, composition_version=1, planning_version=1, tool_calls=0)
    research = job.setdefault('research', {'text': '', 'sources': []})
    research['internal_context_only'] = True
    research['agent_background_knowledge'] = {'internal_context_only': True, 'terms': []}
    for source in research.get('sources', []) + research.get('pages', []):
        source.update(internal_context_only=True, verified=False)
    if actual_mode != 'review':
        job.pop('research_requests_completed', None)
        job.pop('research_request_results', None)
    store.archive_review(job, 'Novo ciclo editorial iniciado; a análise anterior permanece no histórico.')
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


def invocation_inputs(job, role, payload, callback, slot):
    """Build the exact cache identity for execution and budget reservation alike."""
    state = job['editorial']
    spec = agents.ROLES[role]
    docs = knowledge.retrieve(spec['sector'], job['brief'].get('keyword', ''), state['knowledge_version'])
    scope = {'role': role, 'name': spec['name'], 'sector': spec['sector'], 'profile': state['profile'],
             'video_first': state.get('video_first', False),
             'knowledge': docs, 'model': state['model'],
             'research_requests': payload.get('findings', payload.get('checks', {}).get('findings', [])),
             'correction_of_invalid_delivery': state.get('invalid_deliveries', {}).get(slot),
             'max_output_tokens': 8000 if role in ('extractor', 'planner', 'writer', 'fact_reviewer') else 4500}
    if callback is generation.research:
        scope['research_tool_budget'] = min(2, state['profile']['profile']['research_tool_calls'])
        scope['mentioned_terms'] = payload.get('mentioned_terms', [])
    if '_context_sources' in payload:
        scope['context_sources'] = payload['_context_sources']
    elif job.get('apuration', {}).get('valid'):
        from .workflow import context_sources
        scope['context_sources'] = context_sources(job, payload)
    if 'context_sources' in scope:
        video_ids = generation.evidence_map(job)
        scope['context_sources'] = {ident: source for ident, source in scope['context_sources'].items()
                                    if ident in video_ids and not source.get('internal_context_only')
                                    and source.get('kind', 'transcript') == 'transcript'}
    if job.get('apuration') and (role not in ('extractor', 'source_checker') or callback is generation.research):
        from .guidance import source_guide
        scope['source_guidance'] = source_guide(job)
    recovery = state.get('response_recoveries', {}).get(slot)
    if recovery:
        scope['response_recovery'] = recovery
        scope['max_output_tokens'] = recovery['max_output_tokens']
    if payload.get('article'):
        scope['article_passages'] = article_passages(payload['article'])
        scope['article_passage_refs'] = {f'p{n}': text for n, text in enumerate(scope['article_passages'])}
        scope['article_title'] = payload['article']['title']
        from .contracts import EditPlan
        if role in ('voice_editor', 'seo_editor') or spec.get('schema') is EditPlan:
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
    # Operational reservations must not invalidate already paid deliveries.
    scope['budget_reserve'] = payload.get('_budget_reserve', 0)
    fingerprint_scope = {k: v for k, v in scope.items() if k != 'budget_reserve'}
    fingerprint_scope['profile'] = store.voice(scope['profile'])
    fingerprint_payload = {k: v for k, v in payload.items() if k != '_budget_reserve'}
    fingerprint = generation.article_hash({'scope': fingerprint_scope, 'payload': fingerprint_payload, 'input': state['input_hash'],
                                            'agent_version': agents.VERSION,
                                            'plan_version': None if role in ('extractor', 'planner') else (job.get('plan') or {}).get('version'),
                                            'knowledge_version': None if role == 'extractor' else (job.get('apuration') or {}).get('version'),
                                            'sources': scope.get('context_sources', generation.evidence_map(job))})
    return scope, docs, fingerprint


def cached_invocation(job, role, payload=None, callback=None, slot=None, fingerprint=None):
    slot = slot or role
    if fingerprint is None:
        _, _, fingerprint = invocation_inputs(job, role, payload or {}, callback, slot)
    existing_id = job['editorial']['completed'].get(slot)
    existing = store.get_run(existing_id) if existing_id else None
    if existing and existing.get('input_hash') == fingerprint:
        return existing
    return store.cached(job, role, fingerprint)


def invoke(job, role, payload=None, callback=None, slot=None):
    from ..pipeline import safe_error, step
    payload = payload or {}
    slot = slot or role
    state = job['editorial']
    if state.get('video_first') and role not in agents.ACTIVE_ROLES:
        raise ValueError('Este papel foi retirado do fluxo Video-First. Use extração, pauta, redação ou revisão factual.')
    spec = agents.ROLES[role]
    scope, docs, fingerprint = invocation_inputs(job, role, payload, callback, slot)
    recovery = state.get('response_recoveries', {}).get(slot)
    cached = cached_invocation(job, role, payload, callback, slot, fingerprint)
    if cached:
        state['completed'][slot] = cached['run_id']
        db.save_job(job)
        return deepcopy(cached['output']), cached['run_id']
    cost = 1 + (scope.get('research_tool_budget', 0) if callback is generation.research else 0)
    limit = min(24, state['profile']['profile']['max_calls']) - payload.get('_budget_reserve', 0)
    if state['calls'] + cost > limit:
        from .workflow import BudgetExceeded
        raise BudgetExceeded('A etapa atingiu o limite auxiliar de chamadas. As entregas foram preservadas. '
                             'Confira o rascunho e as pendências; o teto financeiro do artigo continua valendo ao retomar.')
    preflight = generation.agent_scope.set(scope)
    try:
        if callback is None:
            # Include instructions and the constrained output schema, not only
            # the raw materials. Callback-specific requests are checked by
            # structured() after evidence selection; failures remain unbilled.
            generation.prepare_structured(job, spec['schema'], spec['prompt'], role, payload)
    finally:
        generation.agent_scope.reset(preflight)
    state['calls'] += cost
    state['tool_calls'] = state.get('tool_calls', 0) + cost - 1
    state['current_role'] = role
    status = {'apuration': 'analyzing', 'writing': 'writing', 'seo': 'optimizing', 'quality': 'reviewing'}[spec['sector']]
    step(job, status, spec['name'] + ': trabalhando na ' + ('nova rodada de correção.' if state['round'] else 'entrega editorial.'))
    run_id = store.new_id()
    run = {'run_id': run_id, 'name': spec['name'], 'sector': spec['sector'], 'slot': slot,
           'model': state['model'], 'profile_version': state['profile']['version'], 'knowledge_version': docs['version'],
           'rule_ids': [r['id'] for r in docs['rules']], 'input_hash': fingerprint,
           'article_hash': generation.article_hash(job['article']) if job.get('article') else None,
           'started_at': db.now()}
    run['budget_calls'] = cost
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
        if isinstance(exc, (generation.ContextLimitExceeded, spending.SpendLimitExceeded)) and len(job.get('usage', [])) == usage_start:
            # The serialized prompt can exceed the limit after callback-specific
            # instructions/evidence are added. No provider request was sent.
            state['calls'] -= cost
            state['tool_calls'] -= cost - 1
            db.save_job(job)
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
            if retryable and not recovery and state['calls'] + cost <= limit:
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
            if previous == 0 and state['calls'] + cost <= limit:
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
    if role == 'voice_editor':
        from .text_checks import analyze
        payload = {**payload, 'local_editorial_review': analyze(job)}
    result, run_id = (deepcopy(existing['output']), existing_id) if existing and proposal else invoke(
        job, role, {'article': job['article'], **payload}, slot=slot)
    job['editorial'].setdefault('sector_requests', {})[role] = {
        'article_hash': generation.article_hash(job['article']), 'findings': result.get('findings', [])}
    item = changes.propose(job, role, result, run_id)
    if item['status'] == 'invalid':
        job['editorial'].setdefault('unapplied_proposals', {})[item['id']] = {
            'role': role, 'reason': item['error'], 'summary': item['summary']}
        db.save_job(job)
    if (item['status'] == 'pending' and item.get('automatic_eligible')
            and job['editorial']['profile']['profile']['auto_apply']):
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


def decision_report(factual):
    """Forward every finding, without duplicating the evidence audit in the decision prompt."""
    report = {key: deepcopy(value) for key, value in factual.items()
              if key not in ('supported_claims', 'semantic_coverage', 'reviewed_at')}
    report['supported_claims_count'] = len(factual.get('supported_claims', []))
    if factual.get('semantic_coverage'):
        report['semantic_coverage'] = {key: value for key, value in factual['semantic_coverage'].items()
                                       if key != 'assessments'}
    report['evidence_notice'] = ('A auditoria integral permanece salva no artefato factual_review. '
        'Todos os apontamentos e a cobertura seguem neste parecer como diagnósticos internos. '
        'Não repita a auditoria de cada evidência nem trate sugestões de aprofundamento como fatos ausentes '
        'sem conferir o artigo e a cobertura atuais.')
    return report


def reading_payload(job):
    from . import guidance, text_checks
    return {'article': job['article'], 'local_editorial_review': text_checks.analyze(job),
            'article_route': guidance.article_route(job['plan']['data']) if job.get('plan') else None}


def final_review(job, round_index):
    from . import review_policy
    review_policy.repair_local(job, round_index)
    if job['editorial'].get('video_first'):
        return video_first_final_review(job, round_index)
    from . import guidance, workflow
    if workflow.enabled() and job.get('apuration', {}).get('valid'):
        factual = workflow.factual_review(job, round_index)
    else:
        factual, _ = invoke(job, 'fact_reviewer', {'article': job['article']}, generation.review_article, f'fact_reviewer:{round_index}')
    # Save the complete audit before a subsequent reviewer can fail. Prompt compaction
    # must never remove evidence from the durable report or the final review.
    artifact = store.artifact(job, 'factual_review', generation.article_hash(job['article']),
                              {key: value for key, value in factual.items() if key != 'reviewed_at'},
                              {'article': generation.article_hash(job['article']),
                               'plan': (job.get('plan') or {}).get('version'),
                               'cycle': job['editorial']['cycle_id']})
    report = decision_report(factual)
    report['artifact_version'] = artifact['version']
    reading, _ = invoke(job, 'readability_reviewer', reading_payload(job), slot=f'readability_reviewer:{round_index}')
    chief, _ = invoke(job, 'chief', {'article': job['article'], 'factual_review': report,
                                    'article_route': guidance.article_route(job['plan']['data']) if job.get('plan') else None,
                                    '_context_sources': {}, '_local_context': True,
                                    'reading_review': reading, 'local_checks': checks.analyze(job),
                                    'earlier_sector_requests': job['editorial'].get('sector_requests', {}),
                                    'unapplied_proposals': job['editorial'].get('unapplied_proposals', {}),
                                    'request_notice': 'Confira se os pedidos anteriores ainda se aplicam ao artigo atual; não copie trechos de versões anteriores.'}, slot=f'chief:{round_index}')
    review = deepcopy(factual)
    from .text_checks import analyze
    review['findings'].extend(f for f in analyze(job)['findings'] if f['severity'] != 'blocking')
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
    review_policy.annotate_review(job, review)
    store.archive_review(job, 'Um novo diagnóstico editorial foi concluído.')
    job['review'] = review
    job['editorial']['decision'] = chief
    job['editorial']['reading'] = reading
    job['editorial']['reviewed_hash'] = review['article_hash']
    db.save_job(job)
    return chief


def defer_correction(job, code, reason, **details):
    """Keep a current review and visible draft instead of spending on a dead end."""
    review = job.get('review') or {}
    if review.get('article_hash') != generation.article_hash(job['article']):
        return False
    job['editorial']['correction_deferred'] = {'reason': reason, **details}
    finding = {'code': code, 'severity': 'warning', 'passage': '', 'reason': reason,
               'suggestion': 'Confira as pendências no rascunho preservado.', 'source_ids': [],
               'recipient': 'writing', 'origin': 'correction_deferred'}
    review['findings'] = [f for f in review.get('findings', []) if f.get('code') != code] + [finding]
    db.save_job(job)
    return True


def video_first_final_review(job, round_index=0):
    from . import workflow, text_checks, review_policy
    factual = workflow.factual_review(job, round_index)
    version = generation.article_hash(job['article'])
    store.artifact(job, 'factual_review', version, factual,
                   {'article': version, 'plan': (job.get('plan') or {}).get('version'),
                    'cycle': job['editorial']['cycle_id']})
    factual['findings'].extend(checks.blocking_findings(job))
    factual['findings'].extend(f for f in text_checks.analyze(job)['findings'] if f['severity'] != 'blocking')
    review_policy.annotate_review(job, factual)
    blocking = any(f['severity'] == 'blocking' for f in factual['findings'])
    decision = {'decision': 'revise' if blocking else 'ready', 'summary': factual['summary'],
                'findings': factual['findings']}
    store.archive_review(job, 'Um novo diagnóstico editorial foi concluído.')
    job['review'] = factual
    job['editorial'].update(decision=decision, reviewed_hash=version)
    job['local_seo_checks'] = checks.analyze(job)
    if job.get('draft_delivery'):
        job['draft_delivery']['review_pending'] = False
    db.save_job(job)
    return decision


def run(job, mode):
    """Four active deliveries with one conservative, free correction pass."""
    from . import workflow
    mode = start(job, mode)
    state = job['editorial']
    if mode != 'review':
        if mode == 'write' and not workflow.compatible(job):
            raise ValueError('O plano depende de outra versão das fontes ou do perfil. Planeje novamente antes de redigir.')
        if not workflow.compatible(job):
            workflow.extract(job)
        plan = job.get('plan') or {}
        if mode == 'plan' or not plan.get('valid') or plan.get('input_version') != store.inputs_version(job):
            workflow.plan(job)
        if mode == 'plan':
            state.update(current_role=None, finished_at=db.now(), planned=True)
            db.save_job(job)
            return None
        if not state.get('draft_installed'):
            article = workflow.write(job)
            if article is None:
                return None
            if not job.get('draft_delivery') or job['draft_delivery'].get('cycle_id') != state['cycle_id']:
                db.revision(job)
            job['article'] = article
            state['draft_installed'] = True
            job.update(generation_complete=True, article_needs_generation=False,
                       article_editorial_version=generation.EDITORIAL_VERSION,
                       article_evidence_version=workflow.VERSION, review=None)
            store.artifact(job, 'article', 'draft', article,
                           {**workflow.dependencies(job), 'plan': job['plan']['version']})
            db.save_job(job)
    if not job.get('article'):
        raise ValueError('Não há artigo disponível para revisão.')
    state['initial_complete'] = True
    db.save_job(job)
    try:
        final_review(job, 0)
        state.pop('review_failure', None)
    except Exception as exc:
        from .delivery import ensure_exportable
        # Review failures never discard a valid saved draft. Generation failures
        # before this boundary retain normal technical error/recovery handling.
        ensure_exportable(job)
        from ..pipeline import safe_error
        budget = isinstance(exc, (workflow.BudgetExceeded, spending.SpendLimitExceeded))
        version = generation.article_hash(job['article'])
        # Recover a completed audit even if a later local diagnostic failed.
        audits = [a for a in store.artifacts(job['id'], 'factual_review')
                  if a['dependencies'].get('article') == version
                  and a['dependencies'].get('cycle') == state['cycle_id']]
        review = deepcopy(audits[0]['data']) if audits else {
            'article_hash': version, 'supported_claims': [], 'findings': []}
        review.update(article_hash=version, reviewed_at=db.now(), review_incomplete=True,
            summary='Artigo disponível. A análise editorial não foi concluída ' +
                    ('por limite de orçamento.' if budget else 'por indisponibilidade da revisão.'))
        review.setdefault('findings', []).append({'severity': 'blocking', 'code': 'review_pending',
            'passage': '', 'reason': safe_error(exc),
            'suggestion': 'A versão salva pode ser exportada. Uma nova análise é opcional.',
            'source_ids': [], 'origin': 'budget' if budget else 'review_service', 'recipient': 'quality'})
        job['review'] = review
        state['review_failure'] = {'error_type': type(exc).__name__, 'message': safe_error(exc),
                                  'budget': budget, 'article_hash': version, 'at': db.now()}
        state['decision'] = {'decision': 'revise', 'summary': review['summary'], 'findings': review['findings']}
        if job.get('draft_delivery'):
            job['draft_delivery']['review_pending'] = True
    from . import review_policy
    try:
        review_policy.finalize(job)
    except Exception as exc:
        # A broken diagnostic must not replay its own failure while handling a
        # reviewer outage. Keep the audit and finish with explicit uncertainty.
        from ..pipeline import safe_error
        review = job['review']
        review.update(review_incomplete=True,
                      summary='Artigo disponível. Alguns diagnósticos editoriais não puderam ser concluídos.')
        review.setdefault('findings', []).append({'severity': 'warning',
            'code': 'review_diagnostics_unavailable', 'passage': '', 'reason': safe_error(exc),
            'suggestion': 'O artigo salvo permanece disponível para exportação.',
            'source_ids': [], 'origin': 'review_service', 'category': 'factual_uncertainty',
            'export_blocking': False, 'error_verified': False})
        for finding in review['findings']:
            finding['export_blocking'] = False
            finding.setdefault('category', 'factual_uncertainty')
        review['policy'] = {'version': review_policy.VERSION, 'export_blocking': False,
                            'editorial_state': 'uncertainties', 'diagnostics_incomplete': True}
        state['review_failure'] = {'error_type': type(exc).__name__, 'message': safe_error(exc),
                                  'article_hash': generation.article_hash(job['article']), 'at': db.now()}
    state.update(current_role=None, finished_at=db.now())
    db.save_job(job)
    return job['review']
