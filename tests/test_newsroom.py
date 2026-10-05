from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import agents, changes, engine, store
from app.editorial.contracts import VoiceProfile
from app.seo import knowledge


def change(job, field='seo_title', before=None, after='Um título mais claro para a horta'):
    engine.start(job, 'optimize')
    plan = {'summary': 'Título mais claro.', 'findings': [], 'changes': [
        {'field': field, 'before': job['article'][field] if before is None else before, 'after': after,
         'reason': 'Clareza para o leitor.', 'source_ids': [], 'rule_ids': ['google.title']}]}
    return changes.propose(job, 'seo_editor', plan, store.new_id())


def test_full_cycle_runs_all_twelve_roles_with_shared_profile(job, newsroom_ai):
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    report = store.report(saved)
    assert {r['role'] for r in report['runs']} == set(agents.ROLES)
    assert len(report['runs']) == 12
    assert saved['editorial']['calls'] == 12
    assert len({r['data']['profile_version'] for r in report['runs']}) == 1
    assert all(r['data']['rule_ids'] for r in report['runs'])
    assert len(report['messages']) >= 12
    assert saved['review']['article_hash'] == generation.article_hash(saved['article'])


def test_source_check_feedback_is_delivered_to_planner(job, newsroom_ai):
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if stage == 'source_checker':
            output['summary'] = 'Preserve a ressalva sobre observação individual.'
        if stage == 'planner':
            assert extra['source_review']['summary'].startswith('Preserve a ressalva')
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    assert db.get_job(job['id'])['status'] == 'ready'


def test_manual_apply_and_undo_invalidate_review_and_preserve_history(authed, job):
    item = change(job)
    before = deepcopy(job['article'])
    response = authed.post(f'/api/jobs/{job["id"]}/changes/{item["id"]}', json={
        'article_hash': item['base_hash'], 'action': 'apply'})
    assert response.status_code == 200
    saved = db.get_job(job['id'])
    assert saved['article']['seo_title'] != before['seo_title']
    assert saved['review'] is None
    assert saved['editorial']['stale']
    assert db.revisions(job['id'])[0]['data'] == before
    response = authed.post(f'/api/jobs/{job["id"]}/changes/{item["id"]}', json={
        'article_hash': item['result_hash'], 'action': 'undo'})
    assert response.status_code == 200
    assert db.get_job(job['id'])['article'] == before
    assert store.get_changes(job['id'], item['id'])['status'] == 'undone'


def test_stale_proposal_never_overwrites_manual_edit(authed, job):
    item = change(job)
    edited = job['article'] | {'title': 'Um título escolhido pelo usuário'}
    authed.put(f'/api/jobs/{job["id"]}/article', json=edited)
    response = authed.post(f'/api/jobs/{job["id"]}/changes/{item["id"]}', json={
        'article_hash': item['base_hash'], 'action': 'apply'})
    assert response.status_code == 409
    assert db.get_job(job['id'])['article'] == edited
    assert db.get_job(job['id'])['editorial']['stale']


def test_changed_brief_rejects_proposal_even_when_article_is_identical(job):
    item = change(job)
    job['brief']['topic'] = 'Outra pauta'
    db.save_job(job)
    with pytest.raises(ValueError, match='fontes ou a direção'):
        changes.decide(job, item, 'apply', item['base_hash'])
    assert db.get_job(job['id'])['article'] == job['article']


@pytest.mark.parametrize('before,after', [('texto ausente','novo texto'), ('O ','novo texto'), ('[[v1s1]]','[[invented]]')])
def test_invalid_changes_preserve_article(job, before, after):
    item = change(job, 'markdown', before, after)
    assert item['status'] == 'invalid'
    assert db.get_job(job['id'])['article'] == job['article']


def test_profile_is_versioned_and_midcycle_changes_do_not_change_context(authed, job):
    engine.start(job, 'generate')
    original = deepcopy(job['editorial']['profile'])
    profile = authed.get('/api/editorial/profile').json()
    profile['profile']['tone'] = 'Direto e acolhedor'
    response = authed.put('/api/editorial/profile', json={'base_version': profile['version'], 'profile': profile['profile']})
    assert response.status_code == 200
    assert response.json()['version'] != original['version']
    assert job['editorial']['profile'] == original
    assert authed.put('/api/editorial/profile', json={'base_version': profile['version'], 'profile': profile['profile']}).status_code == 409


def test_knowledge_retrieval_and_local_checks_are_not_fake_yoast_scores(authed, job):
    bundle = authed.get('/api/knowledge').json()
    assert bundle['version']
    rules = authed.get('/api/knowledge/search', params={'q': 'metadescrição'}).json()['rules']
    assert any(r['id'] == 'google.snippet' for r in rules)
    checks = generation.seo_checks(job)
    assert any(c['status'] == 'unknown' for c in checks)
    assert not any('30 e 65' in c['label'] for c in checks)
    assert all('score' not in c for c in checks)


def test_invalid_document_reference_stops_agent_without_applying_changes(job, newsroom_ai):
    engine.start(job, 'optimize')
    docs = knowledge.retrieve('seo')
    result = {'findings': [{'rule_ids': ['invented'], 'source_ids': []}]}
    with pytest.raises(ValueError, match='regra ausente'):
        engine.validate_output(job, 'yoast_analyst', result, docs, {'article': job['article']})


def test_provider_schema_constrains_review_quotes_and_rule_ids(job):
    from app.editorial.contracts import Audit, EditPlan
    scope = {'article_passages': engine.article_passages(job['article']),
             'edit_blocks': {'b1': {'field': 'title', 'text': job['article']['title']}}, 'knowledge': knowledge.retrieve('seo')}
    token = generation.agent_scope.set(scope)
    try:
        for schema in (Audit, EditPlan):
            schema_json = generation.scoped_schema(schema, generation.evidence_map(job)).model_json_schema()
            observation = schema_json['$defs']['ScopedObservation']['properties']
            assert 'Texto inventado' not in observation['passage']['enum']
            assert job['article']['title'] in observation['passage']['enum']
            assert 'google.title' in observation['rule_ids']['items']['enum']
    finally:
        generation.agent_scope.reset(token)


def test_factual_schema_binds_excerpts_to_the_correct_source(job):
    from app.schemas import Review
    scope = {'article_passages': engine.article_passages(job['article']), 'article_title': job['article']['title'],
             'source_excerpts_by_id': {'v1s1': ['O autor observa as folhas.'], 'v1s2': ['Outro trecho da segunda fonte.']}}
    token = generation.agent_scope.set(scope)
    try:
        schema = generation.scoped_schema(Review, ['v1s1', 'v1s2'])
        sample = {'evaluated_title': job['article']['title'], 'editorial_alignment': {
            'matches_brief': True, 'reason': 'Atende.', 'passage': ''}, 'summary': 'Conferido.', 'findings': [],
            'supported_claims': [{'statement': job['article']['title'], 'kind': 'fato',
                'evidence': [{'source_id': 'v1s1', 'excerpt': 'Outro trecho da segunda fonte.'}]}]}
        with pytest.raises(ValueError):
            schema.model_validate(sample)
        sample['supported_claims'][0]['evidence'][0]['excerpt'] = 'O autor observa as folhas.'
        schema.model_validate(sample)
    finally:
        generation.agent_scope.reset(token)


def test_retry_invalid_delivery_once_then_stop(job, newsroom_ai):
    from app.editorial.contracts import Audit
    def respond(current, schema, instruction, stage, extra=None):
        if schema is Audit:
            return {'summary': 'Parecer inválido.', 'findings': [{'severity': 'warning', 'passage': 'Uma frase inventada',
                'reason': 'Clareza', 'suggestion': 'Ajustar', 'source_ids': [], 'rule_ids': [], 'recipient': 'writing'}]}
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    engine.start(job, 'optimize')
    with pytest.raises(ValueError, match='trecho que não está'):
        engine.invoke(job, 'reader', {'article': job['article']})
    assert newsroom_ai.call_count == 2
    assert job['editorial']['calls'] == 2


def test_changes_cannot_introduce_numbers_missing_from_sources(job):
    item = change(job, 'markdown', after=job['article']['markdown'] + '\n\nEspere entre 2 e 4 minutos. [[v1s1]]')
    assert item['status'] == 'invalid'
    assert 'números ausentes' in item['error']


def test_changes_cannot_duplicate_sentences(job):
    item = change(job, 'markdown', after=job['article']['markdown'] + '\n\nO autor observa o desenvolvimento das folhas do manjericão. [[v1s1]]')
    assert item['status'] == 'invalid'
    assert 'duplicou' in item['error']


def test_correction_round_rechecks_changed_text_and_survives_restart(job, newsroom_ai, monkeypatch):
    from app.editorial.contracts import EditorialDecision
    from app.schemas import Review
    problem = {'severity': 'blocking', 'passage': 'O relato é uma experiência pessoal.',
               'reason': 'Falta clareza na atribuição.', 'suggestion': 'Esclareça a origem.', 'source_ids': ['v1s1']}
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        first = current['editorial']['round'] == 0
        if schema is Review and first:
            result['findings'] = [problem]
        if schema is EditorialDecision and first:
            result['decision'] = 'revise'
        if stage == 'voice_editor' and not first:
            result['changes'] = [{'field': 'markdown', 'before': problem['passage'],
                'after': 'A observação das folhas vem da fonte citada. [[v1s1]]', 'reason': 'Atribuição clara.',
                'source_ids': ['v1s1'], 'rule_ids': ['brand.evidence']}]
        return result
    newsroom_ai.side_effect = respond
    original = engine.seo_team
    def interrupted(current, round_index):
        if round_index == 1:
            raise ValueError('reinício após correção')
        return original(current, round_index)
    monkeypatch.setattr(engine, 'seo_team', interrupted)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'error'
    assert problem['passage'] not in saved['article']['markdown']
    monkeypatch.setattr(engine, 'seo_team', original)
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['review']['article_hash'] == generation.article_hash(saved['article'])
    assert not generation.unresolved_findings(saved)
    assert saved['editorial']['review_round'] == 1


def test_final_reviewer_cannot_waive_invalid_citation(job, newsroom_ai):
    job['article']['markdown'] += ' [[invalid]]'
    db.save_job(job)
    pipeline.run(job['id'], 'review')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review'
    assert any(f.get('origin') == 'validation' for f in saved['review']['findings'])
    assert saved['editorial']['calls'] == 3


def test_proposal_only_mode_keeps_article_and_final_review_on_same_version(job, newsroom_ai):
    db.set_setting('editorial_profile', VoiceProfile(auto_apply=False).model_dump())
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if stage == 'seo_editor':
            result['changes'] = [{'field': 'seo_title', 'before': current['article']['seo_title'],
                'after': 'Um título proposto para revisão', 'reason': 'Clareza.', 'rule_ids': ['google.title'], 'source_ids': []}]
        return result
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'optimize')
    saved = db.get_job(job['id'])
    assert saved['article'] == job['article']
    assert any(c['status'] == 'pending' for c in store.report(saved)['changes'])
    assert saved['review']['article_hash'] == generation.article_hash(job['article'])


def test_budget_exhaustion_stops_calls_and_preserves_work(job, newsroom_ai):
    engine.start(job, 'optimize')
    job['editorial']['calls'] = 24
    with pytest.raises(ValueError, match='número de chamadas'):
        engine.invoke(job, 'reader', {'article': job['article']})
    newsroom_ai.assert_not_called()


def test_new_endpoints_require_authentication(client):
    for path in ['/api/knowledge', '/api/editorial/profile', '/api/jobs/nope/team']:
        assert client.get(path).status_code == 401


def test_editing_blocked_during_seo_stage(authed, job):
    job['status'] = 'optimizing'
    db.save_job(job)
    assert authed.put(f'/api/jobs/{job["id"]}/article', json=job['article']).status_code == 409


def test_restart_after_applied_change_does_not_apply_twice(job, newsroom_ai, monkeypatch):
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if stage == 'voice_editor':
            result['changes'] = [{'field': 'seo_title', 'before': current['article']['seo_title'],
                'after': 'Uma nova observação de horta', 'reason': 'Clareza.', 'rule_ids': ['google.title'], 'source_ids': []}]
        return result
    newsroom_ai.side_effect = respond
    original = engine.seo_team
    monkeypatch.setattr(engine, 'seo_team', Mock(side_effect=ValueError('restart')))
    pipeline.run(job['id'], 'optimize')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'error'
    assert saved['article']['seo_title'] == 'Uma nova observação de horta'
    revisions = len(db.revisions(job['id']))
    monkeypatch.setattr(engine, 'seo_team', original)
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert len(db.revisions(job['id'])) == revisions
    assert saved['editorial']['calls'] == 8
