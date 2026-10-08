from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import engine, research, store, workflow
from app.editorial.contracts import ResearchResolution, TopicComparison
from test_editorial_research import note, ready


def test_empty_research_preserves_knowledge_version_and_cached_topic_routing(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    workflow.route_topics(job)
    version = job['apuration']['version']
    artifacts = len(store.artifacts(job['id'], 'knowledge'))
    monkeypatch.setattr(generation, 'research', lambda current: note())
    monkeypatch.setattr(research, 'page_text', Mock(side_effect=ValueError('unavailable')))
    assert research.run(job, ['Confira as condições.']) is False
    assert job['apuration']['version'] == version
    assert len(store.artifacts(job['id'], 'knowledge')) == artifacts
    calls = job['editorial']['calls']
    workflow.route_topics(job)
    assert job['editorial']['calls'] == calls
    assert research.run(job, ['Confira as condições.']) is False
    assert job['editorial']['calls'] == calls


def test_planning_does_not_repeat_comparisons_after_empty_research(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    engine.start(job, 'plan')
    workflow.extract(job)
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is TopicComparison:
            result['research_questions'] = ['Quais condições se aplicam?']
        return result
    newsroom_ai.side_effect = respond
    monkeypatch.setattr(generation, 'research', lambda current: note())
    monkeypatch.setattr(research, 'page_text', Mock(side_effect=ValueError('unavailable')))
    route = Mock(wraps=workflow.route_topics)
    compare = Mock(wraps=workflow.compare)
    monkeypatch.setattr(workflow, 'route_topics', route)
    monkeypatch.setattr(workflow, 'compare', compare)
    workflow.plan(job)
    assert route.call_count == compare.call_count == 1
    assert job['plan']['valid']


def test_research_progress_survives_interruption_after_last_extracted_block(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    before = job['apuration']['version']
    monkeypatch.setattr(generation, 'research', lambda current: note())
    fetch = Mock(return_value='O registro descreve somente a observação nas condições informadas. ' * 3)
    monkeypatch.setattr(research, 'page_text', fetch)
    real_state = research.knowledge_state
    count = 0
    def interrupt(current):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('interrupted after extraction')
        return real_state(current)
    monkeypatch.setattr(research, 'knowledge_state', interrupt)
    with pytest.raises(RuntimeError):
        research.run(job, ['Confira as condições.'])
    saved = db.get_job(job['id'])
    assert any(i['video_id'].startswith('wpage') for i in saved['apuration']['items'])
    monkeypatch.setattr(research, 'knowledge_state', real_state)
    assert research.run(saved, ['Confira as condições.']) is True
    assert saved['apuration']['version'] != before
    assert fetch.call_count == 1


def test_web_evidence_cannot_resolve_audio_confidence_issue(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    ident = store.issue(job, 'transcription', 'v1s1', 'Confira o termo no áudio original.', source_ids=['v1s1'])
    monkeypatch.setattr(generation, 'research', lambda current: note())
    monkeypatch.setattr(research, 'page_text', lambda url: 'O registro descreve as condições de observação da fonte. ' * 3)
    research.run(job, ['Confira as condições.'])
    assert not any(call.args[1] is ResearchResolution for call in newsroom_ai.call_args_list)
    assert next(i for i in store.issues(job) if i['id'] == ident)['status'] == 'open'


def pending_cycle(job, findings):
    engine.start(job, 'generate')
    job['editorial'].update(initial_complete=True, draft_installed=True, composition_version=1)
    job['editorial']['correction_pending'] = {
        'round': 1, 'findings': findings, 'chief': {'decision': 'revise', 'summary': 'Confira os apontamentos.'}}
    job['review'] = {'article_hash': generation.article_hash(job['article']), 'findings': deepcopy(findings),
                     'semantic_coverage': {'batches': 2}, 'summary': 'Revisão anterior.'}
    db.save_job(job)


def test_old_saved_audio_findings_do_not_start_web_search_or_paid_editor(job, newsroom_ai, monkeypatch):
    ident = store.issue(job, 'transcription', 'v1s1', 'Confira o termo no áudio original.', source_ids=['v1s1'])
    finding = {'severity': 'blocking', 'origin': 'pending_issue', 'recipient': 'apuration',
               'reason': 'Confira o termo no áudio original.', 'source_ids': ['v1s1'], 'passage': '', 'suggestion': 'Confira.'}
    pending_cycle(job, [finding])
    web = Mock(side_effect=AssertionError('no web research for audio'))
    monkeypatch.setattr(research, 'run', web)
    article = deepcopy(job['article'])
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review'
    assert saved['article'] == article and saved['editorial']['calls'] == 0
    assert saved['review']['findings'][0] == finding
    assert next(i for i in store.issues(job) if i['id'] == ident)['status'] == 'open'
    newsroom_ai.assert_not_called()
    web.assert_not_called()


def test_exhausted_cycle_keeps_current_review_without_more_spending(job, newsroom_ai):
    finding = {'severity': 'blocking', 'recipient': 'writing', 'reason': 'Falta desenvolver a explicação.',
               'source_ids': [], 'passage': '', 'suggestion': 'Confira o trecho.'}
    pending_cycle(job, [finding])
    job['editorial']['calls'] = job['editorial']['profile']['profile']['max_calls']
    calls = job['editorial']['calls']
    db.save_job(job)
    for _ in range(2):
        pipeline.run(job['id'], 'resume')
        saved = db.get_job(job['id'])
        assert saved['status'] == 'needs_review' and saved['error'] is None
        assert saved['editorial']['calls'] == calls and saved['article'] == job['article']
        assert saved['review']['findings'][0] == finding
        assert len([f for f in saved['review']['findings'] if f.get('code') == 'correction_budget']) == 1
    newsroom_ai.assert_not_called()


def test_unchanged_correction_does_not_pay_for_an_identical_review(job, newsroom_ai, monkeypatch):
    finding = {'severity': 'blocking', 'recipient': 'writing', 'reason': 'Confira a explicação.',
               'source_ids': [], 'passage': '', 'suggestion': 'Confira.'}
    pending_cycle(job, [finding])
    review = Mock(side_effect=AssertionError('unchanged text must keep its saved review'))
    monkeypatch.setattr(engine, 'final_review', review)
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review' and saved['editorial']['calls'] == 1
    assert saved['review']['findings'][0] == finding
    review.assert_not_called()
