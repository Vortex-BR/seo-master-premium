"""Behavioral regressions for P0_01; source data must survive ingestion."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline, publishing, source_cache, transcripts, youtube
from app.editorial import source_processing


EXPLANATION = ('A explicação técnica descreve as condições do procedimento e os '
               'limites que precisam ser conferidos no conteúdo original. ')


def test_mathematical_comparison_survives_source_normalization():
    text = EXPLANATION + 'A condição é 2 < valor e valor > 1.'
    segments = youtube.segment_rows([{'text': text, 'start': 0, 'duration': 5}], 'v1')
    assert segments[0]['text'] == text


def test_unknown_technical_markup_retains_case_and_remains_plain_text():
    text = EXPLANATION + 'O tipo é <MyType>valor</MyType> e o parâmetro é List<T>.'
    segments = youtube.segment_rows([{'text': text, 'start': 0, 'duration': 5}], 'v1')
    assert segments[0]['text'] == text


@pytest.mark.parametrize('tag', ['script', 'style'])
def test_incomplete_raw_text_markup_does_not_discard_remaining_spoken_text(tag):
    tail = 'Uma ressalva indispensável ainda aparece no fim da fala.'
    text = EXPLANATION + f'<{tag}>' + tail
    segments = youtube.segment_rows([{'text': text, 'start': 0, 'duration': 5}], 'v1')
    assert segments[0]['text'] == EXPLANATION + tail
    assert segments[0]['original_cues'][0]['original_text'] == text


def test_distant_cues_do_not_become_one_continuous_evidence_interval():
    rows = [{'text': EXPLANATION, 'start': 0, 'duration': 5},
            {'text': EXPLANATION + 'A segunda fala ocorre depois da pausa.',
             'start': 200, 'duration': 5}]
    segments = youtube.segment_rows(rows, 'v1')
    assert len(segments) == 2
    assert [(s['start'], s['end']) for s in segments] == [(0, 5), (200, 205)]
    assert any('90 segundos' in warning for warning in source_processing.quality(
        {'segments': segments})['warnings'])


def test_manual_srt_retains_cue_end_and_duration():
    srt = '1\n00:00:10,000 --> 00:00:25,000\n' + EXPLANATION + '\n'
    segments = youtube.manual_segments(srt, 'v1')
    assert segments[0]['start'] == 10
    assert segments[0]['end'] == 25


def test_overlapping_warning_is_bound_without_asserting_factual_error():
    warning = {'start': 0, 'end': 15, 'reason': 'low_confidence'}
    source = {'segments': [{'id': 'v1s1', 'text': EXPLANATION, 'start': 10, 'end': 20}],
              'transcription_warnings': [warning]}
    assert source_processing.confidence_source_ids(source) == ['v1s1']
    quality = source_processing.quality(source)
    assert quality['low_confidence'] == [warning]
    assert quality['completeness'] == 'unverified'
    assert not quality.get('factual_errors')


@pytest.mark.parametrize('document,start,end', [
    ('\ufeff1\r\n00:00:10,250 --> 00:00:25,750\r\n{body}\r\n', 10.25, 25.75),
    ('WEBVTT\n\nintro\n00:10.250 --> 00:25.750 align:start position:10%\n{body}\n', 10.25, 25.75),
    ('WEBVTT - teste\r\n\r\n00:00:10.250 --> 00:00:25.750\r\n{body}\r\n', 10.25, 25.75),
    ('1\n01:02:03,004 --> 01:02:07,500\n{body}\n', 3723.004, 3727.5),
])
def test_manual_caption_variants_keep_valid_times(document, start, end):
    segments = youtube.manual_segments(document.format(body=EXPLANATION.strip()), 'v1')
    assert segments[0]['start'] == start
    assert segments[0]['end'] == end
    assert segments[0]['text'] == EXPLANATION.strip()


def test_vtt_metadata_blocks_do_not_become_spoken_evidence():
    vtt = ('WEBVTT\n\nNOTE comentário privado\nMetadado que não foi falado.\n\n'
           'STYLE\n::cue { color: lime }\n\n'
           'REGION\nid:top\nwidth:60%\n\n'
           'cue-final\n00:10.000 --> 00:25.000 region:top\n' + EXPLANATION.strip())
    segments = youtube.manual_segments(vtt, 'v1')
    assert segments[0]['text'] == EXPLANATION.strip()
    assert (segments[0]['start'], segments[0]['end']) == (10, 25)


@pytest.mark.parametrize('markup', ['<v Ana>{body}</v>', '<v.person Ana><c.red>{body}</c></v>'])
def test_explicit_vtt_voice_is_preserved_without_inferring_channel_authorship(markup):
    document = ('WEBVTT\n\ncue-speaker\n00:10.000 --> 00:25.000\n' +
                markup.format(body=EXPLANATION.strip()))
    segments = youtube.manual_segments(document, 'v1')
    assert segments[0]['text'] == EXPLANATION.strip()
    assert segments[0]['speaker'] == 'Ana'
    assert segments[0]['original_cues'][0]['speaker'] == 'Ana'
    assert segments[0]['original_cues'][0]['original_id'] == 'cue-speaker'
    assert segments[0]['original_cues'][0]['original_text'] == markup.format(body=EXPLANATION.strip())


@pytest.mark.parametrize('markup,speaker,warning_code', [
    ('<v Ana>{body}</v> A próxima instrução não identifica quem falou.', None, 'partial_speaker_attribution'),
    ('<v Ana>{body}</v> <v Bruno>Outra contribuição.</v>', None, 'multiple_speakers'),
    ('<v Ana>{body}', 'Ana', None),
])
def test_partial_and_multiple_voice_annotations_do_not_fabricate_a_single_speaker(markup, speaker, warning_code):
    content = markup.format(body=EXPLANATION.strip())
    document = 'WEBVTT\n\n00:10.000 --> 00:25.000\n' + content
    segments = youtube.manual_segments(document, 'v1')
    assert segments[0]['speaker'] == speaker
    assert segments[0]['original_cues'][0]['speaker'] == speaker
    assert segments[0]['original_cues'][0]['original_text'] == content
    if warning_code:
        assert any(warning['code'] == warning_code for warning in segments[0]['normalization_warnings'])


@pytest.mark.parametrize('start,end', [
    ('00:00:25,000', '00:00:10,000'), ('00:60:10,000', '01:01:10,000'),
    ('00:00:70,000', '00:01:20,000'), ('broken', '00:00:25,000'),
    ('00:00:10,000', 'broken'), ('00:00:10,000', ''),
])
def test_invalid_manual_cue_is_explicitly_rejected(start, end):
    document = f'1\n{start} --> {end}\n{EXPLANATION}'
    with pytest.raises(transcripts.SourceError):
        youtube.manual_segments(document, 'v1')


def test_mixed_valid_and_invalid_cues_do_not_silently_drop_evidence():
    document = ('1\n00:00:10,000 --> 00:00:25,000\n' + EXPLANATION +
                '\n\n2\n00:00:70,000 --> 00:01:20,000\n'
                'Uma condição indispensável aparece no trecho inválido.')
    with pytest.raises(transcripts.SourceError):
        youtube.manual_segments(document, 'v1')


def test_plain_manual_text_never_receives_invented_timestamps():
    text = EXPLANATION + 'A condição é 2 < valor e valor > 1.'
    segments = youtube.manual_segments(text, 'v1')
    assert ''.join(s['text'] for s in segments) == text
    assert all(s['start'] is None and s['end'] is None for s in segments)


def test_plain_engineering_diagram_arrow_is_not_misclassified_as_bad_caption():
    text = EXPLANATION + 'O cliente --> servidor transmite a mensagem de solicitação.'
    segments = youtube.manual_segments(text, 'v1')
    assert segments[0]['text'] == text
    assert segments[0]['start'] is None and segments[0]['end'] is None


def test_orphan_spoken_text_in_caption_document_is_preserved_as_untimed_evidence():
    orphan = 'Uma condição complementar foi fornecida sem intervalo de tempo.'
    document = ('1\n00:00:10,000 --> 00:00:25,000\n' + EXPLANATION.strip() +
                '\n\n' + orphan)
    segments = youtube.manual_segments(document, 'v1')
    assert any(segment['text'] == orphan and segment['start'] is None and segment['end'] is None
               for segment in segments)
    assert any(warning['code'] == 'missing_timestamps' for segment in segments
               for warning in segment.get('normalization_warnings', []))


@pytest.mark.parametrize('second_start,second_end,warning_code', [
    (15, 30, 'overlapping_cues'), (30, 30, 'zero_duration'),
])
def test_overlaps_and_zero_duration_stay_observable(second_start, second_end, warning_code):
    segments = youtube.segment_rows([
        {'text': EXPLANATION, 'start': 10, 'duration': 15},
        {'text': EXPLANATION + 'Segundo trecho.', 'start': second_start,
         'duration': second_end - second_start}], 'v1')
    assert len(segments) == 2
    assert [(s['start'], s['end']) for s in segments] == [(10, 25), (second_start, second_end)]
    assert any(warning['code'] == warning_code for segment in segments
               for warning in segment.get('normalization_warnings', []))


def test_speaker_and_confidence_changes_are_not_erased_by_aggregation():
    rows = [
        {'id': 'native-a', 'text': EXPLANATION, 'start': 0, 'duration': 5,
         'speaker': 'speaker-a', 'confidence': .4},
        {'id': 'native-b', 'text': EXPLANATION + 'Outra explicação.', 'start': 5, 'duration': 5,
         'speaker': 'speaker-b', 'confidence': .9},
    ]
    segments = youtube.segment_rows(rows, 'v1')
    assert len(segments) == 2
    assert [(s['speaker'], s['confidence']) for s in segments] == [('speaker-a', .4), ('speaker-b', .9)]
    assert [s['original_cues'][0]['original_id'] for s in segments] == ['native-a', 'native-b']
    assert [s['original_cues'][0]['original_text'] for s in segments] == [row['text'] for row in rows]


def test_unknown_speaker_and_confidence_are_never_inferred_from_creator():
    segments = youtube.segment_rows([{'text': EXPLANATION, 'start': 0, 'duration': 5}], 'v1')
    assert segments[0]['speaker'] is None
    assert segments[0]['confidence'] is None
    assert segments[0]['original_cues'][0]['speaker'] is None
    assert segments[0]['original_cues'][0]['confidence'] is None


def test_original_cue_references_resolve_to_their_own_interval(job):
    rows = [{'id': 'native-a', 'text': EXPLANATION, 'start': 10, 'duration': 5},
            {'id': 'native-b', 'text': EXPLANATION + 'A ressalva encerra a fala.', 'start': 15, 'duration': 10}]
    source = job['sources'][0]
    source.update(provider='Supadata', normalization_version='source-integrity-v1')
    source['segments'] = youtube.segment_rows(rows, 'v1')
    mapping = generation.evidence_map(job)
    provenance = mapping['v1s1']['provenance']
    assert provenance['source_id'] == 'v1'
    assert provenance['segment_id'] == 'v1s1'
    assert provenance['cue_ids'] == ['v1c1', 'v1c2']
    first = generation.resolve_evidence(job, 'v1c1')
    second = generation.resolve_evidence(job, 'v1c2')
    assert first['video_id'] == second['video_id'] == source['video_id']
    assert first['provider'] == 'Supadata'
    assert (first['start'], first['end']) == (10, 15)
    assert (second['start'], second['end']) == (15, 25)
    assert first['text'] == EXPLANATION.strip()
    assert generation.resolve_evidence(job, 'missing') is None


def test_untimed_legacy_reference_resolves_without_fabricating_provenance(job):
    source = job['sources'][0]
    source['segments'][0].update(start=None, end=None)
    original = deepcopy(job)
    reference = generation.resolve_evidence(job, 'v1s1')
    assert reference['text'] == source['segments'][0]['text']
    assert reference['start'] is None and reference['end'] is None
    assert reference['timing']['availability'] == 'unavailable'
    assert reference['video_id'] == source['video_id']
    assert job == original


def test_ambiguous_or_internal_references_do_not_resolve_as_article_evidence(job):
    duplicate = deepcopy(job['sources'][0])
    duplicate['id'] = 'v2'
    job['sources'].append(duplicate)
    assert generation.resolve_evidence(job, 'v1s1') is None
    job['sources'] = [duplicate]
    duplicate['internal_context_only'] = True
    assert generation.resolve_evidence(job, 'v1s1') is None


def current_automatic_source(job):
    source = deepcopy(job['sources'][0])
    source.update(provider='Whisper local · YouTube', medium='audio', extracted_at=db.now(),
                  normalization_version='source-integrity-v1',
                  normalization_options={'merge_adjacent': True})
    source['segments'] = youtube.segment_rows([
        {'id': 'native-a', 'text': EXPLANATION, 'start': 10, 'duration': 15,
         'speaker': 'speaker-a', 'confidence': .4}], 'v1')
    source['transcription_warnings'] = [{'start': 5, 'end': 20, 'reason': 'low_confidence'}]
    return source


def test_current_source_cache_rebases_every_cue_reference_and_preserves_original_job(job, monkeypatch):
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '1')
    job['sources'] = [current_automatic_source(job)]
    job['sources'][0]['transcription_warnings'][0].update(
        source_ids=['v1s1'], cue_ids=['v1c1'], original_id='v1c1')
    db.save_job(job)
    original = deepcopy(job['sources'])
    cached = source_cache.find_recent('abcdefghijk', 'v2', 'another-job', require_current=True)
    assert cached is not None
    assert cached['id'] == 'v2'
    assert cached['segments'][0]['id'] == 'v2s1'
    assert cached['segments'][0]['cue_ids'] == ['v2c1']
    cue = cached['segments'][0]['original_cues'][0]
    assert cue['id'] == 'v2c1'
    assert cue['original_id'] == 'native-a'
    assert cue['original_cue_id'] == 'v1c1'
    assert cached['segments'][0]['original_segment_id'] == 'v1s1'
    assert cached['original_source_id'] == 'v1'
    assert cached['transcription_warnings'][0] == {
        **original[0]['transcription_warnings'][0], 'source_ids': ['v2s1'], 'cue_ids': ['v2c1']}
    assert cached['reused_from_job_id'] == job['id']
    assert db.get_job(job['id'])['sources'] == original
    reference = generation.resolve_evidence({'sources': [cached]}, 'v2c1')
    assert reference['source_id'] == 'v2'
    assert reference['segment_id'] == 'v2s1'
    assert (reference['start'], reference['end']) == (10, 25)
    assert generation.resolve_evidence({'sources': [cached]}, 'v1c1') is None


def test_legacy_cache_remains_readable_but_is_not_reused_as_current_integrity(job):
    source = deepcopy(job['sources'][0])
    source.update(provider='Whisper local · YouTube', medium='audio', extracted_at=db.now())
    source['segments'][0]['text'] = EXPLANATION
    job['sources'] = [source]
    db.save_job(job)
    original = deepcopy(job['sources'])
    assert source_cache.find_recent('abcdefghijk', 'v2', 'another-job') is not None
    assert source_cache.find_recent('abcdefghijk', 'v2', 'another-job', require_current=True) is None
    assert db.get_job(job['id'])['sources'] == original


def test_cache_selection_respects_normalizer_option_without_mutating_saved_data(job, monkeypatch):
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '1')
    job['sources'] = [current_automatic_source(job)]
    db.save_job(job)
    original = deepcopy(job['sources'])
    assert source_cache.find_recent('abcdefghijk', 'v2', 'another-job', require_current=True) is not None
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '0')
    assert source_cache.find_recent('abcdefghijk', 'v2', 'another-job', require_current=True) is None
    assert db.get_job(job['id'])['sources'] == original


def test_retry_reuses_current_integrity_source_without_network_or_paid_generation(authed, job, monkeypatch):
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '1')
    job['sources'] = [current_automatic_source(job)]
    db.save_job(job)
    failed = deepcopy(job)
    failed.update(id='integrity-retry', status='interrupted', sources=[{
        'id': 'v1', 'video_id': 'abcdefghijk', 'status': 'error', 'segments': []}], usage=[])
    failed.pop('article')
    failed.pop('review')
    db.save_job(failed)
    extract, provider = Mock(), Mock()
    monkeypatch.setattr(youtube, 'extract', extract)
    monkeypatch.setattr(generation, 'structured', provider)
    executor = Mock()
    executor.submit.side_effect = lambda fn, *args: fn(*args)
    monkeypatch.setattr(pipeline, 'executor', executor)
    response = authed.post('/api/jobs/integrity-retry/extract')
    assert response.status_code == 200, response.text
    saved = db.get_job(failed['id'])
    assert saved['status'] == 'sources_ready'
    assert saved['sources'][0]['segments'][0]['cue_ids'] == ['v1c1']
    assert saved['usage'] == []
    extract.assert_not_called()
    provider.assert_not_called()


def test_aggregation_flag_only_disables_grouping_and_keeps_integrity_fixes(monkeypatch):
    monkeypatch.setenv('TRANSCRIPT_AGGREGATE_CUES', '0')
    text = EXPLANATION + 'A condição é 2 < valor e valor > 1.'
    segments = youtube.segment_rows([
        {'text': text, 'start': 10, 'duration': 5},
        {'text': EXPLANATION, 'start': 15, 'duration': 5}], 'v1')
    assert len(segments) == 2
    assert segments[0]['text'] == text
    assert [(s['start'], s['end']) for s in segments] == [(10, 15), (15, 20)]
    assert all(s['normalization_version'] == 'source-integrity-v1' for s in segments)


def test_ai_context_keeps_resolvable_ids_without_duplicating_raw_cue_payload(job):
    job['sources'] = [current_automatic_source(job)]
    original = deepcopy(job)
    segments = job['sources'][0]['segments']
    wire = generation.context(job, {'videos': [{'id': 'v1', 'segments': segments}]})
    payload = json.loads(wire)
    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    fields = set(keys(payload))
    assert 'original_cues' not in fields
    assert 'intervals' not in fields
    assert payload['videos'][0]['segments'][0]['cue_ids'] == ['v1c1']
    assert job == original
    assert job['sources'][0]['segments'][0]['original_cues'][0]['original_text'] == EXPLANATION
    assert job['sources'][0]['segments'][0]['intervals'] == [{'start': 10, 'end': 25}]


def test_editorial_cleaning_keeps_raw_originals_and_resolvable_times():
    text = 'Deixe seu like. ' + EXPLANATION
    segments = youtube.segment_rows([{'text': text, 'start': 10, 'duration': 15}], 'v1')
    original = deepcopy(segments)
    cleaned = source_processing.clean_spoken_transcript(segments)
    assert 'Deixe seu like' not in cleaned[0]['text']
    assert cleaned[0]['original_cues'][0]['original_text'] == text
    assert cleaned[0]['cue_ids'] == ['v1c1']
    assert (cleaned[0]['start'], cleaned[0]['end']) == (10, 25)
    assert segments == original


def test_supadata_metadata_survives_millisecond_conversion_and_normalization():
    result = transcripts.normalize({'content': [
        {'id': 'supadata-cue-4', 'text': EXPLANATION, 'offset': 10250, 'duration': 15500,
         'speaker': 'speaker-b', 'confidence': .35}], 'lang': 'pt'})
    segments = youtube.segment_rows(result['rows'], 'v1')
    assert (segments[0]['start'], segments[0]['end']) == (10.25, 25.75)
    assert segments[0]['speaker'] == 'speaker-b'
    assert segments[0]['confidence'] == .35
    assert segments[0]['original_cues'][0]['original_id'] == 'supadata-cue-4'
    assert segments[0]['original_cues'][0]['original_text'] == EXPLANATION


def test_supadata_unknown_duration_does_not_become_a_confirmed_zero_length_interval():
    result = transcripts.normalize({'content': [{'text': EXPLANATION, 'offset': 10250}], 'lang': 'pt'})
    segments = youtube.segment_rows(result['rows'], 'v1')
    assert segments[0]['start'] == 10.25
    assert segments[0]['end'] is None
    assert segments[0]['duration'] is None
    assert segments[0]['original_cues'][0]['duration'] is None


@pytest.mark.parametrize('warning,segments,expected', [
    ({'start': 0, 'end': 10}, [(10, 20)], []),
    ({'start': 20, 'end': 25}, [(10, 20)], []),
    ({'start': 10, 'end': 10}, [(10, 20)], ['v1s1']),
    ({'start': 20, 'end': 20}, [(10, 20)], []),
    ({'start': 10, 'end': 20}, [(10, 10)], ['v1s1']),
    ({'start': 10, 'end': 20}, [(20, 20)], []),
    ({'start': 10, 'end': 10}, [(10, 10)], ['v1s1']),
    ({'start': 0, 'end': 15}, [(10, 20), (20, 30)], ['v1s1']),
    ({'start': 5, 'end': 25}, [(0, 10), (10, 20), (20, 30)], ['v1s1', 'v1s2', 'v1s3']),
])
def test_warning_interval_boundaries_are_deterministic(warning, segments, expected):
    source = {'segments': [{'id': f'v1s{n}', 'text': EXPLANATION, 'start': start, 'end': end}
                           for n, (start, end) in enumerate(segments, 1)],
              'transcription_warnings': [warning]}
    assert source_processing.confidence_source_ids(source) == expected


def test_unknown_time_warning_remains_global_without_inventing_intervals():
    warning = {'reason': 'weak_transcription', 'start': None, 'end': None}
    source = {'segments': [{'id': 'v1s1', 'text': EXPLANATION, 'start': None, 'end': None}],
              'transcription_warnings': [warning]}
    quality = source_processing.quality(source)
    assert quality['low_confidence'] == [warning]
    assert quality['timestamps'] == 'unavailable'
    assert quality['completeness'] == 'unverified'
    assert quality['global_low_confidence'] == [warning]
    assert source_processing.confidence_source_ids(source) == ['v1s1']
    assert not quality.get('factual_errors')


def test_warning_in_internal_gap_does_not_claim_support_from_outer_interval():
    source = {'segments': [{'id': 'v1s1', 'text': EXPLANATION, 'start': 0, 'end': 205,
                           'intervals': [{'start': 0, 'end': 5}, {'start': 200, 'end': 205}]}],
              'transcription_warnings': [{'start': 10, 'end': 190}]}
    assert source_processing.confidence_source_ids(source) == []
    assert source_processing.quality(source)['low_confidence'] == source['transcription_warnings']


class RenderedHTML(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


@pytest.mark.parametrize('gutenberg', [False, True])
def test_source_math_stays_visible_and_markup_cannot_execute_in_exports(job, gutenberg):
    text = (EXPLANATION + 'A condição é 2 < valor e valor > 1. '
            '<script>alert("x")</script> '
            '<img src=x onerror="alert(1)"> '
            '<a href="javascript:alert(1)">link</a>')
    job['sources'][0]['segments'] = youtube.segment_rows(
        [{'text': text, 'start': 10, 'duration': 15}], 'v1')
    job['article']['markdown'] = job['sources'][0]['segments'][0]['text'] + ' [[v1s1]]'
    rendered = RenderedHTML(publishing.render(job, gutenberg=gutenberg))
    assert '2 < valor e valor > 1' in ''.join(rendered.text)
    assert not any(tag in {'script', 'iframe', 'object', 'embed', 'img'}
                   for tag, attrs in rendered.tags)
    assert not any(key.startswith('on') or value.lower().startswith('javascript:')
                   for tag, attrs in rendered.tags for key, value in attrs.items())


@pytest.mark.parametrize('format', ['markdown', 'html', 'json', 'wordpress', 'wordpress-html'])
def test_legacy_source_and_editorial_warning_still_export_without_regeneration(authed, job, monkeypatch, format):
    source = job['sources'][0]
    source['segments'][0].pop('end')
    job['status'] = 'needs_review'
    job['review']['findings'] = [{'severity': 'blocking', 'reason': 'Conferir a fala.',
                                'source_ids': ['v1s1'], 'category': 'transcription_uncertainty'}]
    original_source = deepcopy(source)
    original_article = deepcopy(job['article'])
    db.save_job(job)
    extract, provider = Mock(), Mock()
    monkeypatch.setattr(youtube, 'extract', extract)
    monkeypatch.setattr(generation, 'structured', provider)
    response = authed.get(f'/api/jobs/{job["id"]}/export', params={'format': format})
    assert response.status_code == 200, response.text
    saved = db.get_job(job['id'])
    assert saved['sources'][0] == original_source
    assert saved['article'] == original_article
    assert saved['usage'] == []
    extract.assert_not_called()
    provider.assert_not_called()
