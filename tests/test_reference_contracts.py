from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from openai.lib._parsing._responses import type_to_text_format_param

from app import generation
from app.editorial import evidence_selection, reference_contracts, workflow
from app.editorial.contracts import (DraftSection, PassageAudit, PlanStructure, TopicComparison,
                                    ResearchResolution, TopicPlan, TopicRouting, VideoContext)


def section(ids):
    return {'id': 's1', 'title': 'Observação', 'question': 'O que observar?',
            'purpose': 'Explicar a observação', 'item_ids': ids, 'prerequisites': [],
            'conditions': [], 'transition': '', 'pending': []}


def comparison(ids):
    return {'summary': 'Comparação das observações.', 'rows': [{'item_ids': ids,
        'relation': 'agreement', 'explanation': 'As fontes descrevem a mesma observação.',
        'treatment': 'combine', 'essential': False}], 'research_questions': []}


@pytest.mark.parametrize('schema,payload,valid', [
    (VideoContext, {'items': [{'id': 'k1'}], 'video_index': [{'id': 'k1'}, {'id': 'k2'}]},
        {'summary': 'Contexto', 'relations': [{'item_ids': ['k1', 'k2'], 'relation': 'sequence',
          'explanation': 'Ordem da fala.'}], 'gaps': []}),
    (TopicRouting, {'items': [{'id': 'k1'}, {'id': 'k2'}]},
        {'summary': 'Organização', 'topics': [{'topic': 'Observação', 'item_ids': ['k1', 'k2']}]}),
    (TopicComparison, {'items': [{'id': 'k1'}], 'topic_index': [{'id': 'k1'}, {'id': 'k2'}]},
        comparison(['k1', 'k2'])),
    (TopicPlan, {'items': [{'id': 'k1', 'check': {'status': 'supported'}},
                          {'id': 'k2', 'check': {'status': 'uncertain'}}]},
        {'summary': 'Plano', 'sections': [section(['k1'])], 'dispositions': [
            {'item_id': 'k1', 'status': 'used', 'reason': 'Responde à pergunta.'},
            {'item_id': 'k2', 'status': 'pending', 'reason': 'Requer conferência.'}]}),
    (PlanStructure, {'dispositions': [{'item_id': 'k1', 'status': 'used'},
                                    {'item_id': 'k2', 'status': 'pending'}]},
        {'main_question': 'O que observar?', 'title': 'Como observar', 'opening': 'Situe a pergunta.',
         'closing': 'Encerre a explicação.', 'ready_to_write': True, 'sections': [section(['k1'])], 'pending': []}),
    (DraftSection, {'section': {'item_ids': ['k1']}, 'items': [{'id': 'k1'}, {'id': 'k2'}]},
        {'markdown': 'Observe as folhas e confira o desenvolvimento na fonte.', 'used_item_ids': ['k1']}),
])
def test_each_task_restricts_references_to_its_received_inventory(schema, payload, valid):
    constrained = reference_contracts.scope(schema, schema, payload)
    assert constrained.model_validate(valid).model_dump() == schema.model_validate(valid).model_dump()
    bad = deepcopy(valid)
    list_key = next((key for key in ('relations', 'topics', 'rows', 'sections') if key in bad), None)
    if list_key:
        bad[list_key][0]['item_ids'] = ['source-id-from-other-stage']
    else:
        bad['used_item_ids'] = ['source-id-from-other-stage']
    with pytest.raises(ValidationError):
        constrained.model_validate(bad)
    fmt = type_to_text_format_param(constrained)
    assert fmt['strict'] and fmt['schema']['additionalProperties'] is False


def test_uncertain_information_is_available_for_disposition_but_not_for_writing():
    payload = {'items': [{'id': 'k1', 'check': {'status': 'uncertain'}}]}
    schema = reference_contracts.scope(TopicPlan, TopicPlan, payload)
    valid = {'summary': 'Informação pendente.', 'sections': [],
             'dispositions': [{'item_id': 'k1', 'status': 'pending', 'reason': 'Conferir o original.'}]}
    assert schema.model_validate(valid)
    valid['sections'] = [section(['k1'])]
    with pytest.raises(ValidationError):
        schema.model_validate(valid)


def test_empty_reference_inventory_allows_only_empty_list():
    schema = reference_contracts.scope(DraftSection, DraftSection, {'section': {'item_ids': []}})
    valid = {'markdown': 'Este fechamento não acrescenta informações factuais.', 'used_item_ids': []}
    assert schema.model_validate(valid)
    valid['used_item_ids'] = ['invented']
    with pytest.raises(ValidationError):
        schema.model_validate(valid)
    fmt = type_to_text_format_param(schema)
    assert fmt['schema']['properties']['used_item_ids']['maxItems'] == 0


def test_review_keeps_selected_quotes_and_restricts_passage_and_knowledge_ids():
    sources = {'v1s1': {'text': 'O autor observa o desenvolvimento das folhas.'}}
    wire, options = evidence_selection.prepare(PassageAudit, sources, sources)
    scoped = reference_contracts.scope(PassageAudit, wire,
        {'passages': [{'id': 'p1'}], 'items': [{'id': 'k1'}]})
    valid = {'summary': 'Conferência', 'assessments': [{'passage_id': 'p1', 'status': 'supported',
        'reason': 'A fala sustenta a observação.', 'evidence': [{'reference': 'e1'}], 'used_item_ids': ['k1']}]}
    parsed = scoped.model_validate(valid).model_dump()
    resolved = evidence_selection.resolve(parsed, PassageAudit, options)
    assert resolved['assessments'][0]['evidence'][0]['excerpt'] == sources['v1s1']['text']
    for key in ('passage_id', 'used_item_ids'):
        bad = deepcopy(valid)
        bad['assessments'][0][key] = 'k1' if key == 'passage_id' else ['p1']
        with pytest.raises(ValidationError):
            scoped.model_validate(bad)


def test_comparison_rejects_source_ids_over_real_sdk_and_retries_with_task_feedback(job, monkeypatch):
    from test_response_recovery import provider, response
    from app.editorial import engine, store
    engine.start(job, 'plan')
    payload = {'items': [{'id': 'k1'}], 'topic_index': [{'id': 'k1'}, {'id': 'k2'}]}
    good = comparison(['k1', 'k2'])
    wire = {**good, 'rows': {'k1': [{**{k:v for k,v in good['rows'][0].items() if k != 'item_ids'},
                                   'related_item_ids': ['k2']}]}}
    invalid = deepcopy(wire)
    invalid['rows']['k1'][0]['related_item_ids'] = ['v1s1']
    requests = provider(monkeypatch, [response(json.dumps(invalid)), response(json.dumps(wire))])
    actual = workflow.call(job, 'planner', TopicComparison, 'Compare as informações.', payload,
        'comparison:scoped', lambda result: workflow.known_ids(
            [ident for row in result['rows'] for ident in row['item_ids']], ['k1', 'k2'], 'Comparação'))
    assert actual == comparison(['k1', 'k2'])
    assert len(requests) == 2 and job['editorial']['calls'] == 2
    wire_schema = requests[0]['text']['format']['schema']['$defs']['OwnedComparison']
    assert wire_schema['properties']['related_item_ids']['items']['enum'] == ['k1', 'k2']
    assert {run['status'] for run in store.report(job)['runs']} == {'completed', 'failed'}


def test_known_and_exact_ids_do_not_relax_checks_after_scoping():
    with pytest.raises(generation.GenerationResponseError) as unknown:
        workflow.known_ids(['k1', 'k3'], ['k1', 'k2'], 'Comparação')
    assert unknown.value.reason == 'unknown_reference'
    for ids in (['k1'], ['k1', 'k2', 'k2']):
        with pytest.raises(generation.GenerationResponseError) as incomplete:
            workflow.exact_ids(ids, ['k1', 'k2'], 'Cobertura')
        assert incomplete.value.reason == 'coverage_mismatch'


def test_research_resolution_selects_only_received_issue_and_original_web_evidence():
    sources = {'wpage1s1': {'text': 'A fonte descreve as condições da observação.'}}
    wire, options = evidence_selection.prepare(ResearchResolution, sources, sources)
    scoped = reference_contracts.scope(ResearchResolution, wire, {'issues': [{'id': 'issue1'}]})
    valid = {'summary': 'Conferência da pesquisa.', 'answers': [{'issue_id': 'issue1', 'status': 'resolved',
        'reason': 'O original responde à condição que precisava de conferência.', 'evidence': [{'reference': 'e1'}]}]}
    parsed = scoped.model_validate(valid).model_dump()
    result = evidence_selection.resolve(parsed, ResearchResolution, options)
    workflow.validate_evidence(result['answers'], sources)
    assert result['answers'][0]['evidence'][0]['source_id'] == 'wpage1s1'
    invalid = deepcopy(valid)
    invalid['answers'][0]['issue_id'] = 'wpage1s1'
    with pytest.raises(ValidationError):
        scoped.model_validate(invalid)
    invalid = deepcopy(valid)
    invalid['answers'][0]['evidence'][0]['reference'] = 'unknown'
    with pytest.raises(ValidationError):
        scoped.model_validate(invalid)


def test_selected_evidence_context_sends_every_literal_character_once(job):
    text = 'Condição literal: observe as folhas apenas no método A. ' * 100 + 'ÚLTIMA RESSALVA.'
    sources = {'wpage1s1': {'text': text, 'url': 'https://example.org/original', 'title': 'Original'}}
    _, options = evidence_selection.prepare(ResearchResolution, sources, sources)
    job['sources'][0]['segments'] = [{'id': ident, 'text': value['text']} for ident, value in sources.items()]
    scope = {'role': 'fact_reviewer', 'context_sources': sources,
             'profile': {'profile': {'context_chars': 9000}, 'version': 'context-test'}}
    token = generation.agent_scope.set(scope)
    try:
        # Repeating sources and options would exceed this budget.
        with pytest.raises(ValueError, match='contexto'):
            generation.context(job, {'evidence_options': options})
        rendered = generation.context(job, {'evidence_options': options, '_selected_evidence': True})
        material = json.loads(rendered)
        assert ''.join(option['excerpt'] for option in material['evidence_options'].values()) == text
        meta = material['fontes_para_conferencia']['wpage1s1']
        assert meta['url'] == sources['wpage1s1']['url']
        assert meta['evidence_references'] == list(options)
        assert 'text' not in meta and '_selected_evidence' not in material
        assert len(rendered) < 9000 and sources['wpage1s1']['text'] == text
    finally:
        generation.agent_scope.reset(token)


def test_unrelated_contracts_do_not_require_editorial_identifiers():
    from app.schemas import Article
    assert reference_contracts.scope(Article, Article, {'items': [{'url': 'https://example.org'}]}) is Article


def test_reader_selects_literal_quoted_passage_over_actual_sdk(job,monkeypatch):
    from test_response_recovery import provider,response
    from app.editorial import agents, engine
    from app.editorial.contracts import Audit
    monkeypatch.setitem(agents.ROLES, 'fact_reviewer', {'name': 'Revisor factual', 'sector': 'quality', 'schema': Audit, 'prompt': 'Confira.'})
    text='A fonte descreve a observação como "delicada" e preserva uma condição específica.'
    job['article']['markdown']=text
    engine.start(job,'review')
    passages=engine.article_passages(job['article'])
    reference='p'+str(passages.index(text))
    finding={'severity':'warning','passage':reference,'reason':'A condição merece destaque.',
             'suggestion':'Preserve a condição.', 'source_ids':[],'rule_ids':[], 'recipient':'writing'}
    requests=provider(monkeypatch,[response(json.dumps({'summary':'Trecho examinado.','findings':[finding]}))])
    result,_=engine.invoke(job,'fact_reviewer',{'article':job['article']},slot='review-quoted-sdk')
    assert result['findings'][0]['passage']==text
    fmt=requests[0]['text']['format']['schema']
    assert fmt['$defs']['ScopedObservation']['properties']['passage']['enum']==[f'p{n}' for n in range(len(passages))]
    assert text not in json.dumps(fmt,ensure_ascii=False)
    material=json.loads(requests[0]['input'])
    assert material['equipe_editorial']['article_passage_refs'][reference]==text


def test_global_review_copies_quoted_title_and_passage_without_invented_sources(job,monkeypatch):
    from test_response_recovery import provider,response
    from app import generation
    from app.schemas import Review
    title='Observações com "aspas" e condições'
    text='A fonte usa o termo "observação" com uma ressalva.'
    job['article']['title']=title
    refs={'p0':'','p1':text}
    scope={'article_passages':list(refs.values()),'article_passage_refs':refs,
           'article_title':title,'context_sources':{}}
    wire={'evaluated_title':'article_title','summary':'Continuidade conferida.', 'findings':[],
          'editorial_alignment':{'matches_brief':True,'reason':'Tema preservado.','passage':'p1'},'supported_claims':[]}
    requests=provider(monkeypatch,[response(json.dumps(wire))])
    token=generation.agent_scope.set(scope)
    try:
        result=generation.structured(job,Review,'Revise a continuidade.','fact_reviewer',{'article':job['article']})
    finally:
        generation.agent_scope.reset(token)
    assert result['evaluated_title']==title
    assert result['editorial_alignment']['passage']==text
    fmt=requests[0]['text']['format']['schema']
    assert fmt['properties']['supported_claims']['maxItems']==0
    assert title not in json.dumps(fmt,ensure_ascii=False)
