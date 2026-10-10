"""Offline HKL inspection must recover literal originals without application writes."""
from copy import deepcopy
import hashlib
import json
import sys
from unittest.mock import Mock

import pytest

from app import db, generation
from app.editorial import human_knowledge as hkl
from scripts import human_knowledge as cli


@pytest.fixture
def offline_job(tmp_path, monkeypatch):
    value = {'id': 'offline-inspection', 'brief': {}, 'sources': [{
        'id': 'v1', 'video_id': 'abcdefghijk', 'author': 'Canal declarado',
        'title': 'Teste', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
        'segments': [{'id': 'v1s1', 'text': 'No meu caso, funcionou somente se a porta estava aberta.',
                      'start': 2.5, 'end': 9.0}]}]}
    path = tmp_path / 'job.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(db, 'connect', Mock(side_effect=AssertionError('No application DB operation.')))
    monkeypatch.setattr(db, 'init', Mock(side_effect=AssertionError('No schema initialization.')))
    monkeypatch.setattr(generation, 'client', Mock(side_effect=AssertionError('No paid provider.')))
    return value, path


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['human_knowledge.py', *map(str, args)])
    return cli.main()


def test_offline_projection_preserves_input_and_has_resolvable_single_video(
        offline_job, tmp_path, monkeypatch):
    original, source = offline_job
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'inspection.json'
    assert invoke(monkeypatch, '--job-json', source, '--output', output) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['schema_version'] == 'human_knowledge.v1'
    assert result['independent_source'] is False
    assert len(result['sources']) == 1
    assert result['units'][0]['text'] == ''  # Original speech stays in its source.
    resolved = hkl.resolve_unit(original, result, result['units'][0]['id'])
    assert resolved['status'] == 'resolved'
    assert resolved['records'][0]['text'] == original['sources'][0]['segments'][0]['text']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == initial_hash
    db.connect.assert_not_called()
    db.init.assert_not_called()
    generation.client.assert_not_called()


def test_historical_resolution_requires_matching_original_snapshot(
        offline_job, tmp_path, monkeypatch):
    original, source = offline_job
    dossier = hkl.project(original)
    unit_id = dossier['units'][0]['id']
    dossier_path = tmp_path / 'old-dossier.json'
    dossier_path.write_text(json.dumps(dossier), encoding='utf-8')
    changed = deepcopy(original)
    changed['sources'][0]['segments'][0]['text'] = 'Uma fala diferente, com a mesma referência.'
    source.write_text(json.dumps(changed), encoding='utf-8')
    output = tmp_path / 'resolved.json'
    args = ('--job-json', source, '--output', output, '--dossier-json', dossier_path, '--unit-id', unit_id)
    assert invoke(monkeypatch, *args) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['status'] == 'stale'
    snapshot_path = tmp_path / 'old-sources.json'
    snapshot_path.write_text(json.dumps({'schema_version': 'human_knowledge_sources.v1',
                                         'sources': original['sources']}), encoding='utf-8')
    assert invoke(monkeypatch, *args, '--source-snapshot', snapshot_path) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'resolved'
    assert result['records'][0]['text'] == original['sources'][0]['segments'][0]['text']
    assert json.loads(source.read_text(encoding='utf-8')) == changed
    db.connect.assert_not_called()
    generation.client.assert_not_called()


def test_cli_refuses_to_overwrite_original_job_before_reading_it(offline_job, monkeypatch):
    _, source = offline_job
    before = source.read_bytes()
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, '--job-json', source, '--output', source)
    assert exc.value.code == 2
    assert source.read_bytes() == before


def test_cli_requires_unit_for_saved_dossier(offline_job, tmp_path, monkeypatch):
    _, source = offline_job
    output = tmp_path / 'unused.json'
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, '--job-json', source, '--output', output, '--dossier-json', source)
    assert exc.value.code == 2
    assert not output.exists()


def test_cli_validation_failure_redacts_private_input_and_preserves_existing_output(
        offline_job, tmp_path, monkeypatch, capsys):
    original, source = offline_job
    malformed = deepcopy(original)
    marker = 'PRIVATE_SOURCE_VALIDATION_MARKER'
    malformed['apuration'] = {'items': [{'statement': {'private_content': marker}}]}
    source.write_text(json.dumps(malformed), encoding='utf-8')
    before = source.read_bytes()
    output = tmp_path / 'inspection.json'
    output.write_text('previous-private-result', encoding='utf-8')
    assert invoke(monkeypatch, '--job-json', source, '--output', output) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    error = json.loads(captured.err)
    assert error['error_type'] == 'ValidationError'
    assert marker not in captured.err and 'input_value' not in captured.err
    assert 'Traceback' not in captured.err
    assert source.read_bytes() == before
    assert output.read_text(encoding='utf-8') == 'previous-private-result'
    db.connect.assert_not_called()
    db.init.assert_not_called()
    generation.client.assert_not_called()
