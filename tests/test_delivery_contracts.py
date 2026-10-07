from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from openai.lib._parsing._responses import type_to_text_format_param

from app.editorial import delivery_contracts, evidence_selection, reference_contracts, workflow
from app.editorial.contracts import PassageAudit, PlanStructure, ResearchResolution, TopicComparison, TopicPlan, TopicRouting


def prepare(original, payload, sources=None):
    sources = sources or {}
    wire, options = evidence_selection.prepare(original, sources, sources)
    wire = reference_contracts.scope(original, wire, payload)
    wire, adapter = delivery_contracts.prepare(original, wire, payload)
    return wire, adapter, options


def test_every_one_of_48_topics_is_required_including_last_item():
    ids = [f'k{i}' for i in range(48)]
    schema, adapter, _ = prepare(TopicRouting, {'items': [{'id': ident} for ident in ids]})
    valid = {'summary': 'Classificação completa.', 'catalog': {f't{i}': 'observação' if i == 1 else None for i in range(1,9)},
             'topics': {ident: {'category': 't1'} for ident in ids}}
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    assert result['topics'] == [{'topic': 'observação', 'item_ids': ids}]
    bad = deepcopy(valid)
    bad['topics'].pop(ids[-1])
    with pytest.raises(ValidationError):
        schema.model_validate(bad)
    fmt = type_to_text_format_param(schema)
    assert fmt['schema']['$defs']['RequiredTopics']['required'] == ids
    assert fmt['schema']['$defs']['RequiredTopics']['additionalProperties'] is False


def test_comparisons_cover_owned_items_and_allow_received_distant_counterparts():
    schema, adapter, _ = prepare(TopicComparison, {'items': [{'id': 'k1'}, {'id': 'k2'}],
        'topic_index': [{'id': 'k1'}, {'id': 'k2'}, {'id': 'k3'}]})
    row = {'related_item_ids': ['k3'], 'relation': 'different_methods', 'explanation': 'Métodos diferentes.',
           'treatment': 'keep_separate', 'essential': False}
    valid = {'summary': 'Métodos preservados.', 'rows': {'k1': [row], 'k2': [{**row, 'related_item_ids': []}]},
             'research_questions': []}
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    assert result['rows'][0]['item_ids'] == ['k1', 'k3']
    assert result['rows'][1]['item_ids'] == ['k2']
    assert result['rows'][0]['treatment'] == 'keep_separate'
    workflow.known_ids([i for row in result['rows'] for i in row['item_ids']], ['k1','k2','k3'], 'Comparação')
    for replacement in (None, []):
        bad = deepcopy(valid)
        if replacement is None:
            bad['rows'].pop('k2')
        else:
            bad['rows']['k2'] = replacement
        with pytest.raises(ValidationError):
            schema.model_validate(bad)


@pytest.mark.parametrize('original,field,identifier,input_key', [
    (TopicPlan, 'dispositions', 'item_id', 'items'),
    (PassageAudit, 'assessments', 'passage_id', 'passages'),
    (ResearchResolution, 'answers', 'issue_id', 'issues'),
])
def test_required_deliveries_preserve_meaning_and_never_fill_missing_entries(original, field, identifier, input_key):
    payload = {input_key: [{'id': 'id1', 'check': {'status': 'supported'}},
                          {'id': 'id2', 'check': {'status': 'supported'}}]}
    if original is PassageAudit:
        payload['items'] = [{'id': 'k1'}]
    sources = {'s1': {'text': 'Uma observação literal com uma condição específica.'}}
    schema, adapter, options = prepare(original, payload, sources)
    if original is TopicPlan:
        content = {'status': 'pending', 'reason': 'Ainda falta conferir uma condição.'}
        extra = {'sections': []}
    elif original is PassageAudit:
        content = {'status': 'uncertain', 'reason': 'O trecho depende de contexto visual.',
                   'evidence': [{'reference': 'e1'}], 'used_item_ids': []}
        extra = {}
    else:
        content = {'status': 'unresolved', 'reason': 'A condição indispensável ainda não aparece na fonte.',
                   'evidence': [{'reference': 'e1'}]}
        extra = {}
    valid = {'summary': 'Situações preservadas.', field: {'id1': content, 'id2': content}, **extra}
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    if options:
        result = evidence_selection.resolve(result, original, options)
    else:
        result = original.model_validate(result).model_dump()
    assert [item[identifier] for item in result[field]] == ['id1', 'id2']
    assert all(item['status'] == content['status'] for item in result[field])
    bad = deepcopy(valid)
    del bad[field]['id2']
    with pytest.raises(ValidationError):
        schema.model_validate(bad)


def test_topic_routing_recovery_uses_required_ids_over_actual_sdk(job, monkeypatch):
    from test_response_recovery import provider, response
    from app.editorial import engine
    engine.start(job, 'plan')
    catalog = {f't{i}': 'observação' if i == 1 else None for i in range(1,9)}
    incomplete = {'summary': 'Faltou um item.', 'catalog': catalog, 'topics': {'k1': {'category': 't1'}}}
    complete = {'summary': 'Todos os itens.', 'catalog': catalog, 'topics': {'k1': {'category': 't1'}, 'k2': {'category': 't1'}}}
    requests = provider(monkeypatch, [response(json.dumps(incomplete)), response(json.dumps(complete))])
    result = workflow.call(job, 'planner', TopicRouting, 'Classifique todos os itens.',
        {'items': [{'id': 'k1'}, {'id': 'k2'}]}, 'routing-sdk',
        lambda result: workflow.exact_ids([i for group in result['topics'] for i in group['item_ids']], ['k1','k2'], 'Assuntos'))
    assert result['topics'][0]['item_ids'] == ['k1', 'k2']
    assert len(requests) == 2 and job['editorial']['calls'] == 2
    assert requests[0]['text']['format']['schema']['$defs']['RequiredTopics']['required'] == ['k1', 'k2']


def test_later_topic_batches_use_existing_catalog_and_keep_all_items():
    schema, adapter, _ = prepare(TopicRouting, {'items': [{'id': 'k2'}], 'catalog': ['observação', 'condições']})
    valid = {'summary': 'Mesmas famílias.', 'catalog': {f't{i}': 'condições' if i == 1 else None for i in range(1,9)},
             'topics': {'k2': {'category': 't1'}}}
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    assert result['topics'] == [{'topic': 'condições', 'item_ids': ['k2']}]
    assert result['catalog'] == ['observação', 'condições']
    invalid = deepcopy(valid)
    invalid['catalog']['t1'] = 'novo microtema'
    with pytest.raises(ValidationError):
        schema.model_validate(invalid)
    invalid = deepcopy(valid)
    invalid['topics']['k2']['category'] = 't2'
    from app.generation import GenerationResponseError
    with pytest.raises(GenerationResponseError):
        delivery_contracts.resolve(schema.model_validate(invalid).model_dump(), adapter)


def plan_delivery(ids):
    section = {'id': 's1', 'title': 'Observações', 'question': 'O que a fonte observa?',
               'purpose': 'Explicar as observações.', 'prerequisites': [], 'conditions': [],
               'transition': 'As condições encerram a explicação.', 'pending': []}
    return {'main_question': 'O que a fonte observa?', 'title': 'Observações das fontes',
            'opening': 'Situar o tema.', 'closing': 'Explicar os limites.', 'ready_to_write': True,
            'sections': [section], 'pending': [], 'assignments': {ident: ['s1'] for ident in ids}}


def test_consolidation_preserves_all_48_used_items_and_rejects_missing_sections():
    ids = [f'k{i}' for i in range(48)]
    payload = {'dispositions': [{'item_id': ident, 'status': 'used'} for ident in ids]}
    schema, adapter, _ = prepare(PlanStructure, payload)
    valid = plan_delivery(ids)
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    assert result['sections'][0]['item_ids'] == ids
    assert PlanStructure.model_validate(result).model_dump() == result
    fmt = type_to_text_format_param(schema)
    assert fmt['schema']['$defs']['RequiredSectionAssignments']['required'] == ids
    assert 'item_ids' not in fmt['schema']['$defs']['NamedAssignedSectionPlan']['properties']
    invalid = deepcopy(valid)
    del invalid['assignments'][ids[-1]]
    with pytest.raises(ValidationError):
        schema.model_validate(invalid)
    from app.generation import GenerationResponseError
    invalid = deepcopy(valid)
    invalid['assignments'][ids[-1]] = ['s2']
    with pytest.raises(GenerationResponseError) as error:
        delivery_contracts.resolve(schema.model_validate(invalid).model_dump(), adapter)
    assert error.value.reason == 'unknown_reference'
    invalid = deepcopy(valid)
    invalid['sections'].append(deepcopy(invalid['sections'][0]))
    with pytest.raises(GenerationResponseError) as error:
        delivery_contracts.resolve(schema.model_validate(invalid).model_dump(), adapter)
    assert error.value.reason == 'coverage_mismatch'


def test_consolidation_required_assignments_over_actual_sdk(job, monkeypatch):
    from test_response_recovery import provider, response
    from app.editorial import engine
    engine.start(job, 'plan')
    valid = plan_delivery(['k1','k2'])
    invalid = deepcopy(valid)
    invalid['assignments'].pop('k2')
    requests = provider(monkeypatch, [response(json.dumps(invalid)), response(json.dumps(valid))])
    result = workflow.call(job, 'planner', PlanStructure, 'Consolide as seções.',
        {'dispositions': [{'item_id': 'k1','status': 'used'}, {'item_id': 'k2','status': 'used'}]},
        'consolidation-sdk', lambda result: workflow.exact_ids(
            [i for s in result['sections'] for i in s['item_ids']], ['k1','k2'], 'Plano'))
    assert result['sections'][0]['item_ids'] == ['k1','k2']
    assert len(requests) == 2


def test_consolidation_requires_explicit_priority_without_resolving_comparison():
    pending = {'id':'i1', 'origin':'comparison', 'essential':True}
    schema, adapter, _ = prepare(PlanStructure, {'dispositions':[{'item_id':'k1','status':'used'}], 'pending':[pending]})
    valid = plan_delivery(['k1'])
    valid['issue_priorities'] = {'i1': {'essential': False,
        'reason': 'A comparação é complementar; o plano explica os métodos separadamente.'}}
    result = delivery_contracts.resolve(schema.model_validate(valid).model_dump(), adapter)
    assert result['issue_priorities'] == [{'issue_id':'i1', **valid['issue_priorities']['i1']}]
    invalid = deepcopy(valid)
    invalid['issue_priorities'] = {}
    with pytest.raises(ValidationError):
        schema.model_validate(invalid)
