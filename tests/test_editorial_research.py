from unittest.mock import Mock
from contextlib import contextmanager

import httpx

import pytest

from app import db, generation
from app.editorial import engine, research, store, workflow
from app.editorial.contracts import ResearchResolution


def ready(job,newsroom_ai):
    engine.start(job,'plan');workflow.extract(job);workflow.plan(job)
    job['brief']['research']=True
    db.save_job(job)
    return job


def note():
    return {'text':'Nota resumida que não é a página original.', 'sources':[
        {'id':'w1','url':'https://example.org/research','title':'Publicação original','kind':'research_note',
         'text':'A nota afirma um resultado absoluto sem apresentar as condições.'}], 'notice':'Conferir a fonte.'}


def test_generated_notes_are_excluded_and_original_page_is_checked(job,newsroom_ai,monkeypatch):
    ready(job,newsroom_ai)
    original='A observação das folhas deve preservar o método e as condições descritas na fonte. '*3
    monkeypatch.setattr(generation,'research',lambda current:note())
    fetch=Mock(return_value=original);monkeypatch.setattr(research,'page_text',fetch)
    research.run(job,['Quais condições limitam a observação?'])
    mapping=generation.evidence_map(job)
    assert 'rn1' not in mapping
    assert mapping['wpage1s1']['text']==original and mapping['wpage1s1']['verified'] is True
    assert job['research']['pages'][0]['status']=='checked'
    assert any(i['video_id']=='wpage1' for i in job['apuration']['items'])
    assert all(b['status']=='checked' for b in job['apuration']['inventory']['blocks'])
    calls=job['editorial']['calls'];research.run(job,['Quais condições limitam a observação?'])
    assert job['editorial']['calls']==calls and fetch.call_count==1


def test_unavailable_page_never_becomes_evidence(job,newsroom_ai,monkeypatch):
    ready(job,newsroom_ai)
    monkeypatch.setattr(generation,'research',lambda current:note())
    monkeypatch.setattr(research,'page_text',Mock(side_effect=ValueError('not available')))
    research.run(job,['Confira as condições.'])
    assert all(not k.startswith('rn') and not k.startswith('wpage') for k in generation.evidence_map(job))
    assert job['research']['pages'][0]['status']=='unavailable'


@pytest.mark.parametrize('url',['https://127.0.0.1/admin','http://example.org','https://u:p@example.org/','https://example.org:444/'])
def test_research_reader_rejects_private_and_credentialed_urls(url):
    with pytest.raises(ValueError):research.page_text(url)


def test_research_resume_does_not_download_or_extract_completed_page_blocks_again(job,newsroom_ai,monkeypatch):
    ready(job,newsroom_ai)
    original='Uma explicação longa com condições verificáveis e método identificado. '*150
    monkeypatch.setattr(generation,'research',lambda current:note())
    fetch=Mock(return_value=original);monkeypatch.setattr(research,'page_text',fetch)
    real_call=workflow.call;failed=False;blocks=[]
    def interrupted(current,role,schema,instruction,payload,slot,validate=None):
        nonlocal failed
        if slot.startswith('webextract:'):
            blocks.append(payload['block']['id'])
            if slot.endswith('b2') and not failed:
                failed=True;raise workflow.BudgetExceeded('interrupted')
        return real_call(current,role,schema,instruction,payload,slot,validate)
    monkeypatch.setattr(workflow,'call',interrupted)
    with pytest.raises(workflow.BudgetExceeded):research.run(job,['Confira as condições.'])
    assert job['research']['pages'][0]['status']=='reading'
    research.run(job,['Confira as condições.'])
    assert fetch.call_count==1 and blocks.count('wpage1b1')==1 and blocks.count('wpage1b2')==2
    assert job['research']['pages'][0]['status']=='checked'


def test_research_can_resolve_only_with_actual_original_evidence(job,newsroom_ai,monkeypatch):
    ready(job,newsroom_ai)
    ident=store.issue(job,'comparison','conditions','A condição de aplicação ainda precisa ser confirmada.',essential=True)
    original='A observação das folhas vale somente para o método A em ambiente seco. '*3
    monkeypatch.setattr(generation,'research',lambda current:note())
    monkeypatch.setattr(research,'page_text',lambda url:original)
    def respond(current,schema,instruction,stage,extra=None):
        if schema is ResearchResolution:
            assert all('evidence' not in item for item in extra['web_items'])
            for context_item in extra['web_items']:
                original_item=next(item for item in current['apuration']['items'] if item['id']==context_item['id'])
                assert {key:context_item[key] for key in workflow.compact(original_item)}==workflow.compact(original_item)
                assert context_item['source_ids']==list(dict.fromkeys(e['source_id'] for e in original_item['evidence']))
            assert original in extra['_context_sources']['wpage1s1']['text']
            return {'summary':'Condição conferida no original.', 'answers':[{
                'issue_id':ident,'status':'resolved','reason':'A publicação especifica o método A e o ambiente seco.',
                'evidence':[{'source_id':'wpage1s1','excerpt':'somente para o método A em ambiente seco'}]}]}
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect=respond
    research.run(job,['Qual condição limita o método A?'])
    resolved=next(i for i in store.issues(job) if i['id']==ident)
    assert resolved['status']=='resolved' and resolved['resolution']['actor']=='Checador das fontes'
    assert resolved['resolution']['source_ids']==['wpage1s1']


def test_fabricated_research_resolution_cannot_close_issue(job,newsroom_ai,monkeypatch):
    ready(job,newsroom_ai)
    ident=store.issue(job,'comparison','conditions','A condição de aplicação ainda precisa ser confirmada.',essential=True)
    monkeypatch.setattr(generation,'research',lambda current:note())
    monkeypatch.setattr(research,'page_text',lambda url:'A publicação apenas comenta a observação e não resolve a pergunta. '*3)
    def respond(current,schema,instruction,stage,extra=None):
        if schema is ResearchResolution:
            return {'summary':'Condição conferida.', 'answers':[{'issue_id':ident,'status':'resolved',
                'reason':'Condição declarada como conferida pela pesquisa complementar.',
                'evidence':[{'source_id':'wpage1s1','excerpt':'uma condição inventada'}]}]}
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect=respond
    with pytest.raises(generation.GenerationResponseError):research.run(job,['Qual condição se aplica?'])
    assert next(i for i in store.issues(job) if i['id']==ident)['status']=='open'


def test_page_reader_pins_validated_ip_and_keeps_tls_hostname(monkeypatch):
    dns=Mock(return_value=[(2,1,6,'',('93.184.215.14',443))])
    monkeypatch.setattr(research.socket,'getaddrinfo',dns)
    requests=[]
    class Reader:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextmanager
        def stream(self,method,url,**kwargs):
            requests.append((method,url,kwargs))
            yield httpx.Response(200,headers={'content-type':'text/html; charset=utf-8'},
                                 text='<html><script>do not use</script><p>'+'Condições e método da observação. '*5+'</p></html>',
                                 request=httpx.Request('GET',url))
    monkeypatch.setattr(research.httpx,'Client',Reader)
    text=research.page_text('https://example.org/original?q=method')
    assert dns.call_count==1 and 'do not use' not in text
    _,url,kwargs=requests[0]
    assert url.host=='93.184.215.14' and str(url).endswith('/original?q=method')
    assert kwargs['headers']['Host']=='example.org' and kwargs['extensions']['sni_hostname']=='example.org'
