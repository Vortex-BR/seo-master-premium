"""Knowledge preservation is tested against originals, not a semantic score."""
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation
from app.editorial import human_knowledge as hkl, store
from app.editorial.human_knowledge_contracts import HumanKnowledgeDossier


@pytest.fixture
def original():
    return {'id': 'source-only', 'brief': {'topic': 'Relato'}, 'article': {'markdown': 'Artigo salvo.'},
            'usage': [], 'sources': [{'id': 'video-1', 'video_id': 'abcdefghijk',
                'title': 'Teste do criador', 'author': 'Canal de exemplo', 'provider': 'fixture',
                'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'status': 'ok',
                'segments': [{'id': 's1', 'text': 'Eu testei duas vezes. Na minha opinião, funciona desde que esteja frio.',
                              'start': 1.0, 'end': 5.0, 'speaker': 'SPEAKER_00'},
                             {'id': 's2', 'text': 'O resultado foi bom, exceto quando usei 3 kg. Talvez seja coincidência.',
                              'start': 500.0, 'end': 510.0}]}]}


def item(original, *, kind='experiência', ident='item-1', segment='s1', **extra):
    text = next(value['text'] for value in original['sources'][0]['segments'] if value['id'] == segment)
    return {'id': ident, 'video_id': 'video-1', 'topic': 'Teste', 'statement': text,
            'kind': kind, 'information_type': 'afirmação', 'method': '', 'conditions': [],
            'quantities': [], 'restrictions': [], 'limitations': [],
            'evidence': [{'source_id': segment, 'excerpt': text}], **extra}


def apurate(original, items, **extra):
    original['apuration'] = {'valid': True, 'version': 'saved-apuration-v1', 'items': items,
                            'videos': [], 'comparisons': [],
                            'dependencies': {'inputs': store.inputs_version(original)}, **extra}
    return original


def primary(dossier, item_id='item-1'):
    return next(unit for unit in dossier['units'] if unit['legacy_item_id'] == item_id and not unit['parent_unit_id'])


def test_project_is_pure_no_database_provider_or_job_changes(original, monkeypatch):
    before = deepcopy(original)
    monkeypatch.setattr(db, 'connect', Mock(side_effect=AssertionError('storage must not open')))
    monkeypatch.setattr(generation, 'structured', Mock(side_effect=AssertionError('provider must not run')))
    monkeypatch.setattr(db, 'get_setting', Mock(side_effect=AssertionError('settings must not be read')))
    dossier = hkl.project(original)
    assert original == before
    assert dossier['schema_version'] == 'human_knowledge.v1'
    assert dossier['projection_version'] == 1 and dossier['independent_source'] is False
    assert dossier['apuration_status'] == 'missing'
    assert [unit['text'] for unit in dossier['units']] == ['', '']
    assert dossier['coverage']['by_type'] == {'unknown': 2}
    assert dossier['coverage']['semantic_verification'] == 'not_performed'
    assert 'quality_score' not in json.dumps(dossier)
    assert hkl.project(original) == dossier


@pytest.mark.parametrize(('kind', 'expected'), [('experiência', 'experience'), ('opinião', 'opinion'), ('fato', 'assertion')])
def test_declared_legacy_kind_is_not_independent_truth(original, kind, expected):
    dossier = hkl.project(apurate(original, [item(original, kind=kind)]))
    unit = primary(dossier)
    assert unit['type'] == expected
    assert unit['classification'] == {'basis': 'legacy_kind', 'status': 'declared', 'source_field': 'kind'}
    assert unit['truth_status'] == 'not_independently_verified'
    assert unit['article_writer'] == 'unknown'
    assert dossier['sources'][0]['video_author']['label'] == 'Canal de exemplo'
    assert unit['speaker_labels'] == ['SPEAKER_00']
    assert unit['speaker_identity'] == 'unknown'
    assert hkl.resolve_unit(original, dossier, unit['id'])['records'][0]['text'] == unit['text']


def test_video_first_synthetic_fact_is_unknown_preserves_richer_structure(original):
    spoken = {'topic': 'Teste', 'spoken_explanation': 'Eu testei em uma situação específica.',
              'source_segment_ids': ['s1', 's2'], 'practical_tips': ['Use frio.'],
              'analogies': ['Funciona como uma esponja.'], 'warnings': ['Cuidado com 3 kg.']}
    projected = item(original, kind='fato', source_spoken_insight=spoken,
                     statement=spoken['spoken_explanation'], evidence=[{'source_id': 's1'}, {'source_id': 's2'}])
    dossier = hkl.project(apurate(original, [projected], video_first=True))
    unit = primary(dossier)
    assert unit['type'] == 'unknown' and unit['classification']['status'] == 'unknown'
    assert unit['text'] == spoken['spoken_explanation']
    assert unit['preserved']['source_spoken_insight'] == spoken
    assert {child['type'] for child in dossier['units'] if child['parent_unit_id']} == {'method', 'analogy', 'risk'}
    assert {candidate['type'] for candidate in unit['candidates']} >= {'experience', 'opinion', 'condition', 'exception', 'doubt'}
    assert all(candidate['basis'] == 'literal_cue' and candidate['status'] == 'candidate' for candidate in unit['candidates'])
    assert unit['truth_status'] == 'not_independently_verified'


def test_spoken_extraction_only_and_older_projection_recover_full_insight(original):
    spoken = {'topic': 'Teste', 'spoken_explanation': original['sources'][0]['segments'][0]['text'],
              'source_segment_ids': ['s1'], 'practical_tips': [], 'analogies': ['É como uma esponja.'], 'warnings': []}
    extraction = {'summary': 'Relato.', 'videos': [{'video_id': 'video-1', 'summary': 'Teste.',
                    'insights': [spoken], 'gaps': ['A imagem não foi examinada.']}]}
    apurate(original, [], spoken_extraction=extraction)
    dossier = hkl.project(original)
    assert any(unit['type'] == 'analogy' and unit['text'] == 'É como uma esponja.' for unit in dossier['units'])
    assert dossier['apuration_status'] == 'partial'
    assert 'A imagem não foi examinada.' in dossier['gaps']
    original['apuration']['items'] = [item(original, kind='fato')]
    before = deepcopy(original)
    dossier = hkl.project(original)
    assert primary(dossier)['type'] == 'unknown'
    assert primary(dossier)['preserved']['source_spoken_insight'] == spoken
    assert original == before


def test_conditions_exceptions_quantities_and_distant_relationship_survive(original):
    one = item(original, conditions=['Desde que esteja frio.'], quantities=[{'value': '2', 'unit': 'vezes', 'context': 'teste'}],
               restrictions=['Exceto com 3 kg.'], limitations=['A experiência é particular.'])
    two = item(original, ident='item-2', segment='s2', kind='opinião')
    relation = {'relation': 'restriction', 'item_ids': ['item-1', 'item-2'],
                'explanation': 'A ressalva final limita o teste inicial.',
                'check': {'status': 'supported', 'reason': 'Relação previamente registrada.'}}
    dossier = hkl.project(apurate(original, [one, two], videos=[{'id': 'video-1', 'relations': [relation], 'gaps': []}]))
    assert primary(dossier)['preserved']['quantities'] == one['quantities']
    assert any(unit['type'] == 'condition' and unit['text'] == 'Desde que esteja frio.' for unit in dossier['units'])
    assert any(unit['text'] == 'Exceto com 3 kg.' for unit in dossier['units'])
    relation_new = next(value for value in dossier['relations'] if value['basis'] == 'existing_relation')
    assert relation_new['relation'] == 'restriction' and relation_new['check'] == relation['check']
    assert relation_new['unit_ids'] == [primary(dossier)['id'], primary(dossier, 'item-2')['id']]
    timings = [anchor['timing'] for unit in dossier['units'] if not unit['parent_unit_id'] for anchor in unit['anchors']]
    assert [timing['start'] for timing in timings] == [1.0, 500.0]
    assert not any(timing['start'] == 1.0 and timing['end'] == 510.0 for timing in timings)


def test_no_relation_inferred_from_timestamp_or_order(original):
    dossier = hkl.project(apurate(original, [item(original), item(original, ident='item-2', segment='s2')]))
    assert dossier['relations'] == []


def test_source_author_does_not_become_speaker_identity(original):
    for segment in original['sources'][0]['segments']:
        segment.pop('speaker', None)
    dossier = hkl.project(original)
    assert all(unit['speaker_labels'] == [] and unit['speaker_identity'] == 'unknown' for unit in dossier['units'])
    assert dossier['coverage']['units_unknown_speaker'] == 2
    assert dossier['sources'][0]['video_author']['identity_status'] == 'unverified'
    assert 'professional' not in json.dumps(dossier)


def test_background_research_never_become_external_verified(original):
    original['sources'].append({'id': 'research', 'internal_context_only': True,
                                'segments': [{'id': 'rn1', 'text': 'Explicação interna.'}]})
    apurate(original, [item(original, evidence=[{'source_id': 'rn1', 'excerpt': 'Explicação interna.'}])])
    original['research'] = {'citations': [{'id': 'rn1', 'url': 'https://external.invalid'}]}
    dossier = hkl.project(original)
    assert primary(dossier)['anchors'][0]['status'] == 'internal_only'
    assert all(unit['origin'] == 'video' for unit in dossier['units'])
    assert 'external_verified' not in json.dumps(dossier)
    assert all(source['source_id'] != 'research' for source in dossier['sources'])


@pytest.mark.parametrize(('reference', 'change', 'expected'), [
    ('missing', None, 'missing'), (None, None, 'invalid_reference'),
    ('s1', 'duplicate', 'ambiguous'), ('s1', 'internal', 'internal_only'),
])
def test_unresolvable_reference_is_not_silently_supported(original, reference, change, expected):
    if change == 'duplicate':
        original['sources'][0]['segments'].append(deepcopy(original['sources'][0]['segments'][0]))
    if change == 'internal':
        original['sources'][0]['segments'][0]['internal_context_only'] = True
    apurate(original, [item(original, evidence=[{'source_id': reference}])])
    dossier = hkl.project(original)
    unit = primary(dossier)
    assert unit['anchors'][0]['status'] == expected
    assert unit['origin_status'] == 'unresolved'
    assert hkl.resolve_unit(original, dossier, unit['id'])['status'] == 'unresolved'
    assert dossier['coverage']['units_missing_provenance'] >= 1


@pytest.mark.parametrize(('excerpt', 'offsets', 'expected'), [
    ('Eu testei', {}, 'resolved'), ('Inventado', {}, 'invalid_excerpt'),
    ('', {}, 'invalid_excerpt'), ('Eu testei', {'offset_start': 1, 'offset_end': 10}, 'invalid_excerpt'),
    ('Eu testei', {'offset_start': True, 'offset_end': 9}, 'invalid_excerpt'),
    ('Eu testei', {'offset_start': 0, 'offset_end': 9}, 'resolved'),
])
def test_excerpt_offsets_are_literal_and_never_guessed(original, excerpt, offsets, expected):
    apurate(original, [item(original, evidence=[{'source_id': 's1', 'excerpt': excerpt, **offsets}])])
    dossier = hkl.project(original)
    anchor = primary(dossier)['anchors'][0]
    assert anchor['status'] == expected
    if expected == 'resolved':
        assert anchor['offset_start'] == 0 and anchor['offset_end'] == 9
    else:
        assert anchor['offset_start'] is None and anchor['offset_end'] is None


def test_repeated_excerpt_requires_explicit_offset(original):
    original['sources'][0]['segments'][0]['text'] = 'Teste. Teste.'
    apurate(original, [item(original, evidence=[{'source_id': 's1', 'excerpt': 'Teste.'}])])
    assert primary(hkl.project(original))['anchors'][0]['status'] == 'ambiguous_excerpt'
    original['apuration']['items'][0]['evidence'][0].update(offset_start=7, offset_end=13)
    assert primary(hkl.project(original))['anchors'][0]['status'] == 'resolved'


def test_cue_offsets_and_disjoint_intervals_are_resolved_without_envelope(original):
    segment = original['sources'][0]['segments'][0]
    segment.update(original_cues=[{'id': 'c1', 'text': 'Eu testei.', 'original_text': 'Eu testei.', 'start': 1, 'end': 2},
                                  {'id': 'c2', 'text': 'Exceto depois.', 'original_text': 'Exceto depois.', 'start': 80, 'end': 82}],
                   normalization_version='normalization-fixture')
    apurate(original, [item(original, evidence=[{'source_id': 's1'}])])
    dossier = hkl.project(original)
    anchor = primary(dossier)['anchors'][0]
    assert [(span['start'], span['end']) for span in anchor['intervals']] == [(1, 2), (80, 82)]
    resolved = hkl.resolve_unit(original, dossier, primary(dossier)['id'])
    assert resolved['records'][0]['original_cues'] == segment['original_cues']
    resolved['records'][0]['original_cues'][0]['original_text'] = 'Mutação isolada.'
    assert original['sources'][0]['segments'][0]['original_cues'][0]['original_text'] == 'Eu testei.'
    original['apuration']['items'][0]['evidence'] = [{'source_id': 'c2', 'excerpt': 'Exceto'}]
    cue_dossier = hkl.project(original)
    assert primary(cue_dossier)['anchors'][0]['offset_start'] == 0
    assert hkl.resolve_unit(original, cue_dossier, primary(cue_dossier)['id'])['records'][0]['cue_id'] == 'c2'


@pytest.mark.parametrize(('start', 'end', 'availability'), [(None, None, 'unavailable'),
    (None, 5, 'partial'), (1, None, 'partial'), (1, 1, 'point'), (1, 2, 'interval'),
    (-1, 2, 'partial'), (5, 2, 'partial'), (True, float('inf'), 'unavailable')])
def test_missing_invalid_or_partial_timing_is_explicit(original, start, end, availability):
    original['sources'][0]['segments'][0].update(start=start, end=end)
    dossier = hkl.project(original)
    anchor = dossier['units'][0]['anchors'][0]
    assert anchor['timing']['availability'] == availability
    assert not isinstance(anchor['timing']['start'], bool)


def test_literal_candidates_refer_to_source_offsets_not_summarized_text(original):
    apurate(original, [item(original, kind='fato', source_spoken_insight={
        'spoken_explanation': 'Uma síntese sem classificação.', 'source_segment_ids': ['s1'],
        'practical_tips': [], 'analogies': [], 'warnings': []})], video_first=True)
    dossier = hkl.project(original)
    unit = primary(dossier)
    for candidate in unit['candidates']:
        source = generation.resolve_evidence(original, candidate['reference_id'])
        assert source['text'][candidate['offset_start']:candidate['offset_end']] == candidate['excerpt']
    assert unit['type'] == 'unknown'
    assert {'opinion', 'experience', 'condition'} <= {candidate['type'] for candidate in unit['candidates']}


def test_stale_apuration_not_marked_current_even_when_valid_flag_stays_true(original):
    apurate(original, [item(original, source_spoken_insight={
        'spoken_explanation': 'Eu testei frio.', 'source_segment_ids': ['s1'],
        'practical_tips': [], 'analogies': [], 'warnings': []}, evidence=[{'source_id': 's1'}])], video_first=True)
    old = hkl.project(original)
    original['sources'][0]['segments'][0]['text'] = 'Eu nunca testei frio; testei quente.'
    dossier = hkl.project(original)
    assert original['apuration']['valid'] is True
    assert dossier['apuration_status'] == 'stale'
    unit = primary(dossier)
    assert unit['text'] == 'Eu testei frio.' and unit['anchors'][0]['status'] == 'stale'
    assert unit['candidates'] == [] and unit['origin_status'] == 'unresolved'
    assert hkl.resolve_unit(original, dossier, unit['id'])['status'] == 'unresolved'
    assert hkl.resolve_unit(original, old, primary(old)['id'])['status'] == 'stale'
    assert any(fallback['source_field'] == 'source_segment' for fallback in dossier['units'])


def test_partial_and_legacy_unknown_identity_are_explicit(original):
    apurate(original, [item(original)], inventory={'blocks': [{'id': 'b1', 'status': 'pending'}]})
    assert hkl.project(original)['apuration_status'] == 'partial'
    original['apuration'].pop('inventory')
    original['apuration'].pop('dependencies')
    assert hkl.project(original)['apuration_status'] == 'legacy_unverified'
    original['apuration']['valid'] = False
    assert hkl.project(original)['apuration_status'] == 'stale'


def test_legacy_job_without_new_fields_remains_resolvable(original):
    original['sources'][0]['segments'] = [{'id': 's1', 'text': 'Relato antigo.'}]
    dossier = hkl.project(original)
    unit = dossier['units'][0]
    resolved = hkl.resolve_unit(original, dossier, unit['id'])
    assert resolved['status'] == 'resolved' and resolved['records'][0]['text'] == 'Relato antigo.'
    assert unit['anchors'][0]['timing']['availability'] == 'unavailable'
    assert resolved['records'][0]['original_cues'] == []


def test_missing_and_ambiguous_relation_references_are_preserved_unresolved(original):
    first = item(original)
    apurate(original, [first, deepcopy(first)], videos=[{'relations': [
        {'relation': 'example', 'item_ids': ['item-1'], 'explanation': 'ID duplicado.'},
        {'relation': 'condition', 'item_ids': ['missing'], 'explanation': 'ID ausente.'}]}])
    dossier = hkl.project(original)
    assert all(relation['status'] == 'unresolved' and relation['unit_ids'] == [] for relation in dossier['relations'])
    assert [relation['explanation'] for relation in dossier['relations']] == ['ID duplicado.', 'ID ausente.']


def test_compact_transport_contains_only_references_and_selected_relations(original):
    apurate(original, [item(original, conditions=['Desde que esteja frio.'])])
    dossier = hkl.project(original)
    packed = hkl.compact(dossier, ['item-1'])
    rendered = json.dumps(packed, ensure_ascii=False)
    assert original['sources'][0]['segments'][0]['text'] not in rendered
    assert 'Desde que esteja frio.' not in rendered
    assert 'excerpt' not in rendered and 'preserved' not in rendered
    assert len(packed['units']) == 2 and len(packed['relations']) == 1
    assert packed['source_snapshot_version'] == dossier['source_snapshot_version']
    assert hkl.compact(dossier, [])['units'] == []


def test_resolver_rejects_mismatched_snapshot_tampered_excerpt_and_unknown_version(original):
    apurate(original, [item(original)])
    dossier = hkl.project(original)
    ident = primary(dossier)['id']
    damaged = deepcopy(dossier)
    primary(damaged)['anchors'][0]['excerpt'] = 'Inventado'
    assert hkl.resolve_unit(original, damaged, ident)['status'] == 'unresolved'
    unknown = deepcopy(dossier)
    unknown['schema_version'] = 'human_knowledge.v99'
    with pytest.raises(ValueError):
        hkl.resolve_unit(original, unknown, ident)
    unknown = deepcopy(dossier)
    unknown['projection_version'] = 99
    with pytest.raises(ValueError):
        hkl.compact(unknown)
    assert hkl.resolve_unit(original, dossier, 'missing')['status'] == 'unresolved'
    assert hkl.resolve_unit({**original, 'id': 'other-job'}, dossier, ident)['status'] == 'unresolved'
    damaged = deepcopy(dossier)
    primary(damaged)['anchors'][0]['offset_start'] = -len(original['sources'][0]['segments'][0]['text'])
    assert hkl.resolve_unit(original, damaged, ident)['status'] == 'unresolved'


@pytest.mark.parametrize(('flag', 'expected'), [(None, 'shadow'), ('shadow', 'shadow'), (' SHADOW ', 'shadow'),
                                              ('off', 'off'), ('active', 'off'), ('', 'off')])
def test_feature_flag_never_enables_paid_or_unreviewed_mode(monkeypatch, flag, expected):
    if flag is None:
        monkeypatch.delenv('HUMAN_KNOWLEDGE_MODE', raising=False)
    else:
        monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', flag)
    assert hkl.mode() == expected


def test_off_does_not_project_or_write(original, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'off')
    projected = Mock(side_effect=AssertionError('no CPU work in off'))
    persisted = Mock(side_effect=AssertionError('no storage in off'))
    monkeypatch.setattr(hkl, 'project', projected)
    monkeypatch.setattr(store, 'artifact', persisted)
    assert hkl.persist_shadow(original) is None
    projected.assert_not_called()
    persisted.assert_not_called()


def test_persistence_is_immutable_deduplicated_and_keeps_old_source_snapshot(job, monkeypatch):
    monkeypatch.setenv('HUMAN_KNOWLEDGE_MODE', 'shadow')
    before = deepcopy(job)
    first = hkl.persist_shadow(job)
    second = hkl.persist_shadow(job)
    assert first['version'] == second['version'] and job == before
    assert len(store.artifacts(job['id'], 'human_knowledge')) == 1
    assert len(store.artifacts(job['id'], 'human_knowledge_sources')) == 1
    old_source = store.artifacts(job['id'], 'human_knowledge_sources')[0]
    old_id = first['data']['units'][0]['id']
    job['sources'][0]['segments'][0]['text'] += ' Nova condição.'
    newest = hkl.persist_shadow(job)
    assert newest['version'] != first['version']
    assert len(store.artifacts(job['id'], 'human_knowledge_sources')) == 2
    assert hkl.resolve_unit(job, first, old_id)['status'] == 'stale'
    old = hkl.resolve_unit(job, first, old_id, source_snapshot=old_source)
    assert old['status'] == 'resolved'
    assert old['records'][0]['text'] == before['sources'][0]['segments'][0]['text']
    assert db.get_job(job['id']) == before


def test_projection_apuration_versions_do_not_duplicate_source_snapshot(job, monkeypatch):
    first = hkl.persist_shadow(job)
    job['apuration'] = {'valid': False, 'version': 'old', 'items': [], 'videos': [], 'comparisons': []}
    second = hkl.persist_shadow(job)
    assert first['version'] != second['version']
    monkeypatch.setattr(hkl, 'PROJECTION_VERSION', 2)
    third = hkl.persist_shadow(job)
    assert third['version'] != second['version'] and third['data']['projection_version'] == 2
    assert len(store.artifacts(job['id'], 'human_knowledge_sources')) == 1
    assert len(store.artifacts(job['id'], 'human_knowledge')) == 3
    assert hkl.resolve_unit(job, first, first['data']['units'][0]['id'])['status'] == 'resolved'


@pytest.mark.parametrize('error', [ValueError('storage unavailable'), KeyboardInterrupt(), SystemExit()])
def test_persistence_error_and_cancel_propagate_without_mutating_job(original, monkeypatch, error):
    before = deepcopy(original)
    monkeypatch.setattr(store, 'artifact', Mock(side_effect=error))
    with pytest.raises(type(error)):
        hkl.persist_shadow(original)
    assert original == before


def test_artifact_data_round_trips_typed_contract(original):
    dossier = hkl.project(apurate(original, [item(original)]))
    assert HumanKnowledgeDossier.model_validate(json.loads(json.dumps(dossier))).model_dump() == dossier


def test_index_keeps_duplicate_cue_ambiguity_and_original_cue_metadata(original):
    segment = original['sources'][0]['segments'][0]
    segment['original_cues'] = [{'id': 'cue', 'text': 'Original.', 'original_text': 'Original.'},
                                {'id': 'cue', 'text': 'Duplicado.', 'original_text': 'Duplicado.'}]
    apurate(original, [item(original, evidence=[{'source_id': 'cue'}])])
    assert primary(hkl.project(original))['anchors'][0]['status'] == 'ambiguous'


def test_resolved_originals_match_global_resolver_with_indexed_sources(original):
    dossier = hkl.project(original)
    for unit in dossier['units']:
        resolved = hkl.resolve_unit(original, dossier, unit['id'])
        assert resolved['records'][0] == generation.resolve_evidence(original, unit['anchors'][0]['reference_id'])
