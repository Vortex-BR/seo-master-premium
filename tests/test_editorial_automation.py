"""Regression coverage for autonomous, nonblocking editorial delivery."""
from copy import deepcopy

import httpx
import pytest
from openai import APIConnectionError

from app import db, generation, pipeline
from app.editorial import delivery, engine, store, workflow
from app.editorial.contracts import DraftArticle, EditorialPlan, VideoFidelityReview, VoiceProfile


def test_legacy_needs_review_exposes_independent_dimensions_without_migration(job):
    job['status'] = 'needs_review'
    job['review']['findings'] = [{'severity': 'blocking', 'origin': 'semantic_review',
        'passage': 'O autor observa', 'reason': 'Dúvida do revisor.', 'source_ids': ['v1s1']}]
    db.save_job(job)
    original = deepcopy(job)
    state = delivery.describe(job)
    assert state == {'processing_state': 'completed', 'editorial_state': 'uncertainties',
                     'delivery_state': 'article_available', 'export_available': True, 'export_error': None}
    assert job == original and db.get_job(job['id'])['status'] == 'needs_review'


def test_source_and_direction_changes_do_not_invalidate_saved_delivery(job):
    job.update(article_needs_generation=True, generation_complete=False, review=None)
    assert delivery.describe(job)['export_available']
    assert delivery.describe(job)['delivery_state'] == 'article_available'
    assert delivery.describe(job)['editorial_state'] == 'not_evaluated'


def test_optional_review_can_analyze_saved_version_after_direction_change(authed, job, monkeypatch):
    from unittest.mock import Mock
    from app import main
    job.update(article_needs_generation=True, review=None, generation_complete=False)
    db.save_job(job)
    queued = Mock()
    monkeypatch.setattr(pipeline, 'submit', queued)
    monkeypatch.setattr(main, 'get_secret', lambda name: 'test-only-key')
    response = authed.post(f'/api/jobs/{job["id"]}/review')
    assert response.status_code == 200
    queued.assert_called_once_with(job['id'], 'review')
    assert db.get_job(job['id'])['article'] == job['article']


def test_partial_draft_remains_available_during_review(job):
    job.update(status='reviewing', draft_delivery={'complete': False}, review=None)
    state = delivery.describe(job)
    assert state['export_available'] and state['delivery_state'] == 'partial_draft'
    assert state['editorial_state'] == 'analyzing'


@pytest.mark.parametrize('failure', ['connection', 'invalid', 'internal'])
def test_review_service_failure_delivers_saved_article_and_optional_diagnosis(job, newsroom_ai, failure):
    def respond(current, schema, instruction, stage, extra=None):
        if schema is VideoFidelityReview:
            if failure == 'connection':
                raise APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))
            if failure == 'invalid':
                raise generation.GenerationResponseError('invalid_output', generation.INVALID_RESPONSE_MESSAGE, retryable=True)
            raise RuntimeError('private provider details')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['generation_complete'] and saved['article']['markdown'].strip()
    assert saved['error'] is None
    assert saved['review']['review_incomplete']
    assert delivery.describe(saved)['export_available']
    assert delivery.describe(saved)['processing_state'] == 'completed'
    assert not any(f.get('export_blocking', True) for f in saved['review']['findings'])
    assert 'private provider details' not in str(saved['editorial']['review_failure'])
    assert newsroom_ai.call_count == (5 if failure in ('connection', 'invalid') else 4)


def test_local_diagnostic_failure_recovers_current_completed_factual_audit(job, newsroom_ai, monkeypatch):
    def unavailable(current):
        raise RuntimeError('diagnostic unavailable')
    monkeypatch.setattr(engine.checks, 'blocking_findings', unavailable)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['review']['review_incomplete']
    assert saved['review']['semantic_coverage']['assessed'] > 0
    assert saved['review']['supported_claims']
    assert delivery.describe(saved)['export_available']


def test_annotation_failure_does_not_repeat_a_fatal_diagnostic(job, newsroom_ai, monkeypatch):
    from app.editorial import review_policy
    def unavailable(*args):
        raise RuntimeError('private diagnostic details')
    monkeypatch.setattr(review_policy, 'annotate_review', unavailable)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['review']['review_incomplete'] and saved['error'] is None
    assert delivery.describe(saved)['processing_state'] == 'completed'
    assert delivery.describe(saved)['export_available']
    assert saved['review']['semantic_coverage']['assessed'] > 0
    assert all(f['export_blocking'] is False for f in saved['review']['findings'])
    assert saved['review']['policy']['diagnostics_incomplete']
    assert 'private diagnostic details' not in str(saved['editorial']['review_failure'])


def test_generate_automatically_writes_despite_legacy_auto_write_preference(job, newsroom_ai):
    db.set_setting('editorial_profile', VoiceProfile(auto_write=False, auto_apply=False).model_dump())
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['generation_complete'] and delivery.describe(saved)['export_available']
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['extractor', 'planner', 'writer', 'fact_reviewer']


def test_planner_uncertainty_writes_supported_scope_without_manual_approval(job, newsroom_ai):
    seen = []
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            output.update(ready_to_write=False, pending=['A imagem não foi analisada.'])
        if schema is DraftArticle:
            seen.append(extra['supported_writing_scope'])
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['plan']['data']['ready_to_write'] is False
    assert saved['generation_complete'] and delivery.describe(saved)['export_available']
    assert seen[0]['pending'] == ['A imagem não foi analisada.']
    assert seen[0]['planner_ready'] is False and seen[0]['item_ids']
    assert 'Não complete lacunas' in seen[0]['instruction']


def test_plan_without_supported_content_keeps_technical_source_error(job, newsroom_ai):
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            output.update(ready_to_write=False)
            for section in output['sections']:
                section['item_ids'] = []
            output['reader_journey']['video_item_ids'] = []
            for item in output['dispositions']:
                item.update(status='out_of_scope')
        return output
    newsroom_ai.side_effect = respond
    previous = deepcopy(job['article'])
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_input' and saved['article'] == previous
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['extractor', 'planner']
    assert delivery.describe(saved)['export_available']


def test_false_positive_preserves_concise_article_and_never_forces_rewrite(job, newsroom_ai):
    drafts = []
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is DraftArticle:
            drafts.append(deepcopy({key: value for key, value in output.items() if key != 'used_item_ids'}))
        if schema is VideoFidelityReview:
            output['editorial_alignment'] = {'matches_brief': False,
                'reason': 'O revisor prefere um texto mais longo.', 'passage': ''}
        return output
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['article'] == drafts[0]
    assert len(saved['article']['markdown'].split()) < 100
    assert newsroom_ai.call_count == 4
    assert delivery.describe(saved)['export_available']
    assert any(f['origin'] == 'editorial_alignment' and f['category'] == 'recommendation'
               for f in saved['review']['findings'])


def test_review_invalidation_preserves_old_analysis_in_immutable_history(job):
    previous = deepcopy(job['review'])
    store.invalidate(job, 'Texto editado pelo autor.')
    db.save_job(job)
    assert job['review'] is None and delivery.describe(job)['export_available']
    archived = store.artifacts(job['id'], 'review_history')
    assert len(archived) == 1 and archived[0]['data'] == previous
    assert job['review_history'][0]['version'] == archived[0]['version']
