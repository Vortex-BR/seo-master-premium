"""Bounded source-backed revision, independently audited before installation."""
from copy import deepcopy
import re

from .. import db, generation, spending
from . import composition, engine, guidance, store, text_checks, video_first, workflow

VERSION = 1
REPAIR_INSTRUCTION = composition.INSTRUCTION + '''
Esta entrega corrige um rascunho já salvo. repair contém o diagnóstico concreto
da conferência independente. Corrija as omissões, a direção e a estrutura
identificadas, mantendo o que já está correto e a voz dos criadores.
Confronte os detalhes ausentes diretamente com os trechos originais fornecidos.
O resumo extraído e o plano não autorizam omitir conteúdo relevante da fonte.
Integre os detalhes sustentados no lugar adequado; ajuste a organização quando
necessário para responder à pauta. Não crie fatos, etapas, motivos ou quantidades
ausentes das fontes. Preserve as alternativas e ressalvas com sua atribuição.
Entregue o artigo completo corrigido. Não responda com uma lista de problemas,
um pedido de aprovação, notas ao editor ou uma promessa de correção futura.
'''


def _request(job, role, schema, instruction, payload, slot):
    payload = {'_local_context': True, **payload}
    scope, _, _ = engine.invocation_inputs(job, role, payload, None, slot)
    token = generation.agent_scope.set(scope)
    try:
        return generation.prepare_structured(job, schema, instruction, role, payload)[0]
    finally:
        generation.agent_scope.reset(token)


def _preflight_bounds(requests, remaining):
    """Prefer offline estimates; refine only a refusal that counting can resolve."""
    bounds = [spending.text_request_bound(request) if request else spending.amount(0)
              for request, _ in requests]
    if sum(bounds) <= remaining:
        return bounds
    floor = sum(spending.minimum_text_request_bound(request, additional_input_tokens=extra)
                for request, extra in requests if request)
    if floor > remaining:
        return bounds
    try:
        api = generation.client()
    except Exception:
        return bounds
    return [spending.counted_text_request_bound(api, request, additional_input_tokens=extra)
            if request else spending.amount(0) for request, extra in requests]


def _local(job):
    return [f for f in text_checks.analyze(job)['findings'] if f.get('auto_repair')]


def repair_findings(job):
    result = []
    compliance = (job.get('review') or {}).get('brief_compliance')
    correctable_brief = not compliance or any(a['status'] in ('partial', 'missing', 'violated')
                                              for a in compliance['assessments'])
    uncertain_sources = {ident for f in (job.get('review') or {}).get('findings', [])
                         if f.get('origin') == 'semantic_review' and f.get('assessment_status') == 'uncertain'
                         for ident in f.get('source_ids', [])}
    for finding in (job.get('review') or {}).get('findings', []):
        origin = finding.get('origin')
        if (origin == 'coverage' and not set(finding.get('source_ids', [])) & uncertain_sources
                or origin == 'editorial_alignment' and correctable_brief
                or origin == 'brief_compliance' and finding.get('assessment_status') in ('partial', 'missing', 'violated')
                or origin == 'source_coverage' and finding.get('assessment_status') in ('missing', 'partial')
                or origin == 'semantic_review' and finding.get('assessment_status') == 'unsupported'
                or set(finding.get('rule_ids', [])) & {'video_first.attribution', 'video_first.no_ai_jargon'}):
            result.append(deepcopy(finding))
    return result + _local(job)


def _score(job, review):
    assessments = review.get('source_coverage', {}).get('assessments', [])
    incomplete = [a for a in assessments if a['status'] in ('partial', 'missing')]
    return (
        sum(f.get('origin') == 'semantic_review' and f.get('assessment_status') == 'unsupported'
            for f in review.get('findings', [])),
        sum(f.get('origin') == 'semantic_review' and f.get('assessment_status') == 'uncertain'
            for f in review.get('findings', [])),
        len(incomplete), sum(len(a['missing_details']) for a in incomplete),
        sum(c.get('planned_status') == 'used' and c['status'] not in ('used', 'excluded') for c in review.get('coverage', [])),
        int(not review.get('editorial_alignment', {}).get('matches_brief', True)),
        len(_local(job)),
        sum(f['severity'] == 'blocking' for f in review.get('findings', [])),
        sum(a['status'] != 'met' for a in review.get('brief_compliance', {}).get('assessments', [])),
    )


def _acceptable(job, before, candidate, after):
    context = {**job, 'article': candidate}
    old, new = _score(job, before), _score(context, after)
    old_sources = {a['source_id']: a['status'] for a in before.get('source_coverage', {}).get('assessments', [])}
    new_sources = {a['source_id']: a['status'] for a in after.get('source_coverage', {}).get('assessments', [])}
    allowed = {'complete': {'complete'}, 'partial': {'partial', 'complete'},
               'missing': {'missing', 'partial', 'complete'}, 'uncertain': {'uncertain'},
               'excluded': {'excluded', 'complete'}}
    exclusions = set(after.get('brief_compliance', {}).get('excluded_source_ids', []))
    preserved = all(new_sources.get(ident) in allowed[status]
                    or new_sources.get(ident) == 'excluded' and ident in exclusions
                    for ident, status in old_sources.items())
    no_new_uncertainty = all(old_sources.get(ident) == 'uncertain' for ident, status in new_sources.items() if status == 'uncertain')
    old_used = {c['item_id'] for c in before.get('coverage', []) if c['status'] == 'used'}
    new_used = {c['item_id'] for c in after.get('coverage', []) if c['status'] == 'used'}
    new_used.update(after.get('brief_compliance', {}).get('excluded_item_ids', []))
    for item in job.get('apuration', {}).get('items', []):
        ids = {e['source_id'] for e in item.get('evidence', [])}
        if ids and ids <= exclusions:
            new_used.add(item['id'])
    before_requirements = {a['requirement_id']: a['status'] for a in before.get('brief_compliance', {}).get('assessments', [])}
    after_requirements = {a['requirement_id']: a['status'] for a in after.get('brief_compliance', {}).get('assessments', [])}
    requirement_progress = {
        'met': {'met'}, 'partial': {'partial', 'met'}, 'missing': {'missing', 'partial', 'met'},
        'violated': {'violated', 'missing', 'partial', 'met'},
        'conflict': {'conflict', 'met'}, 'uncertain': {'uncertain', 'met'},
    }
    requirements_preserved = all(after_requirements.get(ident) in requirement_progress[status]
                                 for ident, status in before_requirements.items())
    def uncertain_claim(finding):
        # One source can support several unrelated claims. Source identity alone
        # cannot establish that an uncertain statement survived unchanged.
        return (tuple(sorted(finding.get('source_ids', []))),
                re.sub(r'\s+', ' ', finding.get('passage', '')).strip().casefold())

    old_uncertain = {uncertain_claim(f)
                     for f in before.get('findings', []) if f.get('origin') == 'semantic_review'
                     and f.get('assessment_status') == 'uncertain'}
    new_uncertain = {uncertain_claim(f)
                     for f in after.get('findings', []) if f.get('origin') == 'semantic_review'
                     and f.get('assessment_status') == 'uncertain'}
    preservation = after.get('preservation') or {}
    return (preserved and requirements_preserved and no_new_uncertainty and old_used <= new_used and new_uncertain <= old_uncertain
            and preservation.get('preserves_supported_details') is True and not preservation.get('regressions')
            and new[0] == 0
            and all(n <= o for o, n in zip(old, new)) and new != old), old, new


def _save(job, report):
    with db.job_transaction(job['id']) as current:
        expected = job['editorial']['cycle_id']
        # A concurrent upstream edit must never be overwritten by an optional
        # revision's error handler. Its evidence remains available separately.
        changed = current is not None and (
            current.get('editorial', {}).get('cycle_id') != expected
            or generation.article_hash(current.get('article')) != generation.article_hash(job.get('article'))
            or store.inputs_version(current) != store.inputs_version(job)
            or (current.get('plan') or {}).get('version') != (job.get('plan') or {}).get('version'))
        store.artifact(job, 'quality_repair', report['before_hash'], report,
                       {'cycle': expected, 'article': report['before_hash'],
                        'plan': (job.get('plan') or {}).get('version')})
        if changed:
            state = job['editorial']
            job.clear()
            job.update(current)
            state.clear()
            state.update(current.get('editorial', {}))
            job['editorial'] = state
            return {**report, 'status': 'deferred', 'reason': 'dependencies_changed'}
        job['editorial']['quality_repair'] = report
        db.save_job(job)
    return report


def _attempted(job, slot):
    return any(r['cycle_id'] == job['editorial']['cycle_id'] and r['data'].get('slot') == slot
               for r in store.report(job)['runs'])


def run(job):
    """One revision per generation cycle; a review-only request never edits."""
    state = job['editorial']
    if not state.get('quality_repair_version') or state.get('mode') == 'review':
        return None
    before = job.get('review') or {}
    if before.get('review_incomplete') or before.get('article_hash') != generation.article_hash(job.get('article')):
        return None
    previous = state.get('quality_repair')
    if previous and previous['status'] != 'started':
        # Recover only the known preflight defect on an explicit continuation,
        # with identical dependencies and no attempted paid correction.
        prior_slot = f"quality-repair:{VERSION}:{previous['before_hash']}"
        recoverable = (previous['status'] == 'failed' and previous.get('reason') == 'TypeError'
            and 'phase' not in previous and job['brief'].get('target_words') is None
            and previous['before_hash'] == generation.article_hash(job.get('article'))
            and previous.get('inputs_version') == store.inputs_version(job)
            and previous.get('plan_version') == (job.get('plan') or {}).get('version')
            and not _attempted(job, prior_slot))
        if not recoverable:
            return previous
        previous = {**previous, 'status': 'started', 'phase': 'preflight',
                    'recovered_preflight_error': 'automatic_length', 'reason': None}
    findings = repair_findings(job)
    if not findings:
        return None
    original = deepcopy(job['article'])
    before_hash = generation.article_hash(original)
    report = previous or {'version': VERSION, 'before_hash': before_hash, 'status': 'started',
                          'max_revisions': 1, 'started_at': db.now(),
                          'inputs_version': store.inputs_version(job), 'plan_version': (job.get('plan') or {}).get('version')}
    if report['before_hash'] != before_hash:
        return _save(job, {**report, 'status': 'deferred', 'reason': 'article_changed'})
    plan = (job.get('plan') or {}).get('data')
    if not plan:
        return _save(job, {**report, 'status': 'deferred', 'reason': 'plan_unavailable'})
    used = {d['item_id'] for d in plan.get('dispositions', []) if d['status'] == 'used'}
    items = [i for i in job.get('apuration', {}).get('items', []) if i['id'] in used]
    sources = generation.evidence_map(job)
    payload = {'_composition_contract': composition.VERSION, '_local_context': True,
               '_quality_repair_contract': VERSION, '_no_recovery': True, '_budget_reserve': 1,
               'article_route': guidance.article_route(plan),
               'items': [workflow.writing_item(i) for i in items], 'counterpoints': [],
               'required_qualifications': composition.qualifications(items),
               'creator_voice': composition.creator_voice(job, sources),
               'target_words_total': job['brief']['target_words'], '_context_sources': sources,
               'repair': {'draft': original, 'findings': findings}}
    slot = f'quality-repair:{VERSION}:{before_hash}'
    cached = engine.cached_invocation(job, 'writer', payload, slot=slot)
    # A crashed/failed optional request is not authorization to pay again.
    if previous and not cached:
        if _attempted(job, slot):
            return _save(job, {**report, 'status': 'deferred', 'reason': 'previous_request_unconfirmed'})
    phase = 'preflight'
    try:
        cached_review = None
        if cached:
            cached_candidate = composition._article(cached['output'], job)
            cached_hash = generation.article_hash(cached_candidate)
            cached_material, _, _ = video_first.build_review_payload(job, cached_candidate,
                                                   previous_article=original, no_recovery=True)
            cached_review = engine.cached_invocation(job, 'fact_reviewer', cached_material,
                                                     slot='video-fidelity:' + cached_hash)
        calls_needed = (0 if cached_review else 1) if cached else 2
        if workflow.remaining(job) < calls_needed:
            return _save(job, {**report, 'status': 'deferred', 'reason': 'call_budget'})
        # Anticipate a larger correction, then check the exact candidate again.
        # This is a conservative allowance, not a promise about future output.
        forecast = deepcopy(cached_candidate if cached else original)
        growth_tokens = 0
        if not cached:
            target = job['brief'].get('target_words')
            allowance = max(len(original['markdown']), target * 12 if target is not None else 0)
            growth_tokens = min(60000, allowance)
            forecast['markdown'] += '\n\n' + 'x' * growth_tokens
        review_material, _, _ = video_first.build_review_payload(job, forecast,
                                        previous_article=original, no_recovery=True)
        review_request = (None if cached_review else
                _request(job, 'fact_reviewer', video_first.VideoFidelityReview,
                    video_first.review_instruction(original), review_material, 'quality-review-forecast'))
        writer_request = (None if cached else _request(job, 'writer', composition.DraftArticle,
                    REPAIR_INSTRUCTION, payload, slot))
        remaining = spending.amount(spending.summary(job)['remaining_usd'])
        # Repeated forecast characters tokenize cheaply. Preserve one extra
        # token per allowance character even after provider counting.
        writer_bound, hold = _preflight_bounds([(writer_request, 0), (review_request, growth_tokens)], remaining)
        payload['_downstream_usd'] = float(hold)
        if writer_bound + hold > remaining:
            return _save(job, {**report, 'status': 'deferred', 'reason': 'financial_budget',
                               'reserved_review_estimate_usd': float(hold)})
        if _save(job, report)['status'] == 'deferred':
            return {'status': 'deferred', 'reason': 'dependencies_changed'}
        store.artifact(job, 'quality_repair_before', before_hash, {'article': original, 'review': before},
                       {'cycle': state['cycle_id'], 'article': before_hash})

        def validate(output):
            workflow.known_ids(output['used_item_ids'], used, 'Uso na correção')
            workflow.known_ids(re.findall(r'\[\[([\w-]+)\]\]', output['markdown']), sources, 'Fontes da correção')
            composition._article(output, job)

        phase = 'writing'
        output = workflow.call(job, 'writer', composition.DraftArticle, REPAIR_INSTRUCTION, payload, slot, validate)
        candidate = composition._article(output, job)
        candidate_hash = generation.article_hash(candidate)
        store.artifact(job, 'quality_repair_candidate', candidate_hash, candidate,
                       {'cycle': state['cycle_id'], 'article': before_hash})
        if candidate_hash == before_hash:
            return _save(job, {**report, 'status': 'rejected', 'reason': 'unchanged_candidate'})
        review_material, _, _ = video_first.build_review_payload(job, candidate,
                                        previous_article=original, no_recovery=True)
        review_slot = 'video-fidelity:' + candidate_hash
        review_cached = engine.cached_invocation(job, 'fact_reviewer', review_material, slot=review_slot)
        if not review_cached and _attempted(job, review_slot):
            return _save(job, {**report, 'status': 'deferred', 'reason': 'previous_review_unconfirmed'})
        exact_request = (None if review_cached else
                       _request(job, 'fact_reviewer', video_first.VideoFidelityReview,
                       video_first.review_instruction(original), review_material, 'video-fidelity:' + candidate_hash))
        remaining = spending.amount(spending.summary(job)['remaining_usd'])
        exact_review = _preflight_bounds([(exact_request, 0)], remaining)[0]
        if not review_cached and exact_review > remaining:
            return _save(job, {**report, 'status': 'deferred', 'reason': 'candidate_review_budget',
                               'candidate_hash': candidate_hash})
        phase = 'reviewing'
        after = video_first.factual_review(job, 1, article=candidate, previous_article=original)
        context = {**job, 'article': candidate}
        after['findings'].extend(engine.checks.blocking_findings(context))
        after['findings'].extend(f for f in text_checks.analyze(context)['findings'] if f['severity'] != 'blocking')
        store.artifact(job, 'quality_repair_review', candidate_hash, after,
                       {'cycle': state['cycle_id'], 'article': candidate_hash})
        accepted, old_score, new_score = _acceptable(job, before, candidate, after)
        report.update(candidate_hash=candidate_hash, before_score=list(old_score), after_score=list(new_score))
        if not accepted:
            return _save(job, {**report, 'status': 'rejected', 'reason': 'no_verified_improvement'})
        phase = 'installing'
        staged = deepcopy(job)
        staged['article'] = candidate
        staged['article_evidence_version'] = workflow.VERSION
        staged['coverage'] = {'article_hash': candidate_hash, 'items': after['coverage'],
                          'blocks': job.get('coverage', {}).get('blocks', [])}
        staged['review'] = after
        from . import review_policy
        review_policy.annotate_review(staged, after)
        staged['editorial']['reviewed_hash'] = candidate_hash
        staged['editorial']['decision'] = {'decision': 'revise' if any(f['severity'] == 'blocking' for f in after['findings']) else 'ready',
                             'summary': after['summary'], 'findings': after['findings']}
        if staged.get('draft_delivery'):
            staged['draft_delivery'].update(review_pending=False, updated_at=db.now())
        staged['local_seo_checks'] = engine.checks.analyze(staged)
        with db.job_transaction(job['id']) as current:
            if (generation.article_hash(current.get('article')) != before_hash
                    or current.get('editorial', {}).get('cycle_id') != state['cycle_id']
                    or store.inputs_version(current) != store.inputs_version(job)
                    or (current.get('plan') or {}).get('version') != (job.get('plan') or {}).get('version')):
                raise ValueError('A entrega ou suas dependências mudaram durante a correção.')
            db.revision(current)
            staged['review'] = before
            store.archive_review(staged, 'Uma revisão corrigida foi conferida; o diagnóstico anterior foi preservado.')
            staged['review'] = after
            # Persist article, review, history and acceptance in this same
            # transaction, using current as the comparison base for _save.
            staged['editorial']['quality_repair'] = {**report, 'status': 'accepted',
                                                    'after_hash': candidate_hash, 'finished_at': db.now()}
            result = staged['editorial']['quality_repair']
            store.artifact(staged, 'quality_repair', report['before_hash'], result,
                           {'cycle': state['cycle_id'], 'article': before_hash, 'plan': job['plan']['version']})
            db.save_job(staged)
        job.clear()
        job.update(staged)
        state.clear()
        state.update(staged['editorial'])
        job['editorial'] = state
        return result
    except Exception as exc:
        # The candidate was never installed before a completed independent audit.
        # Paid runs/costs remain in their ordinary durable ledger.
        return _save(job, {**report, 'status': 'failed', 'reason': type(exc).__name__, 'phase': phase})


def describe_outcome(job, report):
    """An incomplete correction must be visible alongside the preserved article."""
    if not report or report.get('status') not in ('failed', 'deferred', 'rejected') or not job.get('review'):
        return
    status = report['status']
    messages = {
        'failed': 'A correção automática não foi concluída por uma falha técnica. O artigo anterior permanece salvo com pendências.',
        'deferred': 'A correção automática não foi concluída; confira o saldo e as pendências da geração. O artigo anterior permanece salvo.',
        'rejected': 'A proposta de correção não apresentou melhora confirmada. O artigo anterior permanece salvo com pendências.',
    }
    reason = report.get('reason')
    message = messages[status]
    if reason in ('previous_request_unconfirmed', 'previous_review_unconfirmed'):
        message = 'Uma chamada de correção foi interrompida. Ela não foi repetida, para evitar cobrança duplicada; o artigo anterior permanece salvo.'
    review = job['review']
    review['findings'] = [f for f in review.get('findings', []) if f.get('code') != 'automatic_correction_incomplete']
    review['findings'].append({'code': 'automatic_correction_incomplete', 'severity': 'warning',
        'origin': 'correction_service', 'recipient': 'quality', 'passage': '', 'source_ids': [],
        'reason': message, 'suggestion': 'Consulte o diagnóstico da geração e as pendências do artigo.'})
    review['summary'] = message
    job['editorial']['decision'] = {'decision': 'revise', 'summary': message, 'findings': review['findings']}
