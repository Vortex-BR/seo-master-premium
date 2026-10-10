"""An additive deterministic check cannot cause paid legacy plan reprocessing."""
from copy import deepcopy
import json

import pytest

from app import db, generation
from app.editorial import agents, engine, store, workflow
from app.seo import knowledge


def freeze_legacy_catalog(job, **changes):
    bundle = deepcopy(knowledge.package())
    bundle['version'] = '2026-10-09.internal-review.1'
    bundle['rules'] = [rule for rule in bundle['rules'] if rule['id'] != 'video_first.timestamp_alignment']
    bundle.update(changes)
    with db.connect() as connection:
        connection.execute('INSERT INTO seo_packages VALUES (?,?)',
                           (bundle['version'], json.dumps(bundle, ensure_ascii=False)))
    engine.start(job, 'plan')
    state = job['editorial']
    state['knowledge_version'] = bundle['version']
    state['calls'] = 2
    state['completed'] = {'extractor': 'already-paid', 'planner': 'already-paid'}
    job['plan'] = {'valid': True, 'input_version': store.inputs_version(job), 'version': 'saved-plan'}
    job['apuration'] = {'valid': True, 'dependencies': workflow.dependencies(job), 'version': 'saved-knowledge'}
    return bundle, deepcopy(state)


def test_legacy_plan_continues_same_cycle_and_frozen_dependencies(job, monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('Paid provider called'))
    _, before = freeze_legacy_catalog(job)
    assert before['agents_version'] == agents.VERSION
    apuration, plan, source = deepcopy(job['apuration']), deepcopy(job['plan']), deepcopy(job['sources'])
    assert engine.start(job, 'write') == 'write'
    after = job['editorial']
    assert after['cycle_id'] == before['cycle_id']
    assert after['calls'] == 2 and after['completed'] == before['completed']
    assert after['knowledge_version'] == before['knowledge_version']
    assert workflow.compatible(job)
    assert job['apuration'] == apuration and job['plan'] == plan and job['sources'] == source


@pytest.mark.parametrize('change', ['existing_rule', 'extra_rule', 'metadata', 'missing_catalog'])
def test_other_catalog_changes_do_not_bypass_existing_invalidation(job, change):
    bundle, before = freeze_legacy_catalog(job)
    if change == 'existing_rule':
        bundle['rules'][0]['guidance'] += ' Different policy.'
    elif change == 'extra_rule':
        bundle['rules'].append({**bundle['rules'][0], 'id': 'removed.policy'})
    elif change == 'metadata':
        bundle['language'] = 'en-US'
    with db.connect() as connection:
        if change == 'missing_catalog':
            connection.execute('DELETE FROM seo_packages WHERE version=?', (bundle['version'],))
        else:
            connection.execute('UPDATE seo_packages SET data=? WHERE version=?',
                               (json.dumps(bundle, ensure_ascii=False), bundle['version']))
    assert engine.start(job, 'write') == 'write'
    assert job['editorial']['cycle_id'] != before['cycle_id']
    assert job['editorial']['knowledge_version'] == knowledge.package()['version']
