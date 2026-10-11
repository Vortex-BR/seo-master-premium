"""Provider-visible briefing states with benign chair examples and no network."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError

from app import generation
from app.editorial import delivery_contracts, engine, evidence_selection, reference_contracts, video_first
from app.editorial.contracts import VideoFidelityReview


def prepare(payload):
    sources = {'v1s1': {'text': 'Confira o apoio da cadeira antes de ajustar.'}}
    wire, options = evidence_selection.prepare(VideoFidelityReview, sources, sources)
    wire = reference_contracts.scope(VideoFidelityReview, wire, payload)
    wire, adapter = delivery_contracts.prepare(VideoFidelityReview, wire, payload, sources,
                                              evidence_options=options)
    return wire, adapter, options


def assessment(**patch):
    return {'status': 'met', 'reason': 'Atendido no artigo.', 'passage_ids': ['p1'],
            'correction': '', 'excluded_source_ids': [], 'excluded_item_ids': [], **patch}


@pytest.fixture
def wire_case():
    payload = {'passages': [
        {'id': 'p1', 'field': 'markdown', 'text': 'Confira o apoio da cadeira antes de ajustar.'},
        {'id': 'p2', 'field': 'markdown', 'text': '## Ajuste da cadeira'},
        {'id': 'p3', 'field': 'title', 'text': 'Ajuste da cadeira'},
    ], '_body_development_ids': ['p1'], 'source_inventory': ['v1s1'],
        'items': [{'id': 'i1'}], 'brief_requirements': [
            {'id': 'b1', 'field': 'topic', 'text': 'Ajuste da cadeira.'},
            {'id': 'b2', 'field': 'main_question', 'text': 'O que conferir antes do ajuste?'},
            {'id': 'b3', 'field': 'exclusions', 'text': 'Não citar marcas.'},
            {'id': 'b4', 'field': 'instructions', 'text': 'Explique a preparação.'},
            {'id': 'b5', 'field': 'main_question', 'text': 'Como preparar a cadeira?'},
        ]}
    wire, adapter, options = prepare(payload)
    required = wire.model_fields['brief_assessments'].annotation
    valid = {row['id']: assessment() for row in payload['brief_requirements']}
    return payload, wire, adapter, options, required, valid


@pytest.mark.parametrize('ident', ['b1', 'b2', 'b4', 'b5'])
def test_met_nonexclusion_requires_a_passage(wire_case, ident):
    *_, required, valid = wire_case
    valid[ident]['passage_ids'] = []
    with pytest.raises(ValidationError):
        required.model_validate(valid)


@pytest.mark.parametrize('passages', [['p2'], ['p3'], ['p1', 'p2'], ['foreign']])
def test_met_question_selects_only_actual_body_passages(wire_case, passages):
    *_, required, valid = wire_case
    valid['b2']['passage_ids'] = passages
    with pytest.raises(ValidationError):
        required.model_validate(valid)


def test_question_without_a_body_cannot_claim_met(wire_case):
    payload = deepcopy(wire_case[0])
    payload['_body_development_ids'] = []
    wire, _, _ = prepare(payload)
    required = wire.model_fields['brief_assessments'].annotation
    valid = deepcopy(wire_case[-1])
    with pytest.raises(ValidationError):
        required.model_validate(valid)
    for ident in ('b2', 'b5'):
        valid[ident] = assessment(status='missing', passage_ids=[], correction='Desenvolva a resposta no corpo.')
    required.model_validate(valid)


@pytest.mark.parametrize('ident', ['b1', 'b2', 'b3'])
@pytest.mark.parametrize('patch', [
    {'passage_ids': [], 'correction': 'Corrija o trecho identificado.'},
    {'passage_ids': ['p1'], 'correction': ''},
    {'passage_ids': ['p1'], 'correction': ' \n '},
])
def test_violated_requires_a_real_passage_and_visible_correction(wire_case, ident, patch):
    *_, required, valid = wire_case
    valid[ident] = assessment(status='violated', **patch)
    with pytest.raises(ValidationError):
        required.model_validate(valid)


@pytest.mark.parametrize('ident', ['b1', 'b2', 'b3'])
@pytest.mark.parametrize('status', ['partial', 'missing'])
@pytest.mark.parametrize('correction', ['', ' \n '])
def test_incomplete_requires_a_visible_correction(wire_case, ident, status, correction):
    *_, required, valid = wire_case
    valid[ident] = assessment(status=status, passage_ids=[], correction=correction)
    with pytest.raises(ValidationError):
        required.model_validate(valid)


@pytest.mark.parametrize('ident,status', [
    (ident, status) for ident in ('b1', 'b2', 'b3')
    for status in ('met', 'violated', 'partial', 'missing', 'conflict', 'uncertain')
    if (ident, status) != ('b3', 'met')
])
@pytest.mark.parametrize('field,value', [('excluded_source_ids', ['v1s1']), ('excluded_item_ids', ['i1'])])
def test_only_met_exclusions_can_exclude_sources_or_items(wire_case, ident, status, field, value):
    *_, required, valid = wire_case
    valid[ident] = assessment(status=status, correction='Confira a exigência usando a fonte.', **{field: value})
    with pytest.raises(ValidationError):
        required.model_validate(valid)


@pytest.mark.parametrize('status', ['met', 'violated', 'partial', 'missing', 'conflict', 'uncertain'])
def test_coherent_states_still_dump_plain_lists_and_objects(wire_case, status):
    *_, required, valid = wire_case
    for ident in valid:
        valid[ident] = assessment(status=status,
                                 correction='Corrija usando a fonte.' if status in ('violated', 'partial', 'missing') else '')
    valid['b3']['passage_ids'] = [] if status != 'violated' else ['p2']
    if status == 'met':
        valid['b3'].update(excluded_source_ids=['v1s1'], excluded_item_ids=['i1'])
    assert required.model_validate(valid).model_dump() == valid


def test_sdk_strict_schema_shares_requirement_classes_and_roundtrips(wire_case):
    payload, wire, adapter, options, required, valid = wire_case
    fmt = type_to_text_format_param(wire)
    definitions = fmt['schema']['$defs']
    owned = definitions['RequiredBriefAssessments']
    assert owned['required'] == [row['id'] for row in payload['brief_requirements']]
    assert owned['properties']['b1'] == owned['properties']['b4']
    assert owned['properties']['b2'] == owned['properties']['b5']
    assert all('$ref' in row for row in owned['properties'].values())

    def strict_objects(value):
        if isinstance(value, dict):
            if value.get('type') == 'object':
                assert value['additionalProperties'] is False
                assert set(value['required']) == set(value['properties'])
            for child in value.values():
                strict_objects(child)
        elif isinstance(value, list):
            for child in value:
                strict_objects(child)
    strict_objects(fmt['schema'])
    raw = {'summary': 'Conferido.', 'editorial_alignment': {'matches_brief': True, 'reason': 'Atendido.', 'passage': ''},
        'preservation': None, 'brief_assessments': valid,
        'assessments': {part['id']: {'status': 'not_factual', 'reason': 'Exemplo de formato.',
            'evidence': [], 'used_item_ids': []} for part in payload['passages']},
        'source_assessments': {'v1s1': {'status': 'complete', 'reason': 'Desenvolvido.',
            'passage_ids': ['p1'], 'missing_details': []}},
        'source_details': {key: [{'detail': option['excerpt'], 'status': 'covered',
            'passage_ids': ['p1'], 'reason': 'Desenvolvido.'}] for key, option in options.items()}}
    resolved = delivery_contracts.resolve(wire.model_validate(raw).model_dump(), adapter)
    assert resolved['brief_assessments'] == [{'requirement_id': ident, **row} for ident, row in valid.items()]
    assert resolved['source_assessments'][0]['detail_assessments'][0]['excerpt'] == options['e1']['excerpt']
    assert 'source_details' not in resolved


def test_legacy_delivery_has_no_new_brief_state_encoder(wire_case):
    payload = deepcopy(wire_case[0])
    payload.pop('brief_requirements')
    wire, adapter, _ = prepare(payload)
    fmt = type_to_text_format_param(wire)
    assert not adapter.get('requirement_ids')
    assert 'RequiredBriefAssessments' not in fmt['schema'].get('$defs', {})
    assert 'BriefVerdict' not in fmt['schema'].get('$defs', {})


def test_covered_source_detail_requires_real_body_evidence(wire_case):
    wire, _, options = prepare(wire_case[0])
    required = wire.model_fields['source_details'].annotation
    missing = {key: [{'detail': option['excerpt'], 'status': 'missing',
        'passage_ids': [], 'reason': 'Detalhe ausente.'}] for key, option in options.items()}
    required.model_validate(missing)
    covered = deepcopy(missing)
    covered[next(iter(covered))][0]['status'] = 'covered'
    with pytest.raises(ValidationError):
        required.model_validate(covered)


def test_ninety_original_sources_fit_default_context_without_network(job, monkeypatch):
    job['brief'].update(topic='Cadeira', keyword='cadeira', main_question='Como conferir a cadeira?',
                        exclusions='Não citar marcas.', instructions='')
    source = job['sources'][0]
    source.update(title='Ajuste da cadeira', author='Autor de exemplo')
    source['segments'] = [{'id': f'v1s{n}', 'start': n * 10, 'end': n * 10 + 9,
        'text': f'Na fonte 1, a observação {n} descreve a cadeira com atenção ao método local. '
                'Detalhes da observaç'} for n in range(1, 91)]
    job['article'].update(title='Como conferir a cadeira', seo_title='Como conferir a cadeira', slug='cadeira',
        meta_description='Confira o apoio da cadeira.', excerpt='Ajuste descrito pelo criador.', tags=['cadeira'],
        markdown='No vídeo, Autor de exemplo explica o ajuste.\n\n## Conferência da cadeira\n\n' + '\n\n'.join(
            segment['text'] + f' [[{segment["id"]}]]' for segment in source['segments']))
    engine.start(job, 'review')
    assert job['editorial']['profile']['profile']['context_chars'] == 90000
    payload, _, _ = video_first.build_review_payload(job, job['article'])
    scope, _, _ = engine.invocation_inputs(job, 'fact_reviewer', payload, None, 'wire-state-budget')
    network = Mock(side_effect=AssertionError('No provider call'))
    monkeypatch.setattr(generation, 'client', network)
    token = generation.agent_scope.set(scope)
    try:
        request, *_ = generation.prepare_structured(job, VideoFidelityReview,
            video_first.review_instruction(), 'fact_reviewer', payload)
    finally:
        generation.agent_scope.reset(token)
    assert request['text']['format']['name'] == 'EditorialDeliveryV1'
    assert len(request['instructions']) + len(request['input']) + len(
        json.dumps(request['text']['format'], ensure_ascii=False, separators=(',', ':'))) <= 90000
    network.assert_not_called()
