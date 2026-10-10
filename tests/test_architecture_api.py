"""Real routes and SQLite: edit conflicts, atomicity and read-only polling."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

from app import db, generation


def edit_body(job, title):
    return {**job['article'], 'title': title,
            'base_article_hash': generation.article_hash(job['article'])}


def test_two_tabs_preserve_saved_version_and_refuse_stale_overwrite(authed, job):
    first = edit_body(job, 'Versão salva pela primeira aba')
    second = edit_body(job, 'Rascunho da segunda aba')
    loaded = authed.get('/api/jobs/test-job')
    assert loaded.headers['etag'] == '"' + first['base_article_hash'] + '"'
    assert authed.put('/api/jobs/test-job/article', json=first).status_code == 200
    before = db.get_job(job['id'])
    revisions = db.revisions(job['id'])
    response = authed.put('/api/jobs/test-job/article', json=second)
    assert response.status_code == 409
    assert db.get_job(job['id']) == before
    assert db.revisions(job['id']) == revisions
    assert before['article']['title'] == first['title']
    assert revisions[0]['data'] == job['article']
    assert second['title'] == 'Rascunho da segunda aba'


def test_simultaneous_edits_only_commit_one_base_version(authed, job):
    bodies = [edit_body(job, 'Edição concorrente ' + str(i)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda body: authed.put(
            '/api/jobs/test-job/article', json=body).status_code, bodies))
    assert sorted(statuses) == [200, 409]
    assert len(db.revisions(job['id'])) == 1


def test_edit_requires_base_and_accepts_strong_if_match(authed, job):
    assert authed.put('/api/jobs/test-job/article', json=job['article']).status_code == 428
    base = generation.article_hash(job['article'])
    body = {**job['article'], 'title': 'Versão condicional via ETag'}
    saved = authed.put('/api/jobs/test-job/article', json=body, headers={'If-Match': '"' + base + '"'})
    assert saved.status_code == 200
    assert saved.json()['article_hash'] == generation.article_hash(body)
    assert authed.put('/api/jobs/test-job/article', json=body,
                      headers={'If-Match': '"' + base + '"'}).status_code == 409


def test_edit_rolls_back_revision_and_review_if_save_fails(authed, job):
    previous = deepcopy(db.get_job(job['id']))
    with patch('app.pipeline.step', side_effect=ValueError('Falha controlada ao salvar')):
        response = authed.put('/api/jobs/test-job/article', json=edit_body(job, 'Título não confirmado'))
    assert response.status_code == 400
    assert db.get_job(job['id']) == previous
    assert db.revisions(job['id']) == []
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM editorial_artifacts').fetchone()[0] == 0


def database_snapshot():
    with db.connect() as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [tuple(r) for r in c.execute('SELECT * FROM "' + name + '"')]
                for name in names}


def test_poll_and_profile_reads_do_not_import_ledger_or_write_snapshots(authed, job):
    job['usage'] = [{'model': 'gpt-4.1-mini', 'stage': 'writing',
                     'input_tokens': 1000, 'output_tokens': 100, 'response_id': 'legacy-response'}]
    db.save_job(job)
    before = database_snapshot()
    for url in ('/api/jobs/test-job', '/api/jobs/test-job/estimate', '/api/jobs/test-job/team', '/api/editorial/profile'):
        assert authed.get(url).status_code == 200
    assert database_snapshot() == before
    assert authed.get('/api/jobs/test-job').json()['spending']['spent_usd'] > 0


def test_unmatched_header_and_body_version_do_not_change_article(authed, job):
    before = db.get_job(job['id'])
    response = authed.put('/api/jobs/test-job/article', json=edit_body(job, 'Título conflitante'),
                          headers={'If-Match': '"' + '0' * 64 + '"'})
    assert response.status_code == 400
    assert db.get_job(job['id']) == before
