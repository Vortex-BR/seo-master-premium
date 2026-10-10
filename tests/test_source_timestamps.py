"""Offline temporal validation is distinct from semantic video verification."""
from copy import deepcopy

import pytest

from app import generation, publishing
from app.seo import checks


def timed_job(stamp='[03:45]', start=225, end=230, refs='[[v1s1]]'):
    return {
        'id': 'time-fixture', 'brief': {}, 'editorial': {'video_first': True},
        'sources': [{'id': 'v1', 'author': 'Criador', 'title': 'Condição',
                     'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                     'segments': [{'id': 'v1s1', 'text': 'Confira a condição.',
                                   'start': start, 'end': end}]}],
        'article': {'title': 'Condição', 'seo_title': 'Condição', 'slug': 'condicao',
                    'meta_description': 'Confira a condição.', 'excerpt': 'Condição.', 'tags': [],
                    'markdown': f'Criador explica a condição.\n\nConfira a condição {stamp} {refs}'},
    }


def alignment(job):
    return next(row for row in checks.video_first_checks(job)
                if row['rule_id'] == 'video_first.timestamp_alignment')


def test_valid_syntax_does_not_certify_time_and_never_blocks_export(monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('Provider called'))
    job = timed_job('[99:59]')
    rows = {row['rule_id']: row for row in checks.video_first_checks(job)}
    assert rows['video_first.timestamp_presence']['status'] == 'pass'
    assert alignment(job)['status'] == 'fail'
    findings = checks.blocking_findings(job)
    assert findings and all(item['export_blocking'] is False for item in findings)
    assert 'Confira a condição' in publishing.render(job)


@pytest.mark.parametrize(('stamp', 'start', 'end'), [
    ('[03:45]', 225, 230), ('[03:45]', 225.95, 230),
    ('[1:03:45]', 3825, 3830), ('[03:45]', 225, 225),
])
def test_supported_time_accepts_integer_locators_and_points(stamp, start, end):
    assert alignment(timed_job(stamp, start, end))['status'] == 'pass'


@pytest.mark.parametrize(('start', 'end'), [
    (None, None), (225, None), (None, 230), (230, 225), (float('nan'), 230), (True, 230),
])
def test_unknown_or_invalid_legacy_times_warn_without_false_certainty(start, end):
    job = timed_job(start=start, end=end)
    assert alignment(job)['status'] == 'warning'
    assert checks.blocking_findings(job) == []


def test_positive_interval_end_is_exclusive():
    assert alignment(timed_job('[03:50]'))['status'] == 'fail'


def test_original_intervals_prevent_certifying_a_gap():
    job = timed_job('[01:40]', start=0, end=205)
    job['sources'][0]['segments'][0]['intervals'] = [
        {'start': 0, 'end': 5}, {'start': 200, 'end': 205}]
    assert alignment(job)['status'] == 'fail'


def test_timestamp_must_match_the_cited_video():
    job = timed_job('[00:02]')
    other = deepcopy(job['sources'][0])
    other.update(id='v2', segments=[{'id': 'v2s1', 'text': 'Outra condição.', 'start': 0, 'end': 5}])
    job['sources'].append(other)
    assert alignment(job)['status'] == 'fail'
    job['article']['markdown'] = job['article']['markdown'].replace('[[v1s1]]', '[[v2s1]]')
    assert alignment(job)['status'] == 'pass'


@pytest.mark.parametrize('refs', ['', '\n\n[[v1s1]]'])
def test_absent_or_other_paragraph_citation_is_unresolved(refs):
    assert alignment(timed_job(refs=refs))['status'] == 'warning'


@pytest.mark.parametrize(('stamp', 'refs'), [('[03:45]', '`[[v1s1]]`'), ('`[03:45]`', '[[v1s1]]')])
def test_inline_code_is_not_temporal_evidence(stamp, refs):
    assert alignment(timed_job(stamp=stamp, refs=refs))['status'] == 'warning'


def test_alignment_does_not_claim_semantic_or_visual_verification():
    detail = alignment(timed_job())['detail']
    assert 'não certifica a fidelidade semântica' in detail
    assert 'nem analisa imagens' in detail


def test_legacy_non_video_first_mode_is_unchanged():
    job = timed_job()
    job['editorial']['video_first'] = False
    assert checks.video_first_checks(job) == []


@pytest.mark.parametrize('scope', ['source', 'segment'])
def test_internal_only_material_cannot_certify_article_time(scope):
    job = timed_job()
    record = job['sources'][0] if scope == 'source' else job['sources'][0]['segments'][0]
    record['internal_context_only'] = True
    assert generation.resolve_evidence(job, 'v1s1') is None
    assert alignment(job)['status'] == 'warning'
    rows = {row['rule_id']: row for row in checks.video_first_checks(job)}
    assert rows['video_first.video_evidence']['status'] == 'fail'
    assert all(item['export_blocking'] is False for item in checks.blocking_findings(job))


def test_ambiguous_segment_id_cannot_certify_article_time():
    job = timed_job()
    job['sources'].append(deepcopy(job['sources'][0]))
    assert alignment(job)['status'] == 'warning'
    rows = {row['rule_id']: row for row in checks.video_first_checks(job)}
    assert rows['video_first.video_evidence']['status'] == 'fail'


@pytest.mark.parametrize('intervals', [{'start': 225, 'end': 230}, [None], ['invalid']])
def test_invalid_legacy_interval_metadata_is_uncertain(intervals):
    job = timed_job()
    job['sources'][0]['segments'][0]['intervals'] = intervals
    assert alignment(job)['status'] == 'warning'
