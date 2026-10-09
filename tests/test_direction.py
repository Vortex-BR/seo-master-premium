from unittest.mock import Mock

import pytest

from app import db, generation, pipeline, wordpress
from app.schemas import EditorialDirection


def direction(job, **changes):
    return EditorialDirection.model_validate(job['brief']).model_dump() | changes


def test_direction_change_preserves_text_and_sources_but_invalidates_old_work(authed, job, monkeypatch):
    job.update(dossier={'summary': 'Old focus'}, research={'sources': []}, research_audit={'text': 'Old notes'},
               generation_complete=True, status='error', error='Old error')
    db.save_job(job)
    queued = Mock()
    monkeypatch.setattr(pipeline, 'submit', queued)
    response = authed.put('/api/jobs/test-job/brief', json=direction(job, topic='Como preparar café coado?'))
    assert response.status_code == 200
    saved = db.get_job(job['id'])
    assert saved['article'] == job['article']
    assert saved['sources'] == job['sources']
    assert saved['brief']['urls'] == job['brief']['urls']
    assert saved['brief_history'][0]['brief'] == job['brief']
    assert saved['brief']['topic'] == 'Como preparar café coado?'
    assert saved['status'] == 'brief_updated'
    assert saved['error'] is None
    assert saved['review'] is None
    assert saved['article_needs_generation']
    assert not saved['generation_complete']
    assert all(key not in saved for key in ('dossier', 'research', 'research_audit'))
    queued.assert_not_called()  # Saving direction never starts a paid generation.
    assert authed.post('/api/jobs/test-job/review').status_code == 400
    wordpress.ensure_reviewed(saved)
    assert authed.get('/api/jobs/test-job/export?format=markdown').status_code == 200


def test_unchanged_direction_keeps_completed_review(authed, job):
    response = authed.put('/api/jobs/test-job/brief', json=direction(job))
    assert response.json()['changed'] is False
    assert db.get_job(job['id']) == job


def test_manual_edit_does_not_silently_apply_a_changed_brief(authed, job):
    assert authed.put('/api/jobs/test-job/brief', json=direction(job, topic='Outra pergunta')).status_code == 200
    updated_article = {**job['article'], 'title': 'Título revisado manualmente'}
    assert authed.put('/api/jobs/test-job/article', json=updated_article).status_code == 200
    saved = db.get_job(job['id'])
    assert saved['article_needs_generation'] and not saved['generation_complete']


def test_direction_cannot_change_while_generation_is_running(authed, job):
    job['status'] = 'writing'
    db.save_job(job)
    assert authed.put('/api/jobs/test-job/brief', json=direction(job, topic='New topic')).status_code == 409
    assert db.get_job(job['id'])['brief'] == job['brief']


def test_direction_rejects_invalid_length_without_changing_job(authed, job):
    assert authed.put('/api/jobs/test-job/brief', json=direction(job, target_words=99999)).status_code == 422
    assert db.get_job(job['id']) == job


@pytest.mark.parametrize('current_version', [True, False])
def test_resume_uses_cached_work_only_from_current_editorial_version(job, monkeypatch, current_version):
    job.update(status='error', dossier={'summary': 'Saved'}, research={'sources': []}, generation_complete=True)
    if current_version:
        job['pipeline_editorial_version'] = generation.EDITORIAL_VERSION
    db.save_job(job)
    executor = Mock()
    monkeypatch.setattr(pipeline, 'executor', executor)
    pipeline.submit(job['id'])
    saved = db.get_job(job['id'])
    assert saved['sources'] == job['sources']
    assert saved['article'] == job['article']
    assert saved['pipeline_editorial_version'] == generation.EDITORIAL_VERSION
    assert saved['generation_complete'] is current_version
    assert ('dossier' in saved) is current_version
    executor.submit.assert_called_once_with(pipeline.run, job['id'], 'resume' if current_version else 'generate')


def test_regeneration_applies_saved_direction_and_keeps_previous_article(authed, job, monkeypatch, newsroom_ai):
    authed.put('/api/jobs/test-job/brief', json=direction(job, topic='Nova pergunta do leitor'))
    replacement = job['article'] | {'title': 'Nova pergunta do leitor'}
    from app.editorial import composition
    def write(current, plan, used):
        assert current['brief']['topic'] == 'Nova pergunta do leitor'
        assert current['sources'] == job['sources']
        return replacement
    monkeypatch.setattr(composition, 'write', write)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['article'] == replacement
    assert not saved['article_needs_generation']
    assert saved['article_editorial_version'] == generation.EDITORIAL_VERSION
    assert db.revisions(job['id'])[0]['data'] == job['article']
    assert not authed.get('/api/jobs/test-job').json()['previous_editorial_version']


def test_old_article_is_identified_without_altering_its_content(authed, job):
    response = authed.get('/api/jobs/test-job').json()
    assert response['previous_editorial_version']
    assert response['article'] == job['article']
    assert db.get_job(job['id']) == job


def test_review_must_quote_the_evaluated_article_not_only_its_sources(job):
    review = {'evaluated_title': job['article']['title'], 'editorial_alignment': {'matches_brief': True, 'passage': ''},
              'findings': [], 'supported_claims': [{'statement': 'O relato é uma experiência pessoal.'}]}
    assert generation.review_integrity_findings(job, review) == []
    review['supported_claims'][0]['statement'] = 'Uma frase que está apenas na fonte.'
    job['sources'][0]['segments'][0]['text'] += ' Uma frase que está apenas na fonte.'
    findings = generation.review_integrity_findings(job, review)
    assert len(findings) == 1
    assert findings[0]['severity'] == 'blocking'
    assert findings[0]['origin'] == 'model_evidence'


def test_review_of_wrong_article_cannot_pass_silently(job):
    review = {'evaluated_title': 'Outro artigo', 'editorial_alignment': {'matches_brief': False, 'passage': 'Texto inexistente'},
              'findings': [{'passage': 'Texto inexistente'}], 'supported_claims': []}
    findings = generation.review_integrity_findings(job, review)
    assert len(findings) == 2  # Wrong title and a deduplicated nonliteral passage.
    assert all(f['severity'] == 'blocking' for f in findings)
