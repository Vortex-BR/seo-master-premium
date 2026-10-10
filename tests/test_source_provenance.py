"""Source references remain resolvable, scoped and inexpensive to transport."""
from copy import deepcopy
from contextlib import nullcontext
import json

import pytest

from app import generation, source_cache, youtube
from app.editorial import source_processing
from app.strategy.contracts import BusinessAnalysis


EXPLANATION = 'Uma explicação completa preserva condições, medidas e ressalvas da fala original. ' * 2


def test_warning_cannot_land_in_gap_of_explicit_original_intervals():
    source = {'segments': [{'id': 'v1s1', 'start': 0, 'end': 205, 'text': EXPLANATION,
                           'intervals': [{'start': 0, 'end': 5}, {'start': 200, 'end': 205}]}],
              'transcription_warnings': [{'start': 50, 'end': 100, 'reason': 'Incerteza localizada.'}]}
    assert source_processing.confidence_source_ids(source) == []
    assert source_processing.quality(source)['low_confidence'] == source['transcription_warnings']


@pytest.mark.parametrize('warning', [
    {'start': None, 'end': 15}, {'start': True, 'end': 15},
    {'start': float('nan'), 'end': 15}, {'start': 20, 'end': 10},
    {'start': 5, 'end': 'invalid'}, {'reason': 'Aviso global.'},
])
def test_unlocalizable_warning_is_preserved_globally(warning):
    source = {'segments': [{'id': 'v1s1', 'start': 10, 'end': 20, 'text': EXPLANATION},
                           {'id': 'v1s2', 'start': None, 'end': None, 'text': EXPLANATION}],
              'transcription_warnings': [warning]}
    assert source_processing.confidence_source_ids(source) == ['v1s1', 'v1s2']
    assert source_processing.quality(source)['global_low_confidence'] == [warning]


def test_resolver_rejects_ambiguous_and_internal_ids(job):
    source = job['sources'][0]
    source['segments'] = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': 10}], 'v1')
    original = deepcopy(job)
    source['segments'].append(deepcopy(source['segments'][0]))
    assert generation.resolve_evidence(job, 'v1s1') is None
    assert generation.resolve_evidence(job, 'v1c1') is None
    job['sources'] = original['sources']
    job['sources'][0]['internal_context_only'] = True
    assert generation.resolve_evidence(job, 'v1s1') is None
    assert generation.resolve_evidence(job, 'v1c1') is None


def test_inspecting_original_evidence_does_not_mutate_source(job):
    source = job['sources'][0]
    source['segments'] = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': 10}], 'v1')
    before = deepcopy(job)
    reference = generation.resolve_evidence(job, 'v1s1')
    reference['original_cues'][0]['original_text'] = 'Changed by a caller.'
    assert job == before
    assert generation.resolve_evidence(job, 'v1s1')['original_cues'][0]['original_text'] == EXPLANATION


def test_every_nested_context_path_omits_raw_cue_copies(job):
    segment = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': 10}], 'v1')[0]
    payload = {'review_notes': {'received_sources': [{'segments': [segment]}]}}
    before = deepcopy(payload)
    rendered = generation.context(job, payload)
    transported = json.loads(rendered)['review_notes']['received_sources'][0]['segments'][0]
    assert 'original_cues' not in rendered
    assert 'original_text' not in rendered
    assert transported['cue_ids'] == ['v1c1']
    assert transported['text'] == segment['text']
    assert 'intervals' not in transported
    assert (transported['start'], transported['end']) == (10, 20)
    assert payload['review_notes']['received_sources'][0]['segments'][0]['intervals']
    assert payload == before


def test_strategy_provider_request_uses_the_same_compact_provenance_boundary(job):
    segments = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': 10}], 'v1')
    payload = {'video_sources': [{'segments': segments}]}
    before = deepcopy(payload)
    request = generation.prepare_structured(job, BusinessAnalysis, 'Analyze available evidence.',
                                            'strategy_business', payload)[0]
    assert 'original_cues' not in request['input']
    transported = json.loads(request['input'])['video_sources'][0]['segments'][0]
    assert transported['text'] == segments[0]['text']
    assert transported['cue_ids'] == ['v1c1']
    assert 'intervals' not in transported
    assert payload == before


def test_legacy_research_provider_request_does_not_repeat_original_cues(job, monkeypatch):
    segments = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': 10}], 'v1')
    job['dossier'] = {'received_source_segments': segments}
    original = deepcopy(job)
    captured = []

    def capture_request(_job, _client, request, _stage, **_kwargs):
        captured.append(request)
        raise RuntimeError('Offline request captured.')

    monkeypatch.setattr(generation, 'client', lambda: nullcontext(None))
    monkeypatch.setattr(generation.spending, 'create_response', capture_request)
    with pytest.raises(RuntimeError, match='Offline request captured'):
        generation.research(job)
    assert len(captured) == 1
    assert 'original_cues' not in captured[0]['input']
    transported = json.loads(captured[0]['input'])['dossier']['received_source_segments'][0]
    assert transported['text'] == segments[0]['text']
    assert transported['cue_ids'] == ['v1c1']
    assert job == original


def test_legacy_evidence_map_keeps_exact_fingerprint_shape(job):
    expected = {'v1s1': {'text': job['sources'][0]['segments'][0]['text'],
                         'title': job['sources'][0]['title'],
                         'url': job['sources'][0]['url'] + '&t=10s', 'kind': 'transcript'}}
    assert generation.evidence_map(job) == expected


@pytest.mark.parametrize('originals', [None, [], [{'id': 'v1c1'}]])
def test_incomplete_provenance_never_claims_original_cues_available(job, originals):
    segment = job['sources'][0]['segments'][0]
    segment.update(normalization_version='source-integrity-v1', original_cues=originals,
                   intervals=[{'start': 10, 'end': 12}, {'start': 18, 'end': 20}])
    reference = generation.resolve_evidence(job, segment['id'])
    assert reference['original_cues_available'] is False
    mapped = generation.evidence_map(job)[segment['id']]
    assert mapped['provenance']['original_cues_available'] is False
    assert generation.compact_source_provenance(mapped)['intervals'] == segment['intervals']


def test_legacy_provider_markers_survive_new_normalization_and_reference_resolution(job):
    source = job['sources'][0]
    source.update(provider='Supadata', provider_adapter_version='supadata-rows-legacy',
                  provider_cache_legacy=True, normalization_version='source-integrity-v1')
    source['segments'] = youtube.segment_rows([{'text': EXPLANATION, 'start': 10, 'duration': None}], 'v1')
    reference = generation.resolve_evidence(job, 'v1c1')
    assert reference['provider_adapter_version'] == 'supadata-rows-legacy'
    assert reference['provider_cache_legacy'] is True
    assert reference['original_id'] is None
    assert reference['end'] is None
    assert reference['timing']['availability'] == 'partial'
    provenance = generation.evidence_map(job)['v1s1']['provenance']
    assert provenance['provider_adapter_version'] == 'supadata-rows-legacy'
    assert provenance['provider_cache_legacy'] is True


def test_cache_references_rebase_but_provider_and_origin_records_stay_literal(job, monkeypatch):
    source = deepcopy(job['sources'][0])
    source.update(provider='Supadata', extracted_at=job['created_at'],
                  normalization_version='source-integrity-v1', normalization_options={'merge_adjacent': True})
    provider_id = {'source_id': 'v1', 'segment_id': 'v1s1'}
    source['segments'] = youtube.segment_rows([{'id': provider_id, 'text': EXPLANATION,
                                              'start': 10, 'duration': 10}], 'v1')
    source['transcription_warnings'] = [{'start': 10, 'end': 15, 'source_ids': ['v1s1'], 'cue_ids': ['v1c1']}]
    monkeypatch.setattr(source_cache.db, 'list_jobs', lambda: [{**job, 'sources': [source]}])
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '1')
    before = deepcopy(source)
    reused = source_cache.find_recent(source['video_id'], 'v2', 'other', require_current=True)
    assert reused['segments'][0]['original_cues'][0]['original_id'] == provider_id
    assert reused['transcription_warnings'][0]['source_ids'] == ['v2s1']
    assert reused['transcription_warnings'][0]['cue_ids'] == ['v2c1']
    assert source == before


def test_cache_same_prefix_and_skipped_cue_indices_do_not_remap_ownership_twice(job, monkeypatch):
    source = deepcopy(job['sources'][0])
    source.update(provider='Supadata', extracted_at=job['created_at'],
                  normalization_version='source-integrity-v1', normalization_options={'merge_adjacent': True})
    source['segments'] = youtube.segment_rows([
        {'text': ' ', 'start': 0, 'duration': 5},
        {'text': EXPLANATION, 'start': 10, 'duration': 5},
        {'text': EXPLANATION, 'start': 15, 'duration': 5}], 'v1')
    assert source['segments'][0]['cue_ids'] == ['v1c2', 'v1c3']
    monkeypatch.setattr(source_cache.db, 'list_jobs', lambda: [{**job, 'sources': [source]}])
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '1')
    reused = source_cache.find_recent(source['video_id'], 'v1', 'other', require_current=True)
    assert reused['segments'][0]['cue_ids'] == ['v1c1', 'v1c2']
    assert [cue['id'] for cue in reused['segments'][0]['original_cues']] == ['v1c1', 'v1c2']
    assert generation.resolve_evidence({'sources': [reused]}, 'v1c2')['start'] == 15
