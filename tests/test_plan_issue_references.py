"""Plan priorities are overrides of open comparison issues, never source IDs."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app import generation
from app.editorial import delivery_contracts, guidance, reference_contracts, workflow
from app.editorial.contracts import ArticlePlan, EditorialPlan, PlanStructure


def issue(ident, origin='comparison', status='open', *, essential=False):
    return {'id': ident, 'origin': origin, 'status': status, 'essential': essential,
            'reason': 'A condição ainda precisa de conferência nas fontes.', 'source_ids': ['v1s1']}


def plan_case(job, pending):
    current = deepcopy(job)
    item = {'id': 'v1i1', 'video_id': 'v1', 'topic': 'observação',
            'statement': 'O autor observa o desenvolvimento das folhas.',
            'check': {'status': 'supported', 'reason': 'A fala sustenta a observação.'},
            'evidence': [{'source_id': 'v1s1', 'excerpt': current['sources'][0]['segments'][0]['text']}]}
    current['apuration'] = {'items': [item], 'videos': [], 'comparisons': [], 'pending': deepcopy(pending)}
    payload = {'items': [item], 'dispositions': [{'item_id': 'v1i1', 'status': 'used',
               'reason': 'A explicação responde à pergunta.'}],
               'source_guidance': guidance.source_guide(current), 'pending': deepcopy(pending),
               '_context_sources': generation.evidence_map(current)}
    return current, payload


def prepared(job, pending, original, monkeypatch):
    current, payload = plan_case(job, pending)
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('No provider call allowed'))
    token = generation.agent_scope.set({'context_sources': payload['_context_sources'],
                                        'role': 'planner', 'max_output_tokens': 8000})
    try:
        request, wire, _, _, _, adapter = generation.prepare_structured(
            current, original, 'Planeje usando apenas as referências recebidas.', 'planner', payload)
    finally:
        generation.agent_scope.reset(token)
    return current, payload, request, wire, adapter


def raw_plan(original, priorities):
    section = {'id': 's1', 'title': 'Observação', 'question': 'O que observar?',
               'purpose': 'Explicar a observação descrita pelo autor.', 'prerequisites': [],
               'conditions': [], 'transition': '', 'pending': [],
               'presentation': {'mode': 'explanation', 'reason': 'Uma explicação contínua em parágrafos.',
                                'subheadings': []}}
    result = {'main_question': 'Como observar as folhas?', 'title': 'Como observar as folhas',
              'opening': 'Situe a observação do autor.', 'closing': 'Encerre a explicação.',
              'ready_to_write': True, 'sections': [section], 'pending': [],
              'issue_priorities': priorities,
              'reader_journey': {'kind': 'explicativo', 'goal': 'Compreender a observação das folhas.',
                                'reason': 'A fala original descreve a observação que responde à pergunta.',
                                'video_item_ids': ['v1i1']}}
    if original is EditorialPlan:
        section['item_ids'] = ['v1i1']
        result.update(research_questions=[], dispositions={
            'v1i1': {'status': 'used', 'reason': 'A explicação responde à pergunta.'}})
    else:
        result['assignments'] = {'v1i1': ['s1']}
    return result


def domain_result(wire, adapter, raw, payload):
    resolved = delivery_contracts.resolve(wire.model_validate_json(json.dumps(raw)).model_dump(), adapter)
    # The partitioned planner adds its previously evaluated dispositions after consolidation.
    if adapter['field'] == 'assignments':
        resolved['dispositions'] = deepcopy(payload['dispositions'])
    return resolved


def assert_strict_objects(value):
    if isinstance(value, dict):
        if value.get('type') == 'object':
            assert value['additionalProperties'] is False
            assert set(value['required']) == set(value.get('properties', {}))
        for child in value.values():
            assert_strict_objects(child)
    elif isinstance(value, list):
        for child in value:
            assert_strict_objects(child)


@pytest.mark.parametrize('original', [EditorialPlan, PlanStructure])
@pytest.mark.parametrize('pending', [
    [],
    [issue(f'transcription-{n}', 'transcription') for n in range(5)],
    [issue('closed-comparison', status='resolved')],
    [issue('dismissed-comparison', status='dismissed')],
    [issue('knowledge-gap', 'knowledge')],
], ids=['empty', 'five-transcription-issues', 'resolved', 'dismissed', 'other-origin'])
def test_actual_plan_request_forbids_priorities_without_open_comparisons(job, original, pending, monkeypatch):
    current, payload, request, wire, adapter = prepared(job, pending, original, monkeypatch)
    before = deepcopy(current)
    schema = request['text']['format']['schema']
    assert request['text']['format']['strict'] is True
    assert schema['properties']['issue_priorities']['type'] == 'array'
    assert schema['properties']['issue_priorities']['maxItems'] == 0
    assert 'Alertas de transcrição permanecem em pending' in request['instructions']
    assert 'Se não há comparação aberta' in request['instructions']
    assert_strict_objects(schema)
    assert json.loads(request['input'])['pending'] == pending
    valid = raw_plan(original, [])
    resolved = domain_result(wire, adapter, valid, payload)
    assert workflow.validate_plan(current, resolved)['issue_priorities'] == []
    legacy = deepcopy(valid)
    del legacy['issue_priorities']
    assert wire.model_validate(legacy).issue_priorities == []
    assert current == before  # A plan does not close or remove source issues.
    for ident in ('v1s1', 'v1i1', 'invented', *(row['id'] for row in pending)):
        invalid = deepcopy(valid)
        invalid['issue_priorities'] = [{'issue_id': ident, 'essential': False,
                                      'reason': 'Esta referência não é uma pendência elegível desta etapa.'}]
        with pytest.raises(ValidationError):
            wire.model_validate_json(json.dumps(invalid))


@pytest.mark.parametrize('original', [EditorialPlan, PlanStructure])
def test_actual_plan_request_scopes_priorities_to_open_comparison_inventory(job, original, monkeypatch):
    pending = [issue('comparison-a', essential=True), issue('transcription-a', 'transcription'),
               issue('comparison-b'), issue('old-comparison', status='resolved'), issue('knowledge-a', 'knowledge')]
    current, payload, request, wire, adapter = prepared(job, pending, original, monkeypatch)
    schema = request['text']['format']['schema']
    assert_strict_objects(schema)
    priorities = [{'issue_id': ident, 'essential': False,
                   'reason': 'A comparação é complementar; os métodos serão explicados separadamente.'}
                  for ident in ('comparison-a', 'comparison-b')]
    if original is EditorialPlan:
        priority_schema = schema['$defs']['TaskPlanIssuePriority']['properties']['issue_id']
        assert priority_schema['enum'] == ['comparison-a', 'comparison-b']
        raw_priorities = priorities
    else:
        priority_schema = schema['$defs']['RequiredIssuePriorities']
        assert priority_schema['required'] == ['comparison-a', 'comparison-b']
        assert set(priority_schema['properties']) == {'comparison-a', 'comparison-b'}
        raw_priorities = {row['issue_id']: {k: v for k, v in row.items() if k != 'issue_id'} for row in priorities}
    before = deepcopy(current)
    resolved = domain_result(wire, adapter, raw_plan(original, raw_priorities), payload)
    plan = workflow.validate_plan(current, resolved)
    assert plan['issue_priorities'] == priorities
    assert plan['ready_to_write'] is True
    assert plan['sections'][0]['item_ids'] == ['v1i1']
    assert current == before
    assert current['apuration']['pending'][0]['essential'] is True
    assert current['apuration']['pending'][0]['status'] == 'open'
    for ident in ('transcription-a', 'old-comparison', 'knowledge-a', 'v1s1', 'invented'):
        invalid = raw_plan(original, deepcopy(raw_priorities))
        foreign = {'essential': False, 'reason': 'A referência está fora das pendências elegíveis do plano.'}
        if original is EditorialPlan:
            invalid['issue_priorities'].append({'issue_id': ident, **foreign})
        else:
            invalid['issue_priorities'][ident] = foreign
        with pytest.raises(ValidationError):
            wire.model_validate_json(json.dumps(invalid))


def test_partitioned_plan_still_requires_every_open_comparison_priority(job, monkeypatch):
    _, payload, _, wire, adapter = prepared(job, [issue('comparison-a'), issue('comparison-b')],
                                           PlanStructure, monkeypatch)
    assessment = {'essential': False, 'reason': 'A comparação é complementar ao escopo deste artigo.'}
    valid = raw_plan(PlanStructure, {'comparison-a': assessment, 'comparison-b': assessment})
    resolved = domain_result(wire, adapter, valid, payload)
    assert [row['issue_id'] for row in resolved['issue_priorities']] == ['comparison-a', 'comparison-b']
    for ident in ('comparison-a', 'comparison-b'):
        invalid = deepcopy(valid)
        del invalid['issue_priorities'][ident]
        with pytest.raises(ValidationError):
            wire.model_validate_json(json.dumps(invalid))


def test_priority_override_does_not_downgrade_an_essential_transcription_issue(job, monkeypatch):
    pending = [issue('comparison-a', essential=True), issue('transcription-a', 'transcription', essential=True)]
    current, payload, _, wire, adapter = prepared(job, pending, EditorialPlan, monkeypatch)
    priorities = [{'issue_id': 'comparison-a', 'essential': False,
                   'reason': 'Os métodos podem ser explicados separadamente sem comparação.'}]
    resolved = domain_result(wire, adapter, raw_plan(EditorialPlan, priorities), payload)
    plan = workflow.validate_plan(current, resolved)
    assert plan['ready_to_write'] is False
    assert current['apuration']['pending'] == pending


@pytest.mark.parametrize('original', [EditorialPlan, PlanStructure])
def test_server_keeps_rejecting_foreign_priorities_after_wire_protection(job, original, monkeypatch):
    current, payload, _, wire, adapter = prepared(job, [], original, monkeypatch)
    resolved = domain_result(wire, adapter, raw_plan(original, []), payload)
    resolved['issue_priorities'] = [{'issue_id': 'v1s1', 'essential': False,
                                   'reason': 'O trecho de origem não é uma pendência de comparação.'}]
    # Legacy/manual domain input remains readable, but no invalid priority is silently removed.
    assert ArticlePlan.model_validate(resolved).issue_priorities[0].issue_id == 'v1s1'
    before = deepcopy(current)
    with pytest.raises(generation.GenerationResponseError) as error:
        workflow.validate_plan(current, resolved)
    assert error.value.reason == 'unknown_reference'
    assert error.value.diagnostics == [{'type': 'unknown_reference', 'reference_scope': 'Prioridade do plano'}]
    assert current == before


def test_priority_inventory_requires_explicit_open_state_and_preserves_input():
    pending = [issue('open-comparison'), issue('resolved-comparison', status='resolved'),
               issue('transcription', 'transcription'), {'id': 'legacy-unknown-state', 'origin': 'comparison'}]
    before = deepcopy(pending)
    assert reference_contracts.priority_ids({'pending': pending}) == ('open-comparison',)
    assert pending == before
