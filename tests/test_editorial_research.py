from copy import deepcopy
import json
from unittest.mock import Mock
from contextlib import contextmanager

import httpx
import pytest

from app import db, generation
from app.editorial import engine, research, store, workflow
from app.editorial.contracts import BackgroundKnowledge, ResearchResolution


def ready(job, newsroom_ai):
    engine.start(job, 'plan')
    segment = job['sources'][0]['segments'][0]
    job['apuration'] = {'valid': True, 'version': 'video-knowledge-test',
        'items': [{'id': 'v1i1', 'video_id': 'v1', 'topic': 'manjericão',
                   'statement': segment['text'], 'evidence': [{'source_id': segment['id'], 'excerpt': segment['text']}],
                   'check': {'status': 'supported', 'reason': 'Conferido contra a fala.'}}],
        'inventory': {'blocks': []}, 'videos': [], 'comparisons': [], 'pending': []}
    job['editorial']['calls'] = 1
    job['brief']['research'] = True
    db.save_job(job)
    return job


def note():
    return {'text': 'Nota para compreender o nome citado, sem uso editorial.', 'sources': [
        {'id': 'w1', 'url': 'https://example.org/research', 'title': 'Publicação original',
         'kind': 'research_note', 'text': 'Um resumo externo que não constitui evidência.'}],
        'notice': 'Contexto interno.'}


def background(term='manjericão', source_id='v1s1'):
    return {'internal_context_only': True, 'terms': [{'term': term,
        'explanation': 'Nota interna para reconhecer o nome mencionado pelo criador.',
        'source_segment_ids': [source_id], 'internal_context_only': True}]}


def mock_research(monkeypatch, newsroom_ai, result=None, page=None):
    search = Mock(return_value=note())
    fetch = Mock(return_value=page or 'Uma referência para compreender nomes e termos já mencionados. ' * 3)
    monkeypatch.setattr(generation, 'research', search)
    monkeypatch.setattr(research, 'page_text', fetch)
    def respond(current, schema, instruction, stage, extra=None):
        if schema is BackgroundKnowledge:
            assert 'APENAS' in instruction and 'NÃO extraia tópicos' in instruction
            assert extra['_context_sources'] == {}
            assert extra['agent_background_knowledge']['internal_context_only'] is True
            assert 'pages' not in {key: value for key, value in extra.items() if key != 'agent_background_knowledge'}
            return background() if result is None else deepcopy(result)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    return search, fetch


def test_web_notes_and_original_pages_stay_internal_and_do_not_change_inventory(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    before = deepcopy(job['apuration'])
    search, fetch = mock_research(monkeypatch, newsroom_ai)
    assert research.run(job, ['O que significa manjericão?']) is False
    assert job['apuration'] == before
    assert generation.evidence_map(job) == generation.evidence_map({**job, 'research': {}})
    assert all(s['internal_context_only'] is True and s['verified'] is False for s in job['research']['sources'])
    assert all(p['internal_context_only'] is True for p in job['research']['pages'])
    assert job['research']['internal_context_only'] is True
    assert job['research_request_results'][job['research']['last_result']['request']]['internal_context_only'] is True
    assert research.agent_background_knowledge(job) == background()
    calls = job['editorial']['calls']
    assert research.run(job, ['O que significa manjericão?']) is False
    assert job['editorial']['calls'] == calls and search.call_count == fetch.call_count == 1


def test_unavailable_page_preserves_only_term_understanding_and_no_evidence(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    _, fetch = mock_research(monkeypatch, newsroom_ai)
    fetch.side_effect = ValueError('not available')
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert job['research']['pages'][0]['status'] == 'unavailable'
    assert all(not k.startswith(('rn', 'wpage')) for k in generation.evidence_map(job))
    assert research.agent_background_knowledge(job) == background()


def test_search_never_runs_for_unmentioned_terms_or_without_budget(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    search, fetch = mock_research(monkeypatch, newsroom_ai)
    assert research.run(job, ['Esclareça Kubernetes.']) is False
    assert research.mentioned_terms(job, ['Como se utiliza Kubernetes?']) == []
    job['editorial']['calls'] = job['editorial']['profile']['profile']['max_calls'] - 3
    assert research.run(job, ['Esclareça manjericão.']) is False
    search.assert_not_called()
    fetch.assert_not_called()


@pytest.mark.parametrize('mode,questions,notice', [
    ('disabled', ['Esclareça manjericão.'], 'desativada'),
    ('no_questions', [], 'Nenhum termo'),
    ('unmentioned', ['Esclareça Kubernetes.'], 'não havia termos citados'),
    ('budget', ['Esclareça manjericão.'], 'reservar o saldo'),
])
def test_skipped_research_reports_its_reason_and_demotes_legacy_material(job, newsroom_ai, monkeypatch, mode, questions, notice):
    ready(job, newsroom_ai)
    before = deepcopy(job['apuration'])
    job['research']['sources'] = [{'id': 'legacy-web', 'verified': True, 'text': 'Uma informação externa.'}]
    job['research']['pages'] = [{'url': 'https://example.org/legacy', 'verified': True}]
    if mode == 'disabled':
        job['brief']['research'] = False
    elif mode == 'budget':
        job['editorial']['calls'] = 5
    search = Mock(side_effect=AssertionError('Skipped research must make no provider request.'))
    monkeypatch.setattr(generation, 'research', search)
    calls = job['editorial']['calls']
    assert research.run(job, questions) is False
    assert job['research']['status'] == 'skipped'
    assert notice in job['research']['notice']
    assert job['research']['internal_context_only'] is True
    assert all(material['internal_context_only'] is True and material['verified'] is False
               for material in job['research']['sources'] + job['research']['pages'])
    assert job['apuration'] == before and job['editorial']['calls'] == calls
    assert db.get_job(job['id'])['research']['status'] == 'skipped'
    search.assert_not_called()


@pytest.mark.parametrize('web_material', [
    {'text': 'EXTERNAL-ONLY-CONTENT', 'internal_context_only': True, 'kind': 'transcript'},
    {'text': 'EXTERNAL-ONLY-CONTENT', 'kind': 'web_excerpt', 'verified': True},
])
def test_internal_or_web_context_is_rejected_even_when_its_id_matches_a_video(job, newsroom_ai, web_material):
    ready(job, newsroom_ai)
    scope, _, _ = engine.invocation_inputs(job, 'planner', {'_context_sources': {'v1s1': web_material}}, None, 'colliding-source')
    assert 'v1s1' not in scope['context_sources']
    token = generation.agent_scope.set({**scope, 'context_sources': {'v1s1': web_material}})
    try:
        data = json.loads(generation.context(job))
    finally:
        generation.agent_scope.reset(token)
    assert 'v1s1' not in data['fontes_para_conferencia']
    assert 'EXTERNAL-ONLY-CONTENT' not in json.dumps(data)


@pytest.mark.parametrize('result', [
    background(term='Kubernetes'), background(source_id='rn1'),
    background(source_id='wpage1s1'),
    {**background(), 'internal_context_only': False},
    {**background(), 'items': [{'statement': 'Um tópico externo.'}]},
    {'internal_context_only': True, 'terms': [{**background()['terms'][0], 'explanation': 'Detalhe externo [[rn1]].'}]},
])
def test_web_extraction_rejects_external_topics_references_and_publication_citations(job, newsroom_ai, monkeypatch, result):
    ready(job, newsroom_ai)
    before = deepcopy(job['apuration'])
    mock_research(monkeypatch, newsroom_ai, result=result)
    assert research.run(job, ['Esclareça manjericão.']) is False
    assert job['apuration'] == before
    assert research.agent_background_knowledge(job)['terms'] == []
    assert job['research']['status'] == 'unavailable'
    assert workflow.remaining(job) >= 3


def test_research_resume_reuses_downloaded_pages_and_cached_search(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    search, fetch = mock_research(monkeypatch, newsroom_ai)
    real_call = workflow.call
    failed = False
    def interrupted(current, role, schema, instruction, payload, slot, validate=None):
        nonlocal failed
        if slot.startswith('webextract:') and not failed:
            failed = True
            raise RuntimeError('interrupted before web extraction')
        return real_call(current, role, schema, instruction, payload, slot, validate)
    monkeypatch.setattr(workflow, 'call', interrupted)
    with pytest.raises(RuntimeError):
        research.run(job, ['Esclareça manjericão.'])
    saved = db.get_job(job['id'])
    before = deepcopy(saved['apuration'])
    assert research.run(saved, ['Esclareça manjericão.']) is False
    assert search.call_count == fetch.call_count == 1
    assert saved['apuration'] == before
    assert research.agent_background_knowledge(saved) == background()


def test_research_cannot_resolve_factual_or_transcription_issues(job, newsroom_ai, monkeypatch):
    ready(job, newsroom_ai)
    factual = store.issue(job, 'comparison', 'conditions', 'A condição do vídeo ainda precisa ser confirmada.', essential=True)
    audio = store.issue(job, 'transcription', 'v1s1', 'Confira o nome no áudio original.', source_ids=['v1s1'])
    mock_research(monkeypatch, newsroom_ai)
    research.run(job, ['Esclareça manjericão.'])
    assert not any(call.args[1] is ResearchResolution for call in newsroom_ai.call_args_list)
    assert all(i['status'] == 'open' for i in store.issues(job) if i['id'] in (factual, audio))


def test_background_helper_rechecks_membership_and_never_exposes_raw_notes():
    value = {'sources': [{'segments': [{'id': 'v1s1', 'text': 'Aqui uso Docker no exemplo.'}]}],
             'research': {'internal_context_only': True, 'text': 'RAW-NOTE',
                 'sources': [{'id': 'rn1', 'text': 'RAW-PAGE'}],
                 'agent_background_knowledge': background(term='Docker')}}
    assert research.agent_background_knowledge(value) == background(term='Docker')
    value['sources'][0]['segments'][0]['text'] = 'Uma explicação sem o nome pesquisado.'
    assert research.agent_background_knowledge(value)['terms'] == []
    value['research'].pop('internal_context_only')
    assert research.agent_background_knowledge(value)['terms'] == []


@pytest.mark.parametrize('url', ['https://127.0.0.1/admin', 'http://example.org',
                                 'https://u:p@example.org/', 'https://example.org:444/'])
def test_research_reader_rejects_private_and_credentialed_urls(url):
    with pytest.raises(ValueError):
        research.page_text(url)


def test_page_reader_pins_validated_ip_and_keeps_tls_hostname(monkeypatch):
    dns = Mock(return_value=[(2, 1, 6, '', ('93.184.215.14', 443))])
    monkeypatch.setattr(research.socket, 'getaddrinfo', dns)
    requests = []
    class Reader:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        @contextmanager
        def stream(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            yield httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'},
                text='<html><script>do not use</script><p>' + 'Condições e método da observação. ' * 5 + '</p></html>',
                request=httpx.Request('GET', url))
    monkeypatch.setattr(research.httpx, 'Client', Reader)
    text = research.page_text('https://example.org/original?q=method')
    assert dns.call_count == 1 and 'do not use' not in text
    _, url, kwargs = requests[0]
    assert url.host == '93.184.215.14' and str(url).endswith('/original?q=method')
    assert kwargs['headers']['Host'] == 'example.org' and kwargs['extensions']['sni_hostname'] == 'example.org'
