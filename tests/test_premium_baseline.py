"""Research snapshots must not mutate app data or invent baseline measurements."""
from copy import deepcopy
import json
import sqlite3

import pytest

from scripts import premium_baseline as baseline


def case():
    return {'id': 'principal', 'url': 'https://www.youtube.com/watch?v=cVnRvZ8uMCo',
            'category': 'tutorial', 'permission': {'status': 'authorized', 'basis': 'fixture ownership',
            'reference': 'test-only'}, 'job_id': 'job-1',
            'essential_excerpts': [{'source_id': 'v1s1', 'text': 'Fixture'}],
            'run': {'app_sha256': 'current', 'model': 'fixture-model', 'profile': {'context_chars': 90000},
                    'wall_seconds': 1.5, 'usage_start_index': 0, 'usage_end_index': 1}}


def database(tmp_path):
    path = tmp_path / 'fixture.sqlite3'
    job = {'id': 'job-1', 'status': 'needs_review', 'secret': 'do-not-copy',
           'article': {'title': 'Fixture', 'slug': 'fixture', 'markdown': 'Fixture body'},
           'sources': [{'video_id': 'cVnRvZ8uMCo', 'id': 'v1', 'secret': 'do-not-copy',
                        'segments': [{'id': 'v1s1', 'text': 'Fixture', 'start': 1, 'end': 2}]}],
           'usage': [{'input_tokens': 10, 'output_tokens': 5}]}
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT)')
        conn.execute('INSERT INTO jobs VALUES (?,?)', ('job-1', json.dumps(job)))
    return path


def inspect(value):
    return baseline.inspect_manifest({'schema_version': 1, 'cases': [value]})['cases'][0]


def test_pending_rights_never_reads_database(tmp_path, monkeypatch):
    value = case()
    value['permission']['status'] = 'pending'
    monkeypatch.setattr(baseline, 'read_job', lambda *args: pytest.fail('Pending rights read database'))
    snapshot, issues = baseline.capture_case(value, inspect(value), tmp_path / 'missing.db', 'current')
    assert snapshot is None and issues


def test_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(ValueError, match='Nenhum banco foi criado'):
        baseline.read_job(path, 'job-1')
    assert not path.exists()


def test_capture_read_only_omits_secrets_and_preserves_unknown_cost(tmp_path):
    path = database(tmp_path)
    before = path.read_bytes()
    snapshot, issues = baseline.capture_case(case(), inspect(case()), path, 'current')
    assert any('Custo' in issue for issue in issues)
    assert path.read_bytes() == before
    assert 'do-not-copy' not in json.dumps(snapshot)
    assert snapshot['baseline_metrics']['estimated_usage_usd'] is None
    assert snapshot['baseline_metrics']['known_input_tokens'] == 10
    assert snapshot['status'] == 'needs_review'


@pytest.mark.parametrize('permission', [
    {'status': 'authorized', 'basis': '', 'reference': 'test'},
    {'status': 'authorized', 'basis': 'test', 'reference': None},
    {'status': 'denied', 'basis': 'test', 'reference': 'test'},
])
def test_authorized_label_without_record_is_not_enough(permission):
    value = case()
    value['permission'] = permission
    assert not inspect(value)['authorized']


def test_repeated_video_does_not_complete_sample():
    second = deepcopy(case())
    second['id'] = 'secondary'
    report = baseline.inspect_manifest({'schema_version': 1, 'cases': [case(), second]})
    assert report['unique_videos'] == 1
    assert any('repetido' in issue for issue in report['cases'][1]['issues'])


def test_unsafe_case_id_cannot_write_outside_output():
    value = case()
    value['id'] = '../escape'
    with pytest.raises(ValueError, match='seguros'):
        inspect(value)


@pytest.mark.parametrize('ident', ['CON', 'nul', 'COM1', 'LPT9'])
def test_windows_reserved_case_names_are_rejected(ident):
    value = case()
    value['id'] = ident
    with pytest.raises(ValueError, match='seguros'):
        inspect(value)


def test_windows_case_insensitive_duplicate_ids_are_rejected():
    second = deepcopy(case())
    second['id'] = case()['id'].upper()
    with pytest.raises(ValueError, match='únicos'):
        baseline.inspect_manifest({'schema_version': 1, 'cases': [case(), second]})


def test_version_and_source_mismatch_do_not_certify_current_baseline(tmp_path):
    path = database(tmp_path)
    snapshot, issues = baseline.capture_case(case(), inspect(case()), path, 'new-code')
    assert snapshot and any('Versão' in issue for issue in issues)
    value = case()
    value['url'] = 'https://youtu.be/uibZD5Dgrao'
    snapshot, issues = baseline.capture_case(value, inspect(value), path, 'current')
    assert snapshot is None and any('fontes' in issue for issue in issues)


def test_secondary_source_without_rights_is_not_copied(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        job = json.loads(conn.execute('SELECT data FROM jobs').fetchone()[0])
        job['sources'].append({'video_id': 'uibZD5Dgrao', 'segments': [{'id': 'v2s1', 'text': 'unrecorded source'}]})
        conn.execute('UPDATE jobs SET data=?', (json.dumps(job),))
    snapshot, issues = baseline.capture_case(case(), inspect(case()), path, 'current')
    assert snapshot is None and any('Todas as fontes' in issue for issue in issues)


def test_essential_excerpt_must_match_real_source_segment(tmp_path):
    value = case()
    value['essential_excerpts'][0]['text'] = 'Invented words'
    snapshot, issues = baseline.capture_case(value, inspect(value), database(tmp_path), 'current')
    assert snapshot and any('literalmente' in issue for issue in issues)


def test_dirty_app_bytes_change_fingerprint(tmp_path):
    app = tmp_path / 'app'
    app.mkdir()
    source = app / 'sample.py'
    source.write_text('before', encoding='utf-8')
    first = baseline.fingerprint(tmp_path)
    source.write_text('after', encoding='utf-8')
    second = baseline.fingerprint(tmp_path)
    assert first['app_sha256'] != second['app_sha256']
    assert second['files'][0]['path'] == 'app/sample.py'


def test_incomplete_report_and_human_scores_are_explicit(tmp_path):
    value = case()
    value['permission']['status'] = 'pending'
    root = tmp_path / 'repo'
    (root / 'app').mkdir(parents=True)
    folder, report = baseline.execute({'schema_version': 1, 'cases': [value]}, None, tmp_path / 'out', root)
    sheet = json.loads((folder / 'cases/principal/human-review.json').read_text(encoding='utf-8'))
    assert report['status'] == 'incomplete'
    assert not report['demonstrated_improvement'] and not report['human_evaluation_completed']
    assert report['paid_calls'] == report['provider_calls'] == 0
    assert all(d['score_0_to_5'] is None for d in sheet['dimensions'].values())
    assert sheet['export_blocking'] is False


def test_case_names_cannot_overwrite_report_or_other_reviews(tmp_path):
    cases = []
    for ident in ('report', 'manifest', 'fingerprint', 'x', 'x-human-review'):
        value = case()
        value['id'] = ident
        value['permission']['status'] = 'pending'
        cases.append(value)
    root = tmp_path / 'repo'
    (root / 'app').mkdir(parents=True)
    folder, report = baseline.execute({'schema_version': 1, 'cases': cases}, None, tmp_path / 'out', root)
    assert json.loads((folder / 'report.json').read_text(encoding='utf-8')) == report
    assert all((folder / 'cases' / c['id'] / 'human-review.json').is_file() for c in cases)


def test_unrecorded_run_range_does_not_claim_execution_cost(tmp_path):
    value = case()
    value['run'].pop('usage_start_index')
    snapshot, issues = baseline.capture_case(value, inspect(value), database(tmp_path), 'current')
    assert snapshot['baseline_metrics']['usage_scope'] == 'article_history'
    assert any('histórico do artigo' in issue for issue in issues)


def test_stale_article_remains_captured_with_explicit_baseline_warning(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        job = json.loads(conn.execute('SELECT data FROM jobs').fetchone()[0])
        job['article_needs_generation'] = True
        conn.execute('UPDATE jobs SET data=?', (json.dumps(job),))
    snapshot, issues = baseline.capture_case(case(), inspect(case()), path, 'current')
    assert snapshot['article_needs_generation'] is True
    assert any('entradas atuais' in issue for issue in issues)


def test_one_video_pilot_does_not_require_twelve_or_optional_cases(tmp_path):
    value = case()
    value['permission']['status'] = 'pending'
    secondary = deepcopy(value)
    secondary['id'] = 'optional'
    secondary['url'] = 'https://youtu.be/uibZD5Dgrao'
    manifest = {'schema_version': 1, 'requirements': {'minimum_unique_videos': 1,
                'minimum_per_category': 0, 'required_challenges': [], 'required_case_ids': ['principal']},
                'cases': [value, secondary]}
    report = baseline.inspect_manifest(manifest)
    assert not report['issues']
    assert report['required_videos'] == 1
    assert report['cases'][0]['required'] and not report['cases'][1]['required']


def test_read_only_json_snapshot_preserves_real_source_without_invented_article(tmp_path):
    path = database(tmp_path)
    job = baseline.read_job(path, 'job-1')
    job['article'] = None
    source_json = tmp_path / 'sources.json'
    source_json.write_text(json.dumps(job), encoding='utf-8')
    before = source_json.read_bytes()
    value = case()
    value['job_json'] = str(source_json)
    snapshot, issues = baseline.capture_case(value, inspect(value), None, 'current')
    assert snapshot['sources'] and snapshot['article'] is None
    assert source_json.read_bytes() == before
    assert any('Artigo gerado' in issue for issue in issues)


def test_single_video_case_diagnoses_multi_video_article(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        job = json.loads(conn.execute('SELECT data FROM jobs').fetchone()[0])
        job['sources'].append({'video_id': 'uibZD5Dgrao', 'segments': []})
        conn.execute('UPDATE jobs SET data=?', (json.dumps(job),))
    value = case()
    value['exclusive_source'] = True
    snapshot, issues = baseline.capture_case(value, inspect(value), path, 'current',
                                              {'cVnRvZ8uMCo', 'uibZD5Dgrao'})
    assert snapshot and any('somente no vídeo' in issue for issue in issues)


def test_changed_source_snapshot_does_not_pass_recorded_hash(tmp_path):
    source_json = tmp_path / 'sources.json'
    source_json.write_text('{}', encoding='utf-8')
    value = case()
    value['job_json'] = str(source_json)
    value['transcript_snapshot'] = {'sha256': 'not-the-source-hash'}
    with pytest.raises(ValueError, match='diverge do hash'):
        baseline.capture_case(value, inspect(value), None, 'current')


def test_complete_primary_case_ignores_missing_optional_generation(tmp_path):
    root = tmp_path / 'repo'
    (root / 'app').mkdir(parents=True)
    path = database(tmp_path)
    with sqlite3.connect(path) as conn:
        job = json.loads(conn.execute('SELECT data FROM jobs').fetchone()[0])
        job['usage'][0]['estimated_usd'] = 0.02
        conn.execute('UPDATE jobs SET data=?', (json.dumps(job),))
    primary = case()
    primary['run']['app_sha256'] = baseline.fingerprint(root)['app_sha256']
    optional = deepcopy(primary)
    optional.update(id='optional', url='https://youtu.be/uibZD5Dgrao', job_id=None)
    manifest = {'schema_version': 1, 'requirements': {'minimum_unique_videos': 1,
                'minimum_per_category': 0, 'required_challenges': [], 'required_case_ids': ['principal']},
                'cases': [primary, optional]}
    _, report = baseline.execute(manifest, path, tmp_path / 'output', root)
    assert report['status'] == 'ready_for_human_evaluation'
    assert report['cases'][1]['issues']
    assert report['human_evaluation_completed'] is False
    assert report['demonstrated_improvement'] is False


def test_optional_cases_cannot_supply_required_category_or_challenge():
    primary, optional = case(), deepcopy(case())
    optional.update(id='optional', url='https://youtu.be/uibZD5Dgrao', category='interview',
                    challenges=['visual_demonstration'])
    manifest = {'schema_version': 1, 'requirements': {'minimum_unique_videos': 1,
                'minimum_per_category': 1, 'required_challenges': ['visual_demonstration'],
                'required_case_ids': ['principal']}, 'cases': [primary, optional]}
    report = baseline.inspect_manifest(manifest)
    assert report['category_counts']['interview'] == 0
    assert any('visual_demonstration' in issue for issue in report['issues'])


def test_conflicting_rights_for_same_video_prevent_all_captures(tmp_path, monkeypatch):
    primary, optional = case(), deepcopy(case())
    optional['id'] = 'optional'
    optional['permission']['status'] = 'denied'
    manifest = {'schema_version': 1, 'requirements': {'required_case_ids': ['principal']},
                'cases': [primary, optional]}
    monkeypatch.setattr(baseline, 'read_job', lambda *args: pytest.fail('Denied source read database'))
    root = tmp_path / 'repo'
    (root / 'app').mkdir(parents=True)
    _, report = baseline.execute(manifest, tmp_path / 'missing.db', tmp_path / 'out', root)
    assert report['authorized_cases'] == 0
    assert all(not c['snapshot_available'] for c in report['cases'])


@pytest.mark.parametrize('field,value', [
    ('sources', [None]), ('usage', [None]), ('sources', [{'segments': [None]}]),
    ('article', []), ('editorial', []), ('sources', [{'segments': [{'text': 123}]}]),
    ('sources', None), ('usage', None), ('sources', [{'segments': None}]),
    ('sources', [{'segments': [{'text': 'Missing ID'}]}]),
    ('sources', [{'segments': [{'id': 'v1s1', 'text': 'First'}, {'id': 'v1s1', 'text': 'Duplicate'}]}]),
])
def test_malformed_snapshot_has_diagnostic_without_aborting_report(tmp_path, field, value):
    job = {'id': 'job-1', 'sources': [], 'usage': [], 'article': None}
    job[field] = value
    path = tmp_path / 'malformed.json'
    path.write_text(json.dumps(job), encoding='utf-8')
    entry = case()
    entry['job_json'] = str(path)
    root = tmp_path / 'repo'
    (root / 'app').mkdir(parents=True)
    folder, report = baseline.execute({'schema_version': 1, 'cases': [entry]}, None, tmp_path / 'out', root)
    assert (folder / 'report.json').exists()
    assert not report['cases'][0]['snapshot_available']
    assert report['cases'][0]['issues']


def test_malformed_requirements_are_rejected_before_output_creation():
    with pytest.raises(ValueError, match='requirements'):
        baseline.inspect_manifest({'schema_version': 1, 'requirements': [], 'cases': [case()]})
