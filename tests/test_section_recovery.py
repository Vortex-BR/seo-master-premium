"""Recover complete paid drafts; active video-first composition never splits sections."""
import json
from copy import deepcopy

import pytest

from app import db, generation
from app.editorial import drafts, engine, store, workflow
from app.editorial.contracts import VideoFidelityReview
from test_composition import ready, wire
from test_evidence_workflow import prepare
from test_response_recovery import provider, response


def test_repeated_plan_item_is_composed_once_in_one_complete_sdk_delivery(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    plan = saved['plan']['data']
    first = plan['sections'][0]
    plan['sections'].append({**first, 'id': 's2', 'title': 'Outro contexto da mesma observação'})
    requests = provider(monkeypatch, [wire(saved)])
    article = workflow.write(saved)
    material = json.loads(requests[0]['input'])
    assert len(material['article_route']['sections']) == 2
    assert len(material['items']) == 1
    assert article['markdown'].count('A observação descreve') == 1
    assert len(requests) == 1 and not store.artifacts(saved['id'], 'draft_section')


def test_missing_content_remains_pending_without_section_repair_or_resume_recharge(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved, used=False)])
    article = workflow.write(saved)
    record = store.artifacts(saved['id'], 'draft_coverage')[0]['data']
    assert record['used_item_ids'] == []
    assert record['missing_item_ids'] == [item['id'] for item in saved['apuration']['items']]
    assert workflow.write(saved) == article and len(requests) == 1
    assert not store.artifacts(saved['id'], 'draft_section_repair')
    assert not store.artifacts(saved['id'], 'composition_repair')


def test_interrupted_checkpoint_reuses_paid_writer_delivery_after_reload(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved)])
    original = drafts.checkpoint

    def interrupted(current, article, **kwargs):
        original(current, article, **kwargs)
        raise RuntimeError('worker stopped after paid draft checkpoint')

    monkeypatch.setattr(drafts, 'checkpoint', interrupted)
    with pytest.raises(RuntimeError):
        workflow.write(saved)
    checkpoint = db.get_job(saved['id'])
    paid_calls = checkpoint['editorial']['calls']
    assert checkpoint['draft_delivery']['complete'] and checkpoint['draft_delivery']['review_pending']
    monkeypatch.setattr(drafts, 'checkpoint', original)
    article = workflow.write(checkpoint)
    assert article == checkpoint['article']
    assert checkpoint['editorial']['calls'] == paid_calls and len(requests) == 1
    assert len(store.artifacts(saved['id'], 'composition_draft')) == 1


def test_saved_writer_delivery_reused_at_budget_cap_for_identical_plan(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved)])
    first = workflow.write(saved)
    saved['editorial']['calls'] = saved['editorial']['profile']['profile']['max_calls']
    assert workflow.write(saved) == first
    assert len(requests) == 1


def test_new_plan_identity_requires_new_complete_delivery(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    requests = provider(monkeypatch, [wire(saved), wire(saved, long=True)])
    first = workflow.write(saved)
    saved['plan']['data']['opening'] = 'Situar outro contexto das mesmas observações.'
    saved['plan']['version'] = generation.article_hash(saved['plan']['data'])
    second = workflow.write(saved)
    assert first['markdown'] != second['markdown']
    assert len(requests) == 2
    assert len(store.artifacts(saved['id'], 'composition_draft')) == 2
    assert not store.artifacts(saved['id'], 'draft_section')


def test_invalid_video_references_are_rejected_without_replacing_previous_article(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    previous = saved['article']
    invalid = wire(saved)
    body = json.loads(invalid['output'][0]['content'][0]['text'])
    body['paragraphs'][1]['source_ids'] = ['rn1']
    requests = provider(monkeypatch, [response(json.dumps(body)), response(json.dumps(body))])
    with pytest.raises(generation.GenerationResponseError):
        workflow.write(saved)
    assert len(requests) == 2
    assert db.get_job(saved['id'])['article'] == previous
    assert not store.artifacts(saved['id'], 'composition_draft')


def test_writer_preserves_last_call_for_factual_review(job, newsroom_ai, monkeypatch):
    saved = ready(job, newsroom_ai, monkeypatch)
    cap = saved['editorial']['profile']['profile']['max_calls']
    saved['editorial']['calls'] = cap - 1
    previous = deepcopy(saved['article'])
    requests = provider(monkeypatch, [])
    with pytest.raises(workflow.BudgetExceeded):
        workflow.write(saved)
    assert saved['article'] == previous and not requests
    assert saved['editorial']['calls'] == cap - 1


def test_global_factual_review_blocks_planned_content_absent_from_article(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')

    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is VideoFidelityReview:
            for assessment in output['assessments']:
                assessment.update(status='not_factual', used_item_ids=[], evidence=[])
        return output

    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = respond
    engine.final_review(saved, 0)
    assert saved['review']['coverage'][0]['status'] == 'pending'
    assert any(f['severity'] == 'blocking' and f['origin'] == 'coverage' for f in saved['review']['findings'])
    assert newsroom_ai.call_count == 1 and newsroom_ai.call_args.args[1] is VideoFidelityReview


def test_factual_review_does_not_trust_writer_self_reported_usage(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    workflow.write(saved)
    declared = store.artifacts(saved['id'], 'draft_coverage')[0]['data']
    assert declared['used_item_ids'] and not declared['missing_item_ids']

    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is VideoFidelityReview:
            for assessment in result['assessments']:
                if assessment['status'] == 'supported':
                    assessment.update(status='unsupported', used_item_ids=[],
                                      reason='O artigo ampliou a explicação além da fala.')
        return result

    newsroom_ai.side_effect = respond
    engine.final_review(saved, 0)
    assert saved['review']['coverage'][0]['status'] == 'pending'
    assert any(f['origin'] == 'semantic_review' and f['severity'] == 'blocking' for f in saved['review']['findings'])
