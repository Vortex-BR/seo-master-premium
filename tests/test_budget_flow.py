"""Core deliveries reuse paid work and never launch new article research."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import research, workflow
from app.editorial.contracts import (DraftArticle, EditorialPlan, SpokenExtraction,
                                    VideoFidelityReview, VoiceProfile)


def test_operational_reservation_does_not_repay_cached_delivery(job):
    from app.editorial import engine
    engine.start(job, 'generate')
    delivery = Mock(return_value={'summary': 'Entrega preservada.'})
    first = engine.invoke(job, 'extractor', {'topic': 'Organização'}, delivery, 'saved-delivery')
    repeated = engine.invoke(job, 'extractor', {'topic': 'Organização', '_budget_reserve': 3},
                             delivery, 'saved-delivery')
    assert first == repeated
    assert delivery.call_count == job['editorial']['calls'] == 1


def _sources(job, count=5):
    template = deepcopy(job['sources'][0])
    job['sources'] = []
    for number in range(1, count + 1):
        source = deepcopy(template)
        source.update(id=f'v{number}', title=f'Organização de arquivos {number}',
                      author=f'Criador {number}', video_id=f'video{number:06d}',
                      url=f'https://www.youtube.com/watch?v=video{number:06d}')
        source['segments'] = [{'id': f'v{number}s1', 'start': 10, 'end': 20,
                               'text': f'O criador {number} organiza os arquivos por projeto '
                                       'e mantém cada versão na mesma pasta.'}]
        job['sources'].append(source)
    job['brief']['urls'] = [source['url'] for source in job['sources']]
    job['brief']['research'] = True
    db.save_job(job)


@pytest.mark.parametrize('cap', [4, 8, 24])
def test_small_safety_cap_uses_four_core_calls_including_complete_review(job, newsroom_ai, monkeypatch, cap):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=cap).model_dump())
    _sources(job)
    search = Mock(side_effect=AssertionError('Optional research must not consume core delivery calls.'))
    monkeypatch.setattr(generation, 'research', search)

    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert saved['status'] == 'ready', saved.get('error')
    assert saved['editorial']['calls'] == newsroom_ai.call_count == 4
    assert saved['research']['status'] == 'skipped'
    assert 'Novas pesquisas estão desativadas' in saved['research']['notice']
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert not saved['draft_delivery']['review_pending']
    assert {item['video_id'] for item in saved['apuration']['items']} == {f'v{n}' for n in range(1, 6)}
    search.assert_not_called()


def test_eight_call_cap_can_recover_each_core_delivery_with_research_requested(job, newsroom_ai, monkeypatch):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=8).model_dump())
    _sources(job)
    failed_once = set()
    search = Mock(side_effect=AssertionError('There is no slack for background research.'))
    monkeypatch.setattr(generation, 'research', search)

    def respond(current, schema, instruction, stage, extra=None):
        if schema not in failed_once:
            failed_once.add(schema)
            raise generation.GenerationResponseError('invalid_output', 'Formato incompleto.', retryable=True)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert failed_once == {SpokenExtraction, EditorialPlan, DraftArticle, VideoFidelityReview}
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['editorial']['calls'] == newsroom_ai.call_count == 8
    assert len(saved['editorial']['response_recoveries']) == 4
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert saved['coverage']['items'] and all(item['status'] == 'used' for item in saved['coverage']['items'])
    search.assert_not_called()


def test_reserves_travel_through_core_recovery_requests(job, newsroom_ai):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=8).model_dump())
    _sources(job, count=1)
    failures = set()
    received = []

    def respond(current, schema, instruction, stage, extra=None):
        received.append((stage, extra.get('_budget_reserve', 0)))
        if schema not in failures:
            failures.add(schema)
            raise generation.GenerationResponseError('missing_output', 'Resposta vazia.', retryable=True)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])

    assert db.get_job(job['id'])['status'] == 'ready'
    assert received == [('extractor', 3), ('extractor', 3), ('planner', 2), ('planner', 2),
                        ('writer', 1), ('writer', 1), ('fact_reviewer', 0), ('fact_reviewer', 0)]


def test_legacy_research_flag_never_launches_search_or_web_extraction(job, newsroom_ai, monkeypatch):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=24).model_dump())
    _sources(job, count=1)
    search = Mock(side_effect=AssertionError('New research is disabled even with a large budget.'))
    fetch = Mock(side_effect=AssertionError('No web page may be fetched.'))
    monkeypatch.setattr(research, 'run', search)
    monkeypatch.setattr(research, 'page_text', fetch)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert saved['status'] == 'ready', saved.get('error')
    search.assert_not_called()
    fetch.assert_not_called()
    assert saved['editorial']['calls'] == 4
    assert saved['research']['status'] == 'skipped'
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    # A new plan request must not reactivate the persisted legacy flag either.
    pipeline.run(job['id'], 'plan')
    search.assert_not_called()
    fetch.assert_not_called()


def test_completed_optional_context_is_not_erased_when_recovery_slack_is_low(job, newsroom_ai):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=8).model_dump())
    _sources(job, count=1)
    original = {'internal_context_only': True, 'status': 'completed',
                'text': 'Um registro interno já salvo.', 'sources': [], 'pages': [],
                'agent_background_knowledge': {'internal_context_only': True, 'terms': []}}
    job['research'] = deepcopy(original)
    db.save_job(job)

    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert saved['status'] == 'ready'
    assert saved['research'] == original
    assert saved['editorial']['calls'] == 4


def test_legacy_eight_call_incident_resumes_only_missing_fidelity_review(job, newsroom_ai):
    _sources(job, count=1)

    def interrupted(current, schema, instruction, stage, extra=None):
        if schema is VideoFidelityReview:
            raise RuntimeError('Review worker interrupted after the paid draft checkpoint.')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = interrupted
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['draft_delivery']['complete'] and saved['draft_delivery']['review_pending']
    original_article = deepcopy(saved['article'])
    cycle = saved['editorial']['cycle_id']
    saved['editorial']['calls'] = 8
    saved['editorial']['profile']['profile']['max_calls'] = 8
    saved['editorial']['profile']['profile'].pop('max_spend_usd', None)
    db.save_job(saved)
    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = newsroom_ai.respond

    pipeline.run(job['id'], 'resume')
    resumed = db.get_job(job['id'])

    assert resumed['status'] == 'ready', resumed.get('error')
    assert resumed['article'] == original_article
    assert resumed['editorial']['cycle_id'] == cycle
    assert resumed['editorial']['profile']['profile']['max_calls'] == 24
    assert resumed['editorial']['calls'] == 9
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['fact_reviewer']
    assert not resumed['draft_delivery']['review_pending']


def test_review_monetary_denial_preserves_current_draft_without_factual_approval(job, newsroom_ai):
    from app.spending import SpendLimitExceeded
    _sources(job, count=1)
    drafted = []

    def refused(current, schema, instruction, stage, extra=None):
        if schema is VideoFidelityReview:
            drafted.append(deepcopy(current['article']))
            raise SpendLimitExceeded('A conferência não cabe no saldo conservador deste artigo.')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = refused
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert saved['status'] == 'needs_review', saved.get('error')
    assert saved['error'] is None and len(drafted) == 1
    assert saved['article'] == drafted[0]
    assert saved['draft_delivery']['complete'] and saved['draft_delivery']['review_pending']
    assert saved['review']['review_incomplete'] is True
    assert saved['review']['supported_claims'] == []
    assert any(f['severity'] == 'blocking' and f['code'] == 'review_pending'
               for f in saved['review']['findings'])
    assert saved['editorial']['decision']['decision'] == 'revise'
    assert newsroom_ai.call_count == 4


def test_monetary_denial_before_new_draft_does_not_relabel_previous_article(job, newsroom_ai):
    from app.spending import SpendLimitExceeded
    _sources(job, count=1)
    previous_article = deepcopy(job['article'])

    def refused(current, schema, instruction, stage, extra=None):
        if schema is EditorialPlan:
            raise SpendLimitExceeded('Não há saldo para iniciar uma nova entrega neste artigo.')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = refused
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])

    assert saved['status'] == 'budget_exhausted'
    assert saved['article'] == previous_article
    assert saved['review'] is None
    assert not saved.get('draft_delivery')
    assert not saved['editorial'].get('draft_installed')
    assert not saved['editorial'].get('initial_complete')
    assert newsroom_ai.call_count == 2
