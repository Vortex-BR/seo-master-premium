from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
import httpx
from openai import APIConnectionError

from app import db, generation, pipeline
from app.editorial import engine, source_processing, store, workflow
from app.editorial.contracts import (BlockKnowledge, KnowledgeAudit, PassageAudit, PlanStructure,
                                    TopicComparison, TopicPlan, VideoContext, VoiceProfile)


def set_sources(job, count=5, segments=1, width=120):
    original = deepcopy(job['sources'][0])
    sources = []
    for v in range(1, count+1):
        source = deepcopy(original)
        source.update(id=f'v{v}', video_id=f'video{v:06d}', title=f'Fonte {v}', author=f'Autor {v}',
                      url=f'https://www.youtube.com/watch?v=video{v:06d}')
        source['segments'] = [{'id': f'v{v}s{s}', 'start': s*10, 'end': s*10+9,
            'text': f'Na fonte {v}, a observação {s} descreve as folhas com atenção ao método local. ' +
                    ('Detalhes da observação e do contexto. ' * (width//36))[:max(0,width-80)]}
                              for s in range(1,segments+1)]
        sources.append(source)
    sources[-1]['segments'][-1]['text'] += ' O detalhe final exige observar também a face inferior da folha.'
    job['sources'] = sources
    job['brief']['urls'] = [s['url'] for s in sources]
    db.save_job(job)


def prepare(job, newsroom_ai):
    pipeline.run(job['id'], 'plan')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'plan_ready', saved.get('error')
    return saved


def test_partition_preserves_every_character_including_tail_and_unicode():
    text = ('Primeira explicação com condição. Outra frase com ressalva distante.\n' * 1200) + 'ÚLTIMO DETALHE: apenas neste método.'
    source = {'id':'v5','segments':[{'id':'v5s1','text':text,'start':None,'end':None}]}
    groups = source_processing.blocks(source, 3000)
    owned = [part for group in groups for part in group['owned']]
    assert ''.join(part['text'] for part in owned) == text
    assert owned[0]['offset_start'] == 0 and owned[-1]['offset_end'] == len(text)
    assert all(a['offset_end'] == b['offset_start'] for a,b in zip(owned,owned[1:]))
    assert len(groups) > 20 and groups[-1]['owned'][-1]['text'].endswith('apenas neste método.')
    assert all(group['surroundings'] for group in groups)


def test_quality_detects_duplicate_disorder_gap_and_corruption_without_repair():
    source = {'provider':'Legendas','generated_captions':True,'segments':[
        {'text':'Fala repetida.','start':100,'end':105}, {'text':'Fala repetida.','start':20,'end':25},
        {'text':'Termo ??? \ufffd','start':180,'end':190}]}
    original = deepcopy(source)
    quality = source_processing.quality(source)
    assert len(quality['warnings']) == 4
    assert quality['generated_captions'] is True and quality['completeness'] == 'unverified'
    assert source == original
    assert source_processing.quality({'segments':[]})['generated_captions'] is None


def test_plan_only_and_auto_write_disabled_preserve_article(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    assert saved['article'] == job['article'] and db.revisions(job['id']) == []
    assert saved['plan']['valid'] and len(saved['plan']['data']['dispositions']) == 1
    assert not any(c.args[3] in ('writing','writer') for c in newsroom_ai.call_args_list)
    db.set_setting('editorial_profile', VoiceProfile(auto_write=False).model_dump())
    pipeline.run(job['id'])
    assert db.get_job(job['id'])['status'] == 'plan_ready'
    assert db.get_job(job['id'])['article'] == job['article']


def test_five_complementary_videos_all_participate_in_plan_and_review(job, newsroom_ai):
    set_sources(job)
    def respond(current,schema,instruction,stage,extra=None):
        if schema.__name__ == 'Article':
            article = deepcopy(job['article'])
            article['markdown'] = '## Observações das fontes\n\n' + '\n\n'.join(
                s['segments'][0]['text'] + f' [[{s["segments"][0]["id"]}]]' for s in current['sources'])
            return article
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert {i['video_id'] for i in saved['apuration']['items']} == {f'v{i}' for i in range(1,6)}
    assert all(b['status'] == 'checked' for b in saved['apuration']['inventory']['blocks'])
    assert all(d['status'] == 'used' for d in saved['coverage']['items'])
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert 'face inferior' in saved['article']['markdown']


def test_five_long_videos_process_all_blocks_and_last_detail(job, newsroom_ai):
    set_sources(job,segments=30,width=1000)
    db.set_setting('editorial_profile', VoiceProfile(max_calls=400,context_chars=240000).model_dump())
    saved = prepare(job, newsroom_ai)
    assert saved['apuration']['inventory']['characters'] > 140000
    assert len(saved['apuration']['inventory']['blocks']) >= 20
    assert len(saved['apuration']['items']) == 150
    assert any('face inferior' in i['statement'] and i['video_id']=='v5' for i in saved['apuration']['items'])
    assert len(saved['plan']['data']['dispositions']) == 150
    assert all(b['status']=='checked' for b in saved['apuration']['inventory']['blocks'])
    originals = {s['id']:s['text'] for v in saved['sources'] for s in v['segments']}
    restored = {}
    for block in saved['apuration']['inventory']['blocks']:
        for part in block['owned']:
            restored[part['source_id']] = restored.get(part['source_id'],'') + part['text']
    assert restored == originals


def test_large_draft_is_written_by_sections_and_reviewed_as_a_whole(job,newsroom_ai):
    set_sources(job,count=1,segments=40,width=1000)
    db.set_setting('editorial_profile',VoiceProfile(max_calls=300,context_chars=180000).model_dump())
    seen=[]
    def respond(current,schema,instruction,stage,extra=None):
        if schema.__name__=='DraftSection':
            own={i['id'] for i in extra['items']}
            peers={i['id'] for i in extra['counterpoints_and_conditions']}
            assert not own & peers
            originals=workflow.item_index(current)
            for item in [*extra['items'],*extra['counterpoints_and_conditions']]:
                original=originals[item['id']]
                assert {k:item[k] for k in workflow.compact(original)}==workflow.compact(original)
                assert item['source_ids']==list(dict.fromkeys(e['source_id'] for e in original['evidence']))
                for evidence in original['evidence']:
                    assert evidence['excerpt'] in extra['_context_sources'][evidence['source_id']]['text']
            seen.append(own|peers)
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect=respond
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='ready',saved.get('error')
    assert store.artifacts(job['id'],'draft_section')
    assert len(saved['coverage']['items'])==40
    assert all(c['status']=='used' for c in saved['coverage']['items'])
    assert saved['review']['article_hash']==generation.article_hash(saved['article'])
    assert seen


def test_literal_quote_does_not_override_semantic_rejection(job, newsroom_ai):
    def respond(current,schema,instruction,stage,extra=None):
        output = newsroom_ai.respond(current,schema,instruction,stage,extra)
        if schema is BlockKnowledge:
            output['items'][0]['statement'] = 'Todas as hortas crescem sempre sem risco.'
        if schema is KnowledgeAudit:
            for check in output['checks']:
                check.update(status='unsupported',reason='A observação pessoal não sustenta uma regra universal.')
        return output
    newsroom_ai.side_effect=respond
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='needs_input'
    assert saved['apuration']['items'][0]['check']['status']=='unsupported'
    assert saved['article']==job['article']
    assert not any(call.args[3]=='writing' for call in newsroom_ai.call_args_list)


def test_distant_caveat_is_linked_checked_and_passed_to_planner(job, newsroom_ai):
    set_sources(job,count=1,segments=10,width=1000)
    job['sources'][0]['segments'][-1]['text'] += ' A recomendação inicial vale apenas para folhas novas.'
    db.save_job(job)
    seen=[]
    def respond(current,schema,instruction,stage,extra=None):
        result=newsroom_ai.respond(current,schema,instruction,stage,extra)
        if schema is VideoContext:
            index=extra['video_index']
            result['relations']=[{'item_ids':[index[0]['id'],index[-1]['id']],
                'relation':'restriction','explanation':'A recomendação inicial é limitada às folhas novas.'}]
        if schema is TopicPlan:
            seen.extend(extra['video_relations'])
        return result
    newsroom_ai.side_effect=respond
    saved=prepare(job,newsroom_ai)
    assert saved['apuration']['videos'][0]['relations']
    assert seen and any(v['relations'] for v in seen)
    runs=store.report(saved)['runs']
    assert any(':relations:' in r['data']['slot'] and r['role']=='source_checker' for r in runs)


def test_same_number_different_units_and_methods_remain_separate(job, newsroom_ai):
    set_sources(job,count=2)
    job['sources'][0]['segments'][0]['text']='No método A, observe a folha durante 3 horas, apenas em ambiente seco.'
    job['sources'][1]['segments'][0]['text']='No método B, observe a folha durante 3 minutos, apenas em ambiente úmido.'
    db.save_job(job)
    def respond(current,schema,instruction,stage,extra=None):
        result=newsroom_ai.respond(current,schema,instruction,stage,extra)
        if schema is BlockKnowledge:
            unit='horas' if extra['block']['video_id']=='v1' else 'minutos'
            for item in result['items']:
                item.update(method='A' if unit=='horas' else 'B',
                    quantities=[{'value':'3','unit':unit,'context':'Observação no método indicado.'}])
        if schema is TopicComparison:
            result['rows'][0].update(relation='different_methods',treatment='keep_separate',
                                    explanation='Métodos diferentes conservam suas unidades e condições.')
        return result
    newsroom_ai.side_effect=respond
    saved=prepare(job,newsroom_ai)
    assert {i['quantities'][0]['unit'] for i in saved['apuration']['items']}=={'horas','minutos'}
    row=saved['apuration']['comparisons'][0]['rows'][0]
    assert row['relation']=='different_methods' and row['treatment']=='keep_separate'


def test_visual_only_information_is_pending_and_not_in_article(job, newsroom_ai):
    def respond(current,schema,instruction,stage,extra=None):
        result=newsroom_ai.respond(current,schema,instruction,stage,extra)
        if schema is KnowledgeAudit:
            for check in result['checks']:
                check.update(status='uncertain',reason='A medida aparece apenas no gráfico, não analisado.')
        return result
    newsroom_ai.side_effect=respond
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='needs_input' and saved['article']==job['article']
    assert store.issues(saved)[0]['status']=='open'


def test_plan_edit_is_versioned_invalidates_draft_and_rejects_stale_save(authed,job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    plan=deepcopy(saved['plan']['data']);plan['sections'][0]['title']='Uma nova organização das observações'
    payload={'base_version':saved['plan']['version'],'plan':plan}
    assert authed.put(f'/api/jobs/{job["id"]}/plan',json=payload).status_code==200
    changed=db.get_job(job['id'])
    assert changed['article']==job['article'] and changed['article_needs_generation']
    assert changed['plan']['version']!=saved['plan']['version'] and changed['review'] is None
    assert authed.put(f'/api/jobs/{job["id"]}/plan',json=payload).status_code==409
    history=authed.get(f'/api/jobs/{job["id"]}/artifacts?kind=plan').json()
    assert {a['version'] for a in history}=={changed['plan']['version'],saved['plan']['version']}


@pytest.mark.parametrize('invalid',['unknown','missing','unsupported'])
def test_plan_rejects_unknown_omitted_or_unverified_information(authed,job,newsroom_ai,invalid):
    saved=prepare(job,newsroom_ai)
    plan=deepcopy(saved['plan']['data'])
    if invalid=='unknown':plan['sections'][0]['item_ids'].append('invented')
    elif invalid=='missing':plan['dispositions']=[]
    else:
        saved['apuration']['items'][0]['check']['status']='uncertain'
        db.save_job(saved)
    response=authed.put(f'/api/jobs/{job["id"]}/plan',json={'base_version':saved['plan']['version'],'plan':plan})
    assert response.status_code==400
    assert db.get_job(job['id'])['plan']==saved['plan']


def test_plan_edit_blocked_while_busy_and_source_change_invalidates(authed,job,newsroom_ai):
    saved=prepare(job,newsroom_ai);saved['status']='writing';db.save_job(saved)
    payload={'base_version':saved['plan']['version'],'plan':saved['plan']['data']}
    assert authed.put(f'/api/jobs/{job["id"]}/plan',json=payload).status_code==409
    saved['status']='plan_ready';db.save_job(saved)
    response=authed.post(f'/api/jobs/{job["id"]}/source',json={'video_id':job['sources'][0]['video_id'],
        'text':'Uma nova fala sobre a horta, com outras condições. '*5})
    assert response.status_code==200
    changed=db.get_job(job['id'])
    assert not changed['plan']['valid'] and not changed['apuration']['valid']
    assert authed.post(f'/api/jobs/{job["id"]}/write').status_code==400


def test_budget_stops_before_paid_work_and_resume_accepts_increase(job,newsroom_ai):
    db.set_setting('editorial_profile',VoiceProfile(max_calls=12).model_dump())
    pipeline.run(job['id'])
    stopped=db.get_job(job['id'])
    assert stopped['status']=='budget_exhausted' and stopped['editorial']['calls']==0
    assert stopped['article']==job['article'];newsroom_ai.assert_not_called()
    db.set_setting('editorial_profile',VoiceProfile(max_calls=120).model_dump())
    pipeline.run(job['id'],'resume')
    assert db.get_job(job['id'])['status']=='ready'


def test_restart_reuses_completed_blocks_and_changed_payload_is_not_reused(job,newsroom_ai):
    set_sources(job,count=1,segments=10,width=1000)
    seen=[];failed=False
    def respond(current,schema,instruction,stage,extra=None):
        nonlocal failed
        if schema is BlockKnowledge:
            block=extra['block']['id'];seen.append(block)
            if block.endswith('b2') and not failed:
                failed=True;raise RuntimeError('interruption')
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect=respond
    pipeline.run(job['id'],'plan')
    assert db.get_job(job['id'])['status']=='error'
    pipeline.run(job['id'],'resume')
    saved=db.get_job(job['id'])
    assert saved['status']=='plan_ready',saved.get('error')
    assert seen.count('v1b1')==1 and seen.count('v1b2')==2
    engine.start(saved,'optimize')
    first=engine.invoke(saved,'reader',{'article':saved['article']})[1]
    changed={**saved['article'],'title':'Outro título para a mesma horta'}
    second=engine.invoke(saved,'reader',{'article':changed})[1]
    assert first!=second


def test_missing_semantic_assessment_is_retried_and_never_approved(job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    engine.start(saved,'review')
    def respond(current,schema,instruction,stage,extra=None):
        output=newsroom_ai.respond(current,schema,instruction,stage,extra)
        if schema is PassageAudit:output['assessments']=output['assessments'][:-1]
        return output
    newsroom_ai.side_effect=respond
    with pytest.raises(generation.GenerationResponseError):workflow.factual_review(saved,0)
    assert saved['editorial']['calls']==2 and saved['review'] is None


def test_review_batches_cover_more_than_eighty_evidences_and_last_paragraph(job,newsroom_ai):
    set_sources(job,count=1,segments=90,width=100)
    db.set_setting('editorial_profile',VoiceProfile(max_calls=400,context_chars=240000).model_dump())
    saved=prepare(job,newsroom_ai)
    saved['article']['markdown']='## Observações\n\n'+'\n\n'.join(
        s['text']+f' [[{s["id"]}]]' for s in saved['sources'][0]['segments'])
    db.save_job(saved);engine.start(saved,'review')
    result=workflow.factual_review(saved,0)
    assert result['semantic_coverage']['batches']>=2
    assert result['semantic_coverage']['assessed']==len(workflow.passages(saved['article']))
    assert any(e['source_id']=='v1s90' for c in result['supported_claims'] for e in c['evidence'])
    assert all(i['status']=='used' for i in result['coverage'])


def test_issues_survive_new_summary_and_resolution_has_history(authed,job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    ident=store.issue(saved,'comparison','critical','Falta uma condição indispensável para comparar métodos.',essential=True)
    before=store.issues(saved)
    engine.invoke(saved,'reader',{'article':saved['article']})
    assert store.issues(saved)==before
    saved['status']='plan_ready';db.save_job(saved)
    response=authed.post(f'/api/jobs/{job["id"]}/issues/{ident}/resolve',json={
        'reason':'Conferi o trecho original e preservei os métodos em alternativas separadas.','source_ids':['v1s1']})
    assert response.status_code==200
    resolved=store.issues(saved)[0]
    assert resolved['status']=='resolved' and resolved['reason']==before[0]['reason']
    assert resolved['resolution']['source_ids']==['v1s1']


@pytest.mark.parametrize('essential', [False, True])
def test_comparison_priority_is_scoped_to_plan_and_keeps_issue_open(job,newsroom_ai,essential):
    saved=prepare(job,newsroom_ai)
    ident=store.issue(saved,'comparison','optional','Não é possível comparar observações de métodos diferentes.',essential=True)
    saved['apuration']['pending']=store.issues(saved)
    data=deepcopy(saved['plan']['data'])
    data['issue_priorities']=[{'issue_id':ident,'essential':essential,
        'reason':'A relevância da comparação foi avaliada em relação à pergunta central do plano.'}]
    workflow.save_plan(saved,data)
    assert saved['plan']['data']['ready_to_write'] is (not essential)
    issue=store.issues(saved)[0]
    assert workflow.essential_issue(saved,issue) is essential
    assert issue['status']=='open' and issue['essential'] is True and issue['resolution'] is None
    review=workflow.factual_review(saved,0)
    assert any(f.get('origin')=='pending_issue' for f in review['findings']) is essential
    saved['plan']['valid']=False
    assert workflow.essential_issue(saved,issue) is True
    saved['plan']['valid']=True
    saved['plan']['input_version']='old-version'
    assert workflow.essential_issue(saved,issue) is True
    saved['plan']['input_version']=store.inputs_version(saved)
    assert workflow.essential_issue(saved,{**issue,'origin':'transcription'}) is True


def test_plan_cannot_change_priority_of_unknown_or_transcription_issues(job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    ident=store.issue(saved,'transcription','audio','Uma condição falada ainda não pôde ser entendida.',essential=True)
    saved['apuration']['pending']=store.issues(saved)
    data=deepcopy(saved['plan']['data'])
    data['issue_priorities']=[{'issue_id':ident,'essential':False,'reason':'A condição foi considerada complementar neste plano.'}]
    with pytest.raises(generation.GenerationResponseError):
        workflow.save_plan(saved,data)


def test_writing_continues_saved_plan_budget_and_keeps_completed_deliveries(job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    before=deepcopy(saved['editorial'])
    workflow.save_plan(saved,deepcopy(saved['plan']['data']),manual=True)
    store.invalidate(saved,'O planejamento foi editado. Redija a partir da nova versão.')
    assert engine.start(saved,'write')=='write'
    assert saved['editorial']['cycle_id']==before['cycle_id']
    assert saved['editorial']['calls']==before['calls']
    assert saved['editorial']['completed']==before['completed']
    assert saved['editorial']['stale'] is False
    db.set_setting('editorial_profile',VoiceProfile(tone='Outra voz editorial.').model_dump())
    engine.start(saved,'write')
    assert saved['editorial']['cycle_id']!=before['cycle_id'] and saved['editorial']['calls']==0


def test_authentication_estimates_and_legacy_switch(client,authed,job,newsroom_ai,monkeypatch):
    estimate=authed.get(f'/api/jobs/{job["id"]}/estimate').json()
    assert estimate['blocks']==1 and estimate['estimated_calls_min']==18
    monkeypatch.setenv('EDITORIAL_FLOW','legacy')
    pipeline.run(job['id'])
    saved=db.get_job(job['id'])
    assert saved['status']=='ready' and saved['editorial']['calls']==12
    assert authed.post(f'/api/jobs/{job["id"]}/plan').status_code==400


def test_connection_retry_is_counted_and_does_not_save_provider_content(job,newsroom_ai):
    engine.start(job,'review');attempt=0
    def respond(current,schema,instruction,stage,extra=None):
        nonlocal attempt
        attempt+=1
        if attempt==1:
            raise APIConnectionError(request=httpx.Request('POST','https://api.openai.com/v1/responses'))
        return newsroom_ai.respond(current,schema,instruction,stage,extra)
    newsroom_ai.side_effect=respond
    engine.invoke(job,'reader',{'article':job['article']})
    assert attempt==2 and job['editorial']['calls']==2
    assert job['editorial']['response_recoveries']['reader']['reason']=='connection'
    assert {r['status'] for r in store.report(job)['runs']}=={'failed','completed'}


def test_editorial_endpoints_require_authentication(client):
    for path in ['/api/jobs/nope/estimate','/api/jobs/nope/artifacts']:
        assert client.get(path).status_code==401
    for path in ['/api/jobs/nope/plan','/api/jobs/nope/write']:
        assert client.post(path).status_code==401


def test_budget_and_automation_changes_preserve_knowledge_but_voice_changes_do_not(job,newsroom_ai):
    saved=prepare(job,newsroom_ai)
    before=deepcopy(saved['editorial'])
    db.set_setting('editorial_profile',VoiceProfile(max_calls=200,context_chars=160000,auto_write=False).model_dump())
    engine.start(saved,'resume')
    assert saved['editorial']['profile']['profile']['context_chars']==160000
    assert saved['editorial']['calls']==before['calls'] and saved['editorial']['completed']==before['completed']
    engine.start(saved,'write')
    assert workflow.compatible(saved)
    db.set_setting('editorial_profile',VoiceProfile(tone='Outra voz editorial.').model_dump())
    engine.start(saved,'plan')
    assert not workflow.compatible(saved)


def test_new_extraction_contract_uses_actual_sdk_serialization_and_records_usage(job,newsroom_ai,monkeypatch):
    from test_response_recovery import provider,response,structured
    engine.start(job,'plan')
    block=source_processing.blocks(job['sources'][0])[0]
    payload={'block':block,'_context_sources':generation.evidence_map(job)}
    expected=newsroom_ai.respond(job,BlockKnowledge,workflow.EXTRACT,'extractor',payload)
    wire=deepcopy(expected)
    wire['items'][0]['evidence']=[{'reference':'e1'}]
    requests=provider(monkeypatch,[response(json.dumps(wire))])
    monkeypatch.setattr(generation,'structured',structured)
    actual=workflow.call(job,'extractor',BlockKnowledge,workflow.EXTRACT,payload,'sdk:extract',
                         lambda result:workflow.validate_evidence(result['items'],generation.evidence_map(job)))
    assert actual==expected and len(requests)==1
    fmt=requests[0]['text']['format']
    assert fmt['strict'] and fmt['schema']['additionalProperties'] is False
    assert fmt['schema']['$defs']['SelectedKnowledgeItem']['additionalProperties'] is False
    material=json.loads(requests[0]['input'])
    assert material['fontes_para_conferencia']['v1s1']['evidence_references']==['e1']
    assert material['evidence_options']['e1']['excerpt']==job['sources'][0]['segments'][0]['text']
    assert 'max_calls' not in material['equipe_editorial']['profile']['profile']
    assert job['usage'][0]['input_tokens']==123 and job['editorial']['calls']==1


def test_selected_evidence_preserves_all_spans_unicode_and_block_ownership():
    from app.editorial import evidence_selection
    text = ('Contexto original: ação e observação das folhas. ' * 180) + 'ÚLTIMA RESSALVA.'
    original = {'v1s1': {'text': text}}
    owned = {'v1s1': {'text': text[3000:6000]}}
    schema, options = evidence_selection.prepare(BlockKnowledge, owned, original)
    assert ''.join(option['excerpt'] for option in options.values()) == text[3000:6000]
    assert all(option['excerpt'] in owned['v1s1']['text'] for option in options.values())
    assert all(option['source_id'] == 'v1s1' for option in options.values())
    assert schema is not BlockKnowledge


def test_review_evidence_never_quotes_window_markers_or_unseen_spans():
    from app.editorial import evidence_selection
    original = {'v1s1': {'text': 'Primeira fala. Fora do contexto. Última ressalva.'}}
    spans = [(0, 14), (31, len(original['v1s1']['text']))]
    sources = {'v1s1': {'text': 'Primeira fala.\n[... intervalo entre trechos ...]\nÚltima ressalva.',
                        'original_spans': spans}}
    schema, options = evidence_selection.prepare(PassageAudit, sources, original)
    assert all(option['excerpt'] in original['v1s1']['text'] for option in options.values())
    assert not any('Fora do contexto' in option['excerpt'] or '[...' in option['excerpt'] for option in options.values())
    selected = {'summary': 'Conferência', 'assessments': [{'passage_id': 'p1', 'status': 'supported',
        'reason': 'Conferido', 'evidence': [{'reference': 'e2'}], 'used_item_ids': ['k1']}]}
    result = evidence_selection.resolve(schema.model_validate(selected).model_dump(), PassageAudit, options)
    workflow.validate_evidence(result['assessments'], original)
    assert result['assessments'][0]['evidence'] == [options['e2']]


def test_invalid_evidence_selection_retries_without_accepting_invented_reference(job, newsroom_ai, monkeypatch):
    from test_response_recovery import provider, response, structured
    engine.start(job, 'plan')
    block = source_processing.blocks(job['sources'][0])[0]
    payload = {'block': block, '_context_sources': generation.evidence_map(job)}
    expected = newsroom_ai.respond(job, BlockKnowledge, workflow.EXTRACT, 'extractor', payload)
    invalid = deepcopy(expected)
    invalid['items'][0]['evidence'] = [{'reference': 'private-invented-ref'}]
    valid = deepcopy(invalid)
    valid['items'][0]['evidence'] = [{'reference': 'e1'}]
    requests = provider(monkeypatch, [response(json.dumps(invalid)), response(json.dumps(valid))])
    monkeypatch.setattr(generation, 'structured', structured)
    actual = workflow.call(job, 'extractor', BlockKnowledge, workflow.EXTRACT, payload, 'selection-retry',
                           lambda result: workflow.validate_evidence(result['items'], generation.evidence_map(job)))
    assert actual == expected
    assert len(requests) == 2 and job['editorial']['calls'] == 2
    runs = store.report(job)['runs']
    failed = next(run['data'] for run in runs if run['status'] == 'failed')
    assert failed['validation_errors'][0]['type'] == 'literal_error'
    assert 'private-invented-ref' not in json.dumps(runs)
    assert 'private-invented-ref' not in requests[1]['input']


def test_evidence_mismatch_has_specific_safe_diagnostic_and_recovery(job, monkeypatch):
    engine.start(job, 'plan')
    calls = []
    def deliver(current):
        calls.append(deepcopy(generation.agent_scope.get()))
        workflow.validate_evidence([{'evidence': [{'source_id': 'missing-private-id', 'excerpt': 'private quote'}]}], {})
    with pytest.raises(generation.GenerationResponseError) as error:
        engine.invoke(job, 'extractor', callback=deliver)
    assert error.value.reason == 'evidence_mismatch'
    assert len(calls) == 2 and calls[1]['response_recovery']['reason'] == 'evidence_mismatch'
    assert 'private' not in str(error.value)
    assert {r['data']['error_reason'] for r in store.report(job)['runs']} == {'evidence_mismatch'}


def test_relation_audit_requires_top_level_ids_not_nested_knowledge_ids(job, monkeypatch):
    from test_response_recovery import provider, response, structured
    engine.start(job, 'plan')
    payload = {'items': [{'id': 'r1', 'statement': 'Relação a conferir',
                         'items': [{'id': 'v1b1k1'}, {'id': 'v1b1k2'}]},
                        {'id': 'r2', 'statement': 'Outra relação', 'items': [{'id': 'v1b1k1'}]}]}
    bad = {'summary': 'Conferência', 'checks': {'v1b1k1': {'status': 'supported', 'reason': 'Fala original'}}}
    good = {'summary': 'Conferência', 'checks': {
        'r1': {'status': 'uncertain', 'reason': 'A relação é ambígua.'},
        'r2': {'status': 'unsupported', 'reason': 'O original não sustenta a relação.'}}}
    requests = provider(monkeypatch, [response(json.dumps(bad)), response(json.dumps(good))])
    result = workflow.call(job, 'source_checker', KnowledgeAudit, workflow.CHECK, payload, 'relation-sdk',
                           lambda output: workflow.exact_ids([c['item_id'] for c in output['checks']], ['r1', 'r2'], 'Relações'))
    assert [c['item_id'] for c in result['checks']] == ['r1', 'r2']
    assert [c['status'] for c in result['checks']] == ['uncertain', 'unsupported']
    schema = requests[0]['text']['format']['schema']['$defs']['RequiredItemChecks']
    assert set(schema['required']) == {'r1', 'r2'} and schema['additionalProperties'] is False
    assert len(requests) == 2 and job['editorial']['calls'] == 2


@pytest.mark.parametrize('checks', [{}, {'r1': {'status': 'supported', 'reason': 'Conferido'}}])
def test_audit_missing_item_is_never_filled_automatically(checks):
    from pydantic import ValidationError
    from app.editorial import evidence_selection
    schema, ids = evidence_selection.prepare_audit(KnowledgeAudit, {'items': [{'id': 'r1'}, {'id': 'r2'}]})
    with pytest.raises(ValidationError):
        schema.model_validate({'summary': 'Incomplete', 'checks': checks})
    assert ids == ('r1', 'r2')
