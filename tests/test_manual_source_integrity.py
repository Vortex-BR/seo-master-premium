"""Manual input validates atomically and replaces source ownership explicitly."""
from copy import deepcopy

from app import db, generation, youtube


def test_malformed_caption_cannot_archive_or_mutate_saved_work(authed, job, monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: (_ for _ in ()).throw(AssertionError('Provider called')))
    before = db.get_job(job['id'])
    revisions = db.revisions(job['id'])
    text = '1\n00:00:25,000 --> 00:00:10,000\n' + 'A condição deve ser explicada com base na fonte original. ' * 3
    response = authed.post(f'/api/jobs/{job["id"]}/source', json={'video_id': 'abcdefghijk', 'text': text})
    assert response.status_code == 400
    assert db.get_job(job['id']) == before
    assert db.revisions(job['id']) == revisions


def test_manual_replacement_preserves_old_article_and_original_source_history(authed, job):
    source = job['sources'][0]
    source.update(original_source_id='v2', reused_from_source_id='v3',
                  reused_from_job_id='another-job', reused_at=db.now(), medium='audio',
                  provider_adapter_version='supadata-rows-legacy', provider_cache_legacy=True,
                  transcription_warnings=[{'start': 0, 'end': 15, 'reason': 'Verifique o áudio'}])
    db.save_job(job)
    before = deepcopy(source)
    text = '1\n00:00:10,000 --> 00:00:25,000\n' + 'A condição 2 < valor < 10 deve ser explicada. ' * 3
    response = authed.post(f'/api/jobs/{job["id"]}/source', json={'video_id': 'abcdefghijk', 'text': text})
    assert response.status_code == 200
    saved = db.get_job(job['id'])
    current = saved['sources'][0]
    assert saved['article'] == job['article']
    assert saved['source_history'][-1]['source'] == before
    assert saved['review'] is None and saved['article_needs_generation'] is True
    assert current['normalization_version'] == youtube.NORMALIZATION_VERSION
    assert current['normalization_options'] == youtube.normalization_options()
    assert current['input_origin'] == 'manual_text'
    assert current['segments'][0]['end'] == 25
    assert '2 < valor < 10' in current['segments'][0]['text']
    assert not any(key in current for key in ('original_source_id', 'reused_from_source_id',
                                            'reused_from_job_id', 'reused_at', 'transcription_warnings', 'medium',
                                            'provider_adapter_version', 'provider_cache_legacy'))
    assert authed.get(f'/api/jobs/{job["id"]}/export?format=html').status_code == 200
