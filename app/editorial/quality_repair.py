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


def _local(job):
    return [f for f in text_checks.analyze(job)['findings'] if f.get('auto_repair')]


def repair_findings(job):
    result = []
    uncertain_sources = {ident for f in (job.get('review') or {}).get('findings', [])
                         if f.get('origin') == 'semantic_review' and f.get('assessment_status') == 'uncertain'
                         for ident in f.get('source_ids', [])}
    for finding in (job.get('review') or {}).get('findings', []):
        origin = finding.get('origin')
        if (origin == 'coverage' and not set(finding.get('source_ids', [])) & uncertain_sources
                or origin == 'editorial_alignment'
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
        sum(c.get('planned_status') == 'used' and c['status'] != 'used' for c in review.get('coverage', [])),
        int(not review.get('editorial_alignment', {}).get('matches_brief', True)),
        len(_local(job)),
        sum(f['severity'] == 'blocking' for f in review.get('findings', [])),
    )


def _acceptable(job, before, candidate, after):
    context = {**job, 'article': candidate}
    old, new = _score(job, before), _score(context, after)
    old_sources = {a['source_id']: a['status'] for a in before.get('source_coverage', {}).get('assessments', [])}
    new_sources = {a['source_id']: a['status'] for a in after.get('source_coverage', {}).get('assessments', [])}
    allowed = {'complete': {'complete'}, 'partial': {'partial', 'complete'},
               'missing': {'missing', 'partial', 'complete'}, 'uncertain': {'uncertain'},
               'excluded': {'excluded', 'complete'}}
    preserved = all(new_sources.get(ident) in allowed[status] for ident, status in old_sources.items())
    no_new_uncertainty = all(old_sources.get(ident) == 'uncertain' for ident, status in new_sources.items() if status == 'uncertain')
    old_used = {c['item_id'] for c in before.get('coverage', []) if c['status'] == 'used'}
    new_used = {c['item_id'] for c in after.get('coverage', []) if c['status'] == 'used'}
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
    return (preserved and no_new_uncertainty and old_used <= new_used and new_uncertain <= old_uncertain
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
        return previous
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
        if not cached:
            forecast['markdown'] += '\n\n' + 'x' * min(60000, max(len(original['markdown']), job['brief']['target_words'] * 12))
        review_material, _, _ = video_first.build_review_payload(job, forecast,
                                        previous_article=original, no_recovery=True)
        hold = (spending.amount(0) if cached_review else spending.text_request_bound(
                _request(job, 'fact_reviewer', video_first.VideoFidelityReview,
                    video_first.review_instruction(original), review_material, 'quality-review-forecast')))
        payload['_downstream_usd'] = float(hold)
        writer_bound = spending.text_request_bound(_request(job, 'writer', composition.DraftArticle,
                    REPAIR_INSTRUCTION, payload, slot)) if not cached else spending.amount(0)
        if writer_bound + hold > spending.amount(spending.summary(job)['remaining_usd']):
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
        exact_review = (spending.amount(0) if review_cached else spending.text_request_bound(
                       _request(job, 'fact_reviewer', video_first.VideoFidelityReview,
                       video_first.review_instruction(original), review_material, 'video-fidelity:' + candidate_hash)))
        if not review_cached and exact_review > spending.amount(spending.summary(job)['remaining_usd']):
            return _save(job, {**report, 'status': 'deferred', 'reason': 'candidate_review_budget',
                               'candidate_hash': candidate_hash})
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
        return _save(job, {**report, 'status': 'failed', 'reason': type(exc).__name__})
