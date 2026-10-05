from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline, source_cache, youtube


def cached_source(job, **changes):
    source = deepcopy(job['sources'][0])
    source.update(provider='Legendas do YouTube via proxy', extracted_at=db.now(), **changes)
    source['segments'][0]['text'] = 'Este texto de referência sobre um tema de exemplo foi extraído automaticamente para verificar o reaproveitamento de fontes.'
    return source


def test_cache_rebases_citations_and_keeps_timestamps_and_origin(job):
    job['sources'] = [cached_source(job)]
    db.save_job(job)
    source = source_cache.find_recent('abcdefghijk', 'v2', 'another-job')
    assert source['id'] == 'v2'
    assert source['segments'][0]['id'] == 'v2s1'
    assert source['segments'][0]['start'] == 10
    assert source['segments'][0]['text'] == job['sources'][0]['segments'][0]['text']
    assert source['reused_from_job_id'] == job['id']
    assert source['extracted_at'] == job['sources'][0]['extracted_at']
    assert source_cache.find_recent('differentid', 'v1', 'another-job') is None
    assert source_cache.find_recent('abcdefghijk', 'v1', job['id']) is None
    assert db.get_job(job['id'])['sources'] == job['sources']


@pytest.mark.parametrize('change', [
    {'provider': 'Transcrição fornecida pelo usuário'}, {'status': 'error'}, {'segments': []},
    {'extracted_at': 'invalid'}, {'extracted_at': '2020-01-01T00:00:00+00:00'},
    {'extracted_at': '2099-01-01T00:00:00+00:00'},
])
def test_cache_excludes_manual_failed_empty_and_stale_transcripts(job, change):
    job['sources'] = [cached_source(job) | change]
    db.save_job(job)
    assert source_cache.find_recent('abcdefghijk', 'v1', 'another-job') is None


def test_legacy_source_uses_original_job_creation_time(job):
    job['sources'] = [cached_source(job)]
    job['sources'][0].pop('extracted_at')
    db.save_job(job)
    assert source_cache.find_recent('abcdefghijk', 'v1', 'another-job')['extracted_at'] == job['created_at']
    job['created_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    db.save_job(job)  # Updating the job must not renew the cached transcript's age.
    assert source_cache.find_recent('abcdefghijk', 'v1', 'another-job') is None


def test_retry_reuses_cache_without_network_or_generation(authed, job, monkeypatch):
    job['sources'] = [cached_source(job)]
    db.save_job(job)
    failed = deepcopy(job)
    failed.update(id='failed-job', status='error', usage=[], error='Blocked', sources=[{
        'id': 'v1', 'video_id': 'abcdefghijk', 'status': 'error', 'segments': [], 'error': 'Blocked'}])
    failed.pop('article')
    failed.pop('review')
    db.save_job(failed)
    extract, analyze = Mock(), Mock()
    monkeypatch.setattr(youtube, 'extract', extract)
    monkeypatch.setattr(generation, 'extract_dossier', analyze)
    executor = Mock()
    executor.submit.side_effect = lambda fn, *args: fn(*args)
    monkeypatch.setattr(pipeline, 'executor', executor)
    assert authed.post('/api/jobs/failed-job/extract').status_code == 200
    saved = db.get_job('failed-job')
    assert saved['status'] == 'sources_ready'
    assert saved['error'] is None
    assert saved['usage'] == []
    assert 'article' not in saved
    assert saved['sources'][0]['reused_from_job_id'] == job['id']
    extract.assert_not_called()
    analyze.assert_not_called()


def test_uncached_extraction_preserves_the_diagnostic_in_main_error(job, monkeypatch):
    job['sources'] = []
    db.save_job(job)
    monkeypatch.setattr(youtube, 'extract', Mock(side_effect=youtube.SourceError('YouTube: RequestBlocked')))
    monkeypatch.setattr(youtube, 'metadata', lambda vid: {'video_id': vid, 'url': job['brief']['urls'][0]})
    pipeline.run(job['id'], 'extract')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'error'
    assert 'RequestBlocked' in saved['error']


def test_successful_fresh_extraction_records_original_time(job, monkeypatch):
    source = cached_source(job)
    source.pop('extracted_at')
    job['sources'] = []
    db.save_job(job)
    monkeypatch.setattr(youtube, 'extract', lambda *args: deepcopy(source))
    pipeline.run(job['id'], 'extract')
    saved = db.get_job(job['id'])
    assert saved['sources'][0]['extracted_at']
    assert 'reused_at' not in saved['sources'][0]
    assert saved['status'] == 'sources_ready'
