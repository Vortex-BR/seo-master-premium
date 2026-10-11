"""Offline extraction integrity using only synthetic cooking/furniture sources."""
from copy import deepcopy

import pytest

from app import db, generation
from app.editorial import source_processing, store, video_first, workflow
from app.editorial.contracts import SpokenExtraction, VoiceProfile


def insight(text, source_ids, **details):
    return {'topic': 'Preparação', 'spoken_explanation': text,
            'practical_tips': [], 'analogies': [], 'warnings': [],
            'source_segment_ids': source_ids, **details}


def extraction(*videos):
    return {'summary': 'Detalhes concretos dos criadores.', 'videos': list(videos)}


def spoken_video(video_id, *insights):
    return {'video_id': video_id, 'summary': 'Explicação da fonte.',
            'insights': list(insights), 'gaps': []}


@pytest.fixture
def cooking_job():
    return {'id': 'synthetic-source-detail', 'brief': {'topic': 'Preparo do arroz'},
            'editorial': {'profile': {'profile': VoiceProfile().model_dump()}},
            'sources': [{'id': 'v1', 'url': 'https://example.test/cooking',
                         'title': 'Arroz na panela', 'author': 'Criadora de exemplo', 'status': 'ok',
                         'segments': [
                             {'id': 'v1s1', 'start': 0, 'end': 20,
                              'text': 'Eu uso uma xícara de arroz e duas xícaras de água. '
                                      'Lavo o arroz antes de levar à panela, para tirar o excesso de amido.'},
                             {'id': 'v1s2', 'start': 20, 'end': 30,
                              'text': 'Depois da fervura, baixo o fogo e espero 15 minutos. '
                                      'Esse tempo vale para esta panela com a tampa fechada.'},
                             {'id': 'v1s3', 'start': 30, 'end': 35,
                              'text': 'Para arroz integral, esse tempo não vale.'},
                             {'id': 'v1s4', 'start': 35, 'end': 38,
                              'text': 'Deixe o like! Se inscreve no canal!'}]}]}


@pytest.fixture
def offline_extract(monkeypatch):
    calls, saved = [], []
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('Provider calls are forbidden'))
    monkeypatch.setattr(db, 'save_job', lambda job: saved.append(deepcopy(job)))
    monkeypatch.setattr(store, 'issues', lambda job: [])
    monkeypatch.setattr(store, 'artifact', lambda job, kind, scope, data, deps:
                        {'version': 'synthetic-version', 'dependencies': deps})
    monkeypatch.setattr(workflow, 'dependencies', lambda job: {})
    monkeypatch.setattr(source_processing, 'estimate', lambda job, profile: {'estimated_calls_min': 4})

    def run(job, output):
        def deliver(current, role, schema, instruction, payload, slot, validate):
            calls.append({'role': role, 'schema': schema, 'payload': deepcopy(payload)})
            result = schema.model_validate(deepcopy(output)).model_dump()
            validate(result)
            return result

        monkeypatch.setattr(workflow, 'call', deliver)
        return video_first.extract(job)

    return run, calls, saved


def test_legacy_saved_extraction_does_not_invent_structured_details():
    legacy = extraction(spoken_video('v1', insight('Eu lavo o arroz.', ['v1s1'])))
    parsed = SpokenExtraction.model_validate(legacy)
    original = parsed.videos[0].insights[0]
    assert original.kind is None and original.information_type is None
    assert original.method == ''
    assert original.conditions == original.quantities == original.restrictions == []
    assert original.reasons == original.sequence == []
    assert original.spoken_explanation == 'Eu lavo o arroz.'
    assert SpokenExtraction.model_validate(parsed.model_dump()) == parsed


def test_extraction_preserves_concrete_details_types_and_original_evidence(cooking_job, offline_extract):
    run, calls, saved = offline_extract
    originals = deepcopy(cooking_job['sources'])
    output = extraction(spoken_video('v1',
        insight('Eu lavo uma xícara de arroz antes de usar duas xícaras de água, para tirar o excesso de amido.',
                ['v1s1'], kind='experiência', information_type='procedimento',
                method='Lavar o arroz antes de levar à panela.', conditions=['Antes de levar à panela.'],
                quantities=[{'value': 'uma', 'unit': 'xícara', 'context': 'Arroz.'},
                            {'value': 'duas', 'unit': 'xícaras', 'context': 'Água para uma xícara de arroz.'}],
                reasons=['Tirar o excesso de amido.'], sequence=['Lavar o arroz.', 'Levar à panela.']),
        insight('Depois da fervura, baixo o fogo e espero 15 minutos nesta panela com a tampa fechada; '
                'esse tempo não vale para arroz integral.', ['v1s2', 'v1s3'],
                kind='experiência', information_type='procedimento', method='Cozinhar em fogo baixo.',
                conditions=['Nesta panela.', 'Com a tampa fechada.'],
                quantities=[{'value': '15', 'unit': 'minutos', 'context': 'Depois da fervura, em fogo baixo.'}],
                restrictions=['Esse tempo não vale para arroz integral.'],
                warnings=['Esse tempo não vale para arroz integral.'],
                sequence=['Esperar a fervura.', 'Baixar o fogo.', 'Esperar 15 minutos.'])))
    result = run(cooking_job, output)
    assert cooking_job['sources'] == originals
    assert len(result['items']) == 2
    for actual, expected in zip(result['items'], output['videos'][0]['insights']):
        for key in ('kind', 'information_type', 'method', 'conditions', 'quantities', 'sequence'):
            assert actual[key] == expected[key]
        assert actual['statement'] == expected['spoken_explanation']
        assert actual['source_spoken_insight']['source_segment_ids'] == expected['source_segment_ids']
        assert actual['check']['status'] == 'supported'  # Existing planner eligibility.
        assert actual['check']['reference_validation'] == 'validated'
        assert actual['check']['semantic_verification'] == 'pending'
    assert result['items'][0]['reasons'] == ['Tirar o excesso de amido.']
    assert result['items'][1]['restrictions'] == ['Esse tempo não vale para arroz integral.']
    assert result['items'][1]['evidence'] == [
        {'source_id': segment['id'], 'excerpt': segment['text']}
        for segment in originals[0]['segments'][1:3]]
    assert len(calls) == 1 and calls[0]['role'] == 'extractor'
    assert calls[0]['payload']['_source_detail_contract'] == video_first.SOURCE_DETAIL_CONTRACT_VERSION
    assert calls[0]['payload']['_budget_reserve'] == 3
    assert [s['id'] for s in calls[0]['payload']['videos'][0]['segments']] == ['v1s1', 'v1s2', 'v1s3']
    assert result['source_detail_version'] == video_first.SOURCE_DETAIL_CONTRACT_VERSION
    assert saved[-1]['apuration']['source_detail_version'] == video_first.SOURCE_DETAIL_CONTRACT_VERSION


def test_reference_accounting_exposes_unreferenced_segments_without_claiming_coverage(cooking_job, offline_extract):
    run, _, _ = offline_extract
    # Multiple insights may reference one segment. This does not cover the
    # uncited later cooking conditions, or establish the fidelity of either item.
    result = run(cooking_job, extraction(spoken_video('v1',
        insight('Eu uso uma xícara de arroz.', ['v1s1']),
        insight('Eu lavo o arroz.', ['v1s1']))))
    coverage, = result['source_coverage']
    assert coverage['input_segment_count'] == 4
    assert coverage['cleaned_segment_count'] == 3
    assert coverage['referenced_segment_count'] == 1
    assert coverage['referenced_segment_ids'] == ['v1s1']
    assert coverage['unreferenced_segment_ids'] == ['v1s2', 'v1s3']
    assert coverage['cleaned_out_segment_ids'] == ['v1s4']
    assert coverage['basis'] == 'segment_references_only'
    assert coverage['semantic_verification'] == 'pending'
    assert coverage['source_detail_completeness'] == 'unverified'


def test_citing_every_segment_still_does_not_verify_detail_completeness(cooking_job, offline_extract):
    run, _, _ = offline_extract
    result = run(cooking_job, extraction(spoken_video('v1',
        insight('A criadora explica o preparo do arroz.', ['v1s1', 'v1s2', 'v1s3']))))
    coverage, = result['source_coverage']
    assert coverage['referenced_segment_count'] == 3
    assert coverage['unreferenced_segment_ids'] == []
    assert coverage['source_detail_completeness'] == 'unverified'
    assert coverage['semantic_verification'] == 'pending'
    item, = result['items']
    assert item['information_type'] == 'afirmação'
    assert item['source_spoken_insight']['information_type'] is None
    assert item['method'] == '' and item['conditions'] == item['quantities'] == []


def test_information_types_and_kind_are_preserved_for_furniture_examples(cooking_job, offline_extract):
    run, _, _ = offline_extract
    cooking_job['sources'][0]['segments'] = [
        {'id': 'v1s1', 'text': 'Nesta cadeira, apertei os parafusos antes de encaixar o assento.'},
        {'id': 'v1s2', 'text': 'Eu prefiro a chave manual à elétrica para este ajuste.'}]
    result = run(cooking_job, extraction(spoken_video('v1',
        insight('Nesta cadeira, apertei os parafusos antes de encaixar o assento.', ['v1s1'],
                kind='experiência', information_type='exemplo',
                sequence=['Apertar os parafusos.', 'Encaixar o assento.']),
        insight('Eu prefiro a chave manual à elétrica para este ajuste.', ['v1s2'],
                kind='opinião', information_type='comparação'))))
    assert [(item['kind'], item['information_type']) for item in result['items']] == [
        ('experiência', 'exemplo'), ('opinião', 'comparação')]


@pytest.mark.parametrize('source_ids', [['missing'], ['v1s1', 'v1s1'], ['v1s4']])
def test_invalid_or_cleaned_out_references_cannot_be_saved(cooking_job, offline_extract, source_ids):
    run, _, saved = offline_extract
    with pytest.raises((ValueError, generation.GenerationResponseError)):
        run(cooking_job, extraction(spoken_video('v1', insight('Eu lavo o arroz.', source_ids))))
    assert 'apuration' not in cooking_job and saved == []


def test_same_video_references_and_exact_video_inventory_are_validated(cooking_job, offline_extract):
    run, _, saved = offline_extract
    cooking_job['sources'].append({'id': 'v2', 'url': 'https://example.test/furniture',
        'title': 'Ajuste de cadeira', 'author': 'Criador de exemplo', 'status': 'ok',
        'segments': [{'id': 'v2s1', 'text': 'Aperto os parafusos da cadeira com uma chave manual.'}]})
    with pytest.raises(generation.GenerationResponseError):
        run(cooking_job, extraction(spoken_video('v1', insight('Eu lavo o arroz.', ['v1s1']))))
    with pytest.raises(generation.GenerationResponseError):
        run(cooking_job, extraction(
            spoken_video('v1', insight('Eu lavo o arroz.', ['v2s1'])),
            spoken_video('v2', insight('Aperto os parafusos.', ['v2s1']))))
    assert 'apuration' not in cooking_job and saved == []
