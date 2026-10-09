from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import engine, research, store, workflow
from app.editorial.contracts import ResearchResolution
from test_editorial_research import background, mock_research, note, ready


def test_empty_research_preserves_knowledge_version_without_rerouting(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    before = deepcopy(job['apuration'])
    version = job['apuration']['version']
    artifacts = len(store.artifacts(job['id'], 'knowledge'))
    empty = Mock(return_value={'text': '', 'sources': [], 'internal_context_only': True})
    monkeypatch.setattr(generation, 'research', empty)
    fetch = Mock(side_effect=AssertionError('No pages were returned.'))
    monkeypatch.setattr(research, 'page_text', fetch)
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert job['apuration'] == before
    assert job['apuration']['version'] == version
    assert len(store.artifacts(job['id'], 'knowledge')) == artifacts
    calls = job['editorial']['calls']
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert job['editorial']['calls'] == calls
    assert empty.call_count == 1
    fetch.assert_not_called()


def test_search_questions_only_contain_terms_found_in_original_video(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    search, _ = mock_research(monkeypatch, newsroom_ai)
    scopes = []
    def capture(current):
        scopes.append(deepcopy(generation.agent_scope.get()))
        return note()
    search.side_effect = capture
    research.run(job, ['Esclareça manjericão e Kubernetes.'])
    scope = scopes[0]
    assert all('Kubernetes' not in question['reason'] for question in scope['research_requests'])
    assert search.call_count == 1


def test_research_progress_survives_interruption_after_background_extraction(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    before = deepcopy(job['apuration'])
    search, fetch = mock_research(monkeypatch, newsroom_ai)
    real_save = db.save_job
    def interrupt(current):
        if current.get('research', {}).get('status') == 'completed':
            raise RuntimeError('interrupted after extraction')
        return real_save(current)
    monkeypatch.setattr(db, 'save_job', interrupt)
    with pytest.raises(RuntimeError):
        research.run(job, ['Esclareça manjericão.'])
    saved = db.get_job(job['id'])
    calls = saved['editorial']['calls']
    monkeypatch.setattr(db, 'save_job', real_save)
    assert research.run(saved, ['Esclareça manjericão.']) is False
    assert saved['apuration'] == before
    assert saved['editorial']['calls'] == calls
    assert search.call_count == fetch.call_count == 1
    assert research.agent_background_knowledge(saved) == background()


def test_web_evidence_cannot_resolve_audio_confidence_issue(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    ident = store.issue(job, 'transcription', 'v1s1', 'Confira o termo no áudio original.', source_ids=['v1s1'])
    mock_research(monkeypatch, newsroom_ai)
    research.run(job, ['Esclareça manjericão.'])
    assert not any(call.args[1] is ResearchResolution for call in newsroom_ai.call_args_list)
    assert next(i for i in store.issues(job) if i['id'] == ident)['status'] == 'open'


def test_audio_findings_remain_open_without_web_search_or_automatic_editor_loops(job, newsroom_ai, monkeypatch):
    ident = store.issue(job, 'transcription', 'v1s1', 'Confira o termo no áudio original.', source_ids=['v1s1'])
    web = Mock(side_effect=AssertionError('No web lookup can resolve uncertain audio.'))
    monkeypatch.setattr(research, 'run', web)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review', saved.get('error')
    assert saved['editorial']['calls'] == 4
    assert any(f.get('issue_id') == ident for f in saved['review']['findings'])
    assert next(i for i in store.issues(saved) if i['id'] == ident)['status'] == 'open'
    article = deepcopy(saved['article'])
    newsroom_ai.reset_mock()
    pipeline.run(job['id'], 'resume')
    resumed = db.get_job(job['id'])
    assert resumed['status'] == 'needs_review'
    assert resumed['article'] == article and resumed['editorial']['calls'] == 4
    newsroom_ai.assert_not_called()
    web.assert_not_called()


def test_exhausted_cycle_reuses_cached_factual_review_without_more_spending(job, newsroom_ai):
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] in ('ready', 'needs_review'), saved.get('error')
    saved['editorial']['calls'] = 8
    article = deepcopy(saved['article'])
    review = deepcopy(saved['review'])
    db.save_job(saved)
    newsroom_ai.reset_mock()
    for _ in range(2):
        pipeline.run(job['id'], 'resume')
        resumed = db.get_job(job['id'])
        assert resumed['status'] in ('ready', 'needs_review') and resumed['error'] is None
        assert resumed['editorial']['calls'] == 8 and resumed['article'] == article
        assert resumed['review']['article_hash'] == review['article_hash']
        assert resumed['review']['supported_claims'] == review['supported_claims']
    newsroom_ai.assert_not_called()


def test_manual_article_review_spends_one_factual_call_and_preserves_text(job, newsroom_ai):
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    saved['article']['markdown'] += '\n\nEssa observação descreve a experiência do autor. [[v1s1]]'
    article = deepcopy(saved['article'])
    db.save_job(saved)
    newsroom_ai.reset_mock()
    pipeline.run(job['id'], 'review')
    reviewed = db.get_job(job['id'])
    assert reviewed['status'] in ('ready', 'needs_review'), reviewed.get('error')
    assert reviewed['article'] == article and reviewed['editorial']['calls'] == 1
    assert reviewed['review']['article_hash'] == generation.article_hash(article)
    assert {call.args[3] for call in newsroom_ai.call_args_list} == {'fact_reviewer'}


def test_failed_optional_search_cannot_retry_into_the_three_core_calls(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    job['editorial']['profile']['profile']['max_calls'] = 8
    search = Mock(side_effect=generation.GenerationResponseError('incomplete', 'Pesquisa interrompida.', retryable=True))
    fetch = Mock(side_effect=AssertionError('No search result is available.'))
    monkeypatch.setattr(generation, 'research', search)
    monkeypatch.setattr(research, 'page_text', fetch)
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert search.call_count == 1 and job['editorial']['calls'] == 4
    assert workflow.remaining(job) >= 3
    assert job['research']['status'] == 'unavailable'
    assert research.agent_background_knowledge(job)['terms'] == []
    assert any(run['status'] == 'failed' for run in store.report(job)['runs'])
    calls = job['editorial']['calls']
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert search.call_count == 1 and job['editorial']['calls'] == calls
    fetch.assert_not_called()
