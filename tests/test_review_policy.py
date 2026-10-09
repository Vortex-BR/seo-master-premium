from copy import deepcopy

import pytest

from app import db, generation
from app.editorial import changes, engine, review_policy, store


def start(job, body=None):
    if body is not None:
        job['article']['markdown'] = body
        db.save_job(job)
    engine.start(job, 'review')


def test_short_article_and_subjective_false_positive_preserve_content(job):
    body = 'Resposta breve e suficiente para a pergunta do leitor. [[v1s1]]'
    start(job, body)
    before = deepcopy(job['article'])
    review = {'article_hash': generation.article_hash(before), 'findings': [{
        'severity': 'blocking', 'passage': body, 'reason': 'O artigo deveria ter 1500 palavras.',
        'source_ids': [], 'origin': 'editorial_alignment', 'category': 'objective'}]}
    review_policy.annotate_review(job, review)
    finding = review['findings'][0]
    assert finding['category'] == 'recommendation'
    assert finding['export_blocking'] is False and finding['error_verified'] is False
    report = review_policy.repair_local(job)
    assert not report['changed'] and not report['changes']
    assert job['article'] == before and not db.revisions(job['id'])


def test_reviewers_source_id_and_literal_quote_do_not_prove_article_error(job):
    evidence = {'source_id': 'v1s1', 'excerpt': 'observa o desenvolvimento das folhas'}
    review = {'findings': [{'severity': 'blocking', 'passage': 'O autor observa o desenvolvimento das folhas do manjericão.',
                           'reason': 'Talvez seja necessário esclarecer a afirmação.', 'source_ids': ['v1s1'],
                           'origin': 'semantic_review', 'evidence': [evidence]}]}
    review_policy.annotate_review(job, review)
    finding = review['findings'][0]
    assert finding['category'] == 'factual_uncertainty'
    assert finding['verification']['literal_evidence_verified'] is True
    assert finding['verified_evidence'][0]['excerpt'] == evidence['excerpt']
    assert finding['error_verified'] is False and finding['auto_repair'] is False


def test_invented_evidence_and_passage_are_diagnostic_without_edit(job):
    before = deepcopy(job['article'])
    review = {'findings': [{'severity': 'blocking', 'passage': 'O procedimento precisa de 99 dias.',
                           'reason': 'Contradição alegada pelo revisor.', 'source_ids': ['v1s1'],
                           'origin': 'semantic_review', 'evidence': [{'source_id': 'v1s1', 'excerpt': 'dura 99 dias'}]}]}
    review_policy.annotate_review(job, review)
    finding = review['findings'][0]
    assert finding['false_positive_possible'] is True
    assert not finding['verified_evidence'] and finding['error_verified'] is False
    assert job['article'] == before


def test_only_exact_adjacent_duplicate_prose_is_removed_and_undo_preserves_version(job):
    paragraph = 'O autor observa o desenvolvimento das folhas do manjericão. [[v1s1]]'
    body = '## Observação\n\n' + paragraph + '\n\n' + paragraph + '\n\nUma ressalva exclusiva permanece aqui.'
    start(job, body)
    before = deepcopy(job['article'])
    report = review_policy.repair_local(job)
    assert report['changed'] is True and report['calls'] == 0
    assert job['article']['markdown'].count(paragraph) == 1
    assert 'Uma ressalva exclusiva permanece aqui.' in job['article']['markdown']
    assert generation.evidence_map(job)['v1s1']['text'] == paragraph.removesuffix(' [[v1s1]]')
    assert db.revisions(job['id'])[0]['data'] == before
    item = store.get_changes(job['id'], report['changes'][0]['id'])
    assert item['automatic_eligible'] and item['correction_proof']['verified']
    changes.decide(job, item, 'undo', generation.article_hash(job['article']))
    assert job['article'] == before


def test_similar_paragraphs_and_contextual_repetition_are_preserved(job):
    paragraph = 'O autor observa o desenvolvimento das folhas do manjericão. [[v1s1]]'
    body = '\n\n'.join(['## Observação', paragraph, paragraph.replace('folhas', 'novas folhas'),
                         '## Retomada', paragraph])
    start(job, body)
    report = review_policy.repair_local(job)
    assert report['changed'] is False
    assert job['article']['markdown'] == body


def test_local_serialization_fix_preserves_code_and_all_prose(job):
    body = '## Observação\n\nPrimeiro bloco.\\n\\nSegundo bloco. [[v1s1]]\n\n' + \
           'Use `\\n\\n` no exemplo.\n\n```text\nExemplo.\\n\\nConteúdo.\n```'
    start(job, body)
    report = review_policy.repair_local(job)
    assert report['changed']
    assert 'Primeiro bloco.\n\nSegundo bloco. [[v1s1]]' in job['article']['markdown']
    assert 'Use `\\n\\n` no exemplo.' in job['article']['markdown']
    assert 'Exemplo.\\n\\nConteúdo.' in job['article']['markdown']


def test_known_source_footnote_is_converted_and_unknown_note_is_retained(job):
    body = '## Observação\n\nO autor observa as folhas.[^v1s1] A ressalva não pode ser verificada.[^missing]'
    start(job, body)
    report = review_policy.repair_local(job)
    assert report['changed']
    assert 'O autor observa as folhas.[[v1s1]]' in job['article']['markdown']
    assert '[^missing]' in job['article']['markdown']
    assert generation.evidence_map(job)['v1s1']


def test_bounded_local_pass_is_cached_without_new_revisions(job):
    start(job, '## Observação\n\nPrimeiro bloco.\\n\\nSegundo bloco. [[v1s1]]')
    first = review_policy.repair_local(job)
    count = len(db.revisions(job['id']))
    assert first['max_rounds'] == 1 and first['changed']
    assert review_policy.repair_local(job) == first
    assert review_policy.repair_local(job, round_index=1) == first
    assert len(db.revisions(job['id'])) == count


def test_ai_proposal_cannot_auto_apply_from_reviewer_assertion_or_source_ids(job):
    start(job)
    before = deepcopy(job['article'])
    plan = {'summary': 'Preferência do revisor.', 'changes': [{
        'field': 'markdown', 'before': 'O relato é uma experiência pessoal.',
        'after': 'O relato comprova que esse método sempre funciona.', 'reason': 'O revisor considera melhor.',
        'source_ids': ['v1s1'], 'rule_ids': []}]}
    item = changes.propose(job, 'voice_editor', plan, 'unproven-change')
    assert item['status'] == 'pending' and not item['automatic_eligible']
    with pytest.raises(changes.EditConflict, match='não comprova'):
        changes.decide(job, item, 'apply', item['base_hash'], automatic=True)
    assert job['article'] == before and db.get_job(job['id'])['article'] == before
    assert not db.revisions(job['id'])


def test_stale_review_retains_diagnosis_without_certifying_old_defect(job):
    review = {'article_hash': 'older-version', 'findings': [{
        'severity': 'blocking', 'passage': 'missing', 'reason': 'Referência inexistente no material consultado.',
        'source_ids': [], 'origin': 'validation'}]}
    review_policy.annotate_review(job, review)
    assert review['findings'][0]['error_verified'] is False
    assert review['findings'][0]['false_positive_possible'] is True
    assert review['findings'][0]['export_blocking'] is False


def test_changed_upstream_context_skips_optional_correction_without_failing_review(job):
    body = '## Observação\n\nPrimeiro bloco.\\n\\nSegundo bloco. [[v1s1]]'
    start(job, body)
    job['article_needs_generation'] = True
    report = review_policy.repair_local(job)
    assert report['status'] == 'skipped' and report['reason'] == 'sources_or_direction_changed'
    assert job['article']['markdown'] == body and not db.revisions(job['id'])


def test_failed_optional_patch_restores_content_and_preserves_shared_processing_state(job, monkeypatch):
    body = '## Observação\n\nPrimeiro bloco.\\n\\nSegundo bloco. [[v1s1]]'
    start(job, body)
    shared_state = job['editorial']

    def fail(*args, **kwargs):
        job['article']['markdown'] = 'Uma alteração incompleta.'
        raise changes.EditConflict('A aplicação foi interrompida.')

    monkeypatch.setattr(changes, 'decide', fail)
    report = review_policy.repair_local(job)
    assert report['changes'][0]['status'] == 'deferred'
    assert job['article']['markdown'] == body
    assert job['editorial'] is shared_state
    assert not report['changed'] and not db.revisions(job['id'])
