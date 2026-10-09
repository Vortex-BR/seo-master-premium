from copy import deepcopy
import json

import pytest

from app import db, generation, pipeline
from app.editorial import composition, engine, store, workflow
from app.editorial.contracts import DraftArticle, VideoFidelityReview, VoiceProfile
from conftest import real_structured
from test_evidence_workflow import prepare, set_sources
from test_response_recovery import provider, response


def ready(job, newsroom_ai, monkeypatch):
    saved = prepare(job, newsroom_ai)
    assert saved['editorial']['video_first']
    monkeypatch.setattr(generation, 'structured', real_structured)
    return saved


def wire(job, used=True, long=False):
    text = ('Texto com exemplos do registro e explicação de suas condições. ' * 110 if long else
            'A observação descreve o exemplar e suas condições específicas, preservando o contexto e o método usado no registro.')
    return response(json.dumps({**{k: v for k, v in job['article'].items() if k != 'markdown'},
        'paragraphs': [{'markdown': '## O que observar', 'source_ids': []},
                       {'markdown': text, 'source_ids': ['v1s1']}],
        'usage': {item['id']: used for item in job['apuration']['items']}}))


def test_new_cycles_always_use_video_first_and_resume_preserves_same_cycle(job, monkeypatch):
    monkeypatch.setenv('EDITORIAL_COMPOSITION', 'legacy')
    monkeypatch.setenv('EDITORIAL_FLOW', 'legacy')
    engine.start(job, 'generate')
    assert job['editorial']['composition_version'] == 1 and job['editorial']['video_first']
    previous = deepcopy(job['editorial'])
    engine.start(job, 'resume')
    assert job['editorial']['cycle_id'] == previous['cycle_id']
    assert job['editorial']['composition_version'] == 1 and job['editorial']['video_first']


def test_four_call_budget_delivers_five_videos_without_block_multiplier(job, newsroom_ai):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=4).model_dump())
    set_sources(job, count=5)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['editorial']['calls'] == newsroom_ai.call_count == 4
    assert {item['video_id'] for item in saved['apuration']['items']} == {f'v{n}' for n in range(1, 6)}
    assert all(item['status'] == 'used' for item in saved['review']['coverage'])


def test_sdk_complete_delivery_includes_citations_metadata_and_reuses_paid_draft(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved)])
    calls = saved['editorial']['calls']
    article = workflow.write(saved)
    assert '[[v1s1]]' in article['markdown']
    assert article['title'] and article['slug']
    assert saved['editorial']['calls'] == calls + 1
    assert workflow.write(saved) == article and len(requests) == 1
    payload = json.loads(requests[0]['input'])
    assert payload['target_words_total'] == 800
    assert payload['article_route']['main_question'] == saved['plan']['data']['main_question']
    assert payload['creator_voice'][0]['author'] == 'Autor de exemplo'
    assert len(store.artifacts(saved['id'], 'composition_draft')) == 1
    assert not store.artifacts(saved['id'], 'draft_coverage')[0]['data']['missing_item_ids']


def test_overrun_is_saved_with_local_checks_without_paid_repair(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, long=True)])
    article = workflow.write(saved)
    assert 'Texto com exemplos' in article['markdown']
    coverage = store.artifacts(saved['id'], 'draft_coverage')[0]['data']
    assert coverage['delivery_checks']['word_count'] > 800
    assert coverage['delivery_checks']['findings']
    assert not store.artifacts(saved['id'], 'composition_repair')
    assert workflow.write(saved) == article and len(requests) == 1


def test_missing_coverage_remains_explicit_without_paid_repair(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, used=False)])
    article = workflow.write(saved)
    coverage = store.artifacts(saved['id'], 'draft_coverage')[0]['data']
    assert coverage['missing_item_ids'] == [item['id'] for item in saved['apuration']['items']]
    assert coverage['used_item_ids'] == []
    assert not store.artifacts(saved['id'], 'composition_repair')
    assert workflow.write(saved) == article and len(requests) == 1


def test_complete_paid_draft_is_visible_before_factual_review(job, newsroom_ai, monkeypatch, authed):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, used=False)])
    article = workflow.write(saved)
    checkpoint = db.get_job(job['id'])
    assert checkpoint['article'] == article
    assert checkpoint['draft_delivery']['complete'] and checkpoint['draft_delivery']['review_pending']
    assert checkpoint['review'] is None
    assert authed.get(f'/api/jobs/{job["id"]}/preview').status_code == 200
    assert article['markdown'] in authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').text
    assert len(requests) == 1


def test_invalid_delivery_has_one_format_recovery_without_editorial_repair(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [response('{invalid-json'), wire(saved)])
    article = workflow.write(saved)
    assert article['markdown'] and len(requests) == 2
    assert not store.artifacts(saved['id'], 'composition_repair')
    assert workflow.write(saved) == article and len(requests) == 2


def test_oversized_composition_refuses_before_provider_and_section_fallback(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    saved['brief']['instructions'] = 'Material muito extenso. ' * 15000
    requests = provider(monkeypatch, [])
    calls = saved['editorial']['calls']
    with pytest.raises(generation.ContextLimitExceeded):
        workflow.write(saved)
    assert not requests and saved['editorial']['calls'] == calls
    assert not store.artifacts(saved['id'], 'draft_section')


def test_full_coordinator_uses_four_roles_and_one_independent_global_fidelity_review(job, newsroom_ai):
    reviewed = []

    def respond(current, schema, instruction, stage, extra=None):
        if schema is DraftArticle:
            assert extra['target_words_total'] == current['brief']['target_words']
        if schema is VideoFidelityReview:
            assert extra['passages'] == workflow.passages(current['article'])
            assert all('check' not in item for item in extra['items'])
            reviewed.append(extra)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)

    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert saved['review']['semantic_coverage']['batches'] == len(reviewed) == 1
    assert saved['editorial']['calls'] == 4
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['extractor', 'planner', 'writer', 'fact_reviewer']


def test_last_available_call_is_reserved_for_review_and_resume_never_exceeds_cap(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    cap = saved['editorial']['profile']['profile']['max_calls']
    saved['editorial']['calls'] = cap - 1
    saved.pop('article', None)
    db.save_job(saved)

    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = AssertionError('A new writer request must leave one factual-review call.')
    pipeline.run(job['id'], 'write')
    stopped = db.get_job(job['id'])
    assert stopped['status'] == 'budget_exhausted', stopped.get('error')
    assert stopped['editorial']['calls'] == cap - 1
    assert not stopped.get('draft_delivery') and not stopped.get('article')
    assert stopped['plan']['valid'] and stopped['apuration']['valid']
    pipeline.run(job['id'], 'resume')
    assert newsroom_ai.call_count == 0 and db.get_job(job['id'])['editorial']['calls'] == cap - 1


def test_writer_draft_survives_fidelity_worker_failure_and_resume_only_reviews(job, newsroom_ai, authed):
    prepare(job, newsroom_ai)
    original_respond = newsroom_ai.respond

    def interrupted(current, schema, instruction, stage, extra=None):
        if schema is VideoFidelityReview:
            checkpoint = db.get_job(job['id'])
            assert checkpoint['draft_delivery']['complete'] and checkpoint['draft_delivery']['review_pending']
            raise RuntimeError('worker interrupted during fidelity review')
        return original_respond(current, schema, instruction, stage, extra)

    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = interrupted
    pipeline.run(job['id'], 'write')
    stopped = db.get_job(job['id'])
    assert stopped['status'] == 'error'
    assert stopped['article']['markdown']
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=markdown').status_code == 200
    writer_calls = sum(call.args[1] is DraftArticle for call in newsroom_ai.call_args_list)
    newsroom_ai.side_effect = original_respond
    pipeline.run(job['id'], 'resume')
    assert db.get_job(job['id'])['status'] == 'ready'
    assert sum(call.args[1] is DraftArticle for call in newsroom_ai.call_args_list) == writer_calls == 1


def test_saved_complete_draft_can_be_manually_edited_without_paid_calls(job, newsroom_ai, monkeypatch, authed):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved)])
    workflow.write(saved)
    current = db.get_job(job['id'])
    pipeline.step(current, 'error', 'Conferência interrompida; rascunho preservado.')
    current['article']['markdown'] = 'Texto curto revisado pelo usuário.'
    assert authed.put(f'/api/jobs/{job["id"]}/article', json=current['article']).status_code == 200
    edited = db.get_job(job['id'])
    assert edited['editorial']['stale'] and edited['generation_complete']
    assert not edited.get('draft_delivery') and len(requests) == 1


def test_qualification_payload_preserves_material_limits(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    item = saved['apuration']['items'][0]
    item.update(conditions=['Depois de copiar.'], restrictions=['A amostra não garante integridade total.'],
                limitations=['A verificação cobre apenas arquivos abertos.'])
    requests = provider(monkeypatch, [wire(saved)])
    workflow.write(saved)
    payload = json.loads(requests[0]['input'])
    assert payload['required_qualifications'][0] == {'item_id': item['id'],
        'conditions': item['conditions'], 'restrictions': item['restrictions'], 'limitations': item['limitations']}
    assert 'target_words_total' in payload
