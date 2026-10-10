"""Offline regression coverage for scoped opportunities and production commands."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
from unittest.mock import patch

import pytest

from app import db, pipeline
from app.strategy import coordinator, store


def opportunity(identifier='ai-duplicate', **overrides):
    return {
        'opportunity_id': identifier, 'action': 'create', 'main_question': 'Como observar as folhas?',
        'queries': ['folhas'], 'justification': 'Pergunta pertinente à fonte.',
        'selected_videos': [{'url': 'https://www.youtube.com/watch?v=abcdefghijk'}],
        'status': 'approved', **overrides,
    }


def test_opportunity_provider_id_is_scoped_between_projects_and_cycles(client):
    first = opportunity(status='proposed')
    second = opportunity(status='proposed', main_question='Outra pergunta?')
    store.save_opportunity(first, {'id': 'cycle-a'}, 'project-a')
    store.save_opportunity(second, {'id': 'cycle-b'}, 'project-b')
    a = store.list_opportunities('project-a')
    b = store.list_opportunities('project-b')
    assert len(a) == len(b) == 1
    assert a[0]['main_question'] == first['main_question']
    assert b[0]['main_question'] == second['main_question']
    assert a[0]['opportunity_id'] != b[0]['opportunity_id']
    assert a[0]['project_id'] == 'project-a'
    assert b[0]['cycle_id'] == 'cycle-b'


def test_resynthesis_preserves_human_rejection(client):
    original = opportunity(status='rejected', decision_history=[{
        'action': 'reject', 'reason': 'Não produzir esta pauta.', 'actor': 'Administrador', 'at': db.now(),
    }])
    store.save_opportunity(original, {'id': 'cycle-a'}, 'project-a')
    store.save_opportunity(opportunity(status='proposed'), {'id': 'cycle-a'}, 'project-a')
    saved = store.get_opportunity(original['opportunity_id'])
    assert saved['status'] == 'rejected'
    assert saved['decision_history'] == original['decision_history']


def test_rejected_opportunity_does_not_produce_even_with_video(authed):
    store.save_opportunity(opportunity(status='rejected'), {'id': 'cycle-a'})
    with patch.object(pipeline.executor, 'submit') as enqueue:
        response = authed.post('/api/strategy/opportunities/ai-duplicate/produce')
    assert response.status_code == 409
    assert not db.list_jobs()
    enqueue.assert_not_called()


def test_double_click_production_reuses_one_job(authed):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    with patch.object(pipeline.executor, 'submit') as enqueue:
        first = authed.post('/api/strategy/opportunities/ai-duplicate/produce')
        second = authed.post('/api/strategy/opportunities/ai-duplicate/produce')
    assert first.status_code == second.status_code == 200
    assert first.json()['job_id'] == second.json()['job_id']
    assert len(db.list_jobs()) == 1
    enqueue.assert_called_once()


def job_factory(current):
    return {'id': store.new_id(), 'status': 'new', 'created_at': db.now(),
            'brief': {'topic': current['main_question']}, 'sources': [], 'events': [], 'usage': []}


def test_concurrent_commands_reserve_exactly_one_intent_and_job(client):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    barrier = threading.Barrier(8)
    calls = []
    call_lock = threading.Lock()

    def build(current):
        with call_lock:
            calls.append(current['opportunity_id'])
        return job_factory(current)

    def produce(_):
        barrier.wait(timeout=5)
        return store.create_production('ai-duplicate', build)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(produce, range(8)))
    assert sum(result['created'] for result in results) == 1
    assert len({result['job_id'] for result in results}) == 1
    assert len({result['intent_id'] for result in results}) == 1
    assert calls == ['ai-duplicate']
    assert len(db.list_jobs()) == 1
    assert len(store.get_opportunity('ai-duplicate')['production_history']) == 1


@pytest.mark.parametrize('action', ['update', 'consolidate', 'improve_links', 'fix_technical', 'investigate'])
def test_non_creation_action_never_builds_job(client, action):
    store.save_opportunity(opportunity(action=action), {'id': 'cycle-a'})
    with patch(__name__ + '.job_factory') as build, pytest.raises(store.OpportunityConflict):
        store.create_production('ai-duplicate', build)
    build.assert_not_called()
    assert not db.list_jobs()


@pytest.mark.parametrize('status', ['proposed', 'rejected', 'cancelled', 'completed', 'in_progress'])
def test_non_approved_state_without_link_never_builds_job(client, status):
    store.save_opportunity(opportunity(status=status), {'id': 'cycle-a'})
    with patch(__name__ + '.job_factory') as build, pytest.raises(store.OpportunityConflict):
        store.create_production('ai-duplicate', build)
    build.assert_not_called()
    assert not db.list_jobs()


@pytest.mark.parametrize('status', ['error', 'interrupted', 'ready', 'needs_review', 'cancelled'])
def test_retry_returns_existing_job_without_implicit_resume(client, status):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    first = store.create_production('ai-duplicate', job_factory)
    job = db.get_job(first['job_id'])
    job['status'] = status
    db.save_job(job)
    with patch(__name__ + '.job_factory') as build:
        second = store.create_production('ai-duplicate', build)
    build.assert_not_called()
    assert second['reused'] is True
    assert second['job_id'] == first['job_id']
    assert db.get_job(first['job_id'])['status'] == status
    assert len(db.list_jobs()) == 1


def test_operation_key_cannot_bypass_unique_intent(client):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    store.create_production('ai-duplicate', job_factory)
    with pytest.raises(store.OpportunityConflict):
        store.create_production('ai-duplicate', job_factory, operation_key='another-click')
    assert len(db.list_jobs()) == 1


def test_factory_failure_rolls_back_and_retry_preserves_one_job(client):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})

    def broken(_):
        raise ValueError('brief inválido')

    with pytest.raises(ValueError, match='brief inválido'):
        store.create_production('ai-duplicate', broken)
    assert store.get_opportunity('ai-duplicate')['status'] == 'approved'
    assert not db.list_jobs()
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_production_intents').fetchone()[0] == 0
    result = store.create_production('ai-duplicate', job_factory)
    assert result['created'] is True
    assert len(db.list_jobs()) == 1


def test_missing_legacy_job_does_not_create_replacement(client):
    store.save_opportunity(opportunity(status='in_progress', job_id='missing'), {'id': 'cycle-a'})
    with patch(__name__ + '.job_factory') as build, pytest.raises(store.OpportunityConflict):
        store.create_production('ai-duplicate', build)
    build.assert_not_called()
    assert not db.list_jobs()
    assert store.get_opportunity('ai-duplicate')['job_id'] == 'missing'


def test_conflicting_job_identity_rolls_back_without_overwriting_existing_job(client):
    existing = job_factory(opportunity())
    db.save_job(existing)
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    with pytest.raises(store.OpportunityConflict, match='identificador do artigo'):
        store.create_production('ai-duplicate', lambda _: deepcopy(existing))
    assert db.get_job(existing['id']) == existing
    assert store.get_opportunity('ai-duplicate')['status'] == 'approved'
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_production_intents').fetchone()[0] == 0


def test_legacy_two_opportunities_cannot_silently_steal_one_intent(client):
    existing = job_factory(opportunity())
    db.save_job(existing)
    store.save_opportunity(opportunity('first', status='in_progress', job_id=existing['id']), {'id': 'cycle-a'})
    store.save_opportunity(opportunity('second', status='in_progress', job_id=existing['id']), {'id': 'cycle-a'})
    first = store.create_production('first', job_factory)
    with pytest.raises(store.OpportunityConflict, match='outra intenção'):
        store.create_production('second', job_factory)
    assert first['job_id'] == existing['id']
    assert first['reused'] and db.get_job(existing['id']) == existing
    assert len(db.list_jobs()) == 1


def test_legacy_link_is_adopted_without_rewriting_job(client):
    legacy = job_factory(opportunity())
    legacy.update(status='ready', article={'markdown': 'Texto legado preservado.'}, unknown_metadata={'keep': True})
    db.save_job(legacy)
    store.save_opportunity(opportunity(status='in_progress', job_id=legacy['id']), {'id': 'cycle-a'})
    with db.connect() as c:
        before = c.execute('SELECT data FROM jobs WHERE id=?', (legacy['id'],)).fetchone()[0]
    with patch(__name__ + '.job_factory') as build:
        first = store.create_production('ai-duplicate', build)
        second = store.create_production('ai-duplicate', build)
    build.assert_not_called()
    assert first['reused'] is second['reused'] is True
    assert first['intent_id'] == second['intent_id']
    assert first['job_id'] == legacy['id']
    with db.connect() as c:
        assert c.execute('SELECT data FROM jobs WHERE id=?', (legacy['id'],)).fetchone()[0] == before


@pytest.mark.parametrize('status', ['rejected', 'cancelled'])
def test_rejected_or_cancelled_legacy_link_is_not_reused(client, status):
    legacy = job_factory(opportunity())
    db.save_job(legacy)
    store.save_opportunity(opportunity(status=status, job_id=legacy['id']), {'id': 'cycle-a'})
    with pytest.raises(store.OpportunityConflict):
        store.create_production('ai-duplicate', job_factory)
    assert len(db.list_jobs()) == 1


def test_parallel_different_opportunities_cannot_overfill_queue(client):
    store.save_opportunity(opportunity('first'), {'id': 'cycle-a'})
    store.save_opportunity(opportunity('second'), {'id': 'cycle-a'})
    barrier = threading.Barrier(2)

    def produce(identifier):
        barrier.wait(timeout=5)
        try:
            return store.create_production(identifier, job_factory, queue_limit=1)
        except store.ProductionQueueFull:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(produce, ['first', 'second']))
    assert len([result for result in results if result]) == 1
    assert len(db.list_jobs()) == 1


def test_unsubmitted_regular_drafts_do_not_fill_strategy_queue(client):
    for _ in range(12):
        db.save_job(job_factory(opportunity()))
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    result = store.create_production('ai-duplicate', job_factory, queue_limit=1)
    assert result['created'] is True


def test_decision_history_is_serialized_under_concurrent_requests(client):
    store.save_opportunity(opportunity(status='proposed'), {'id': 'cycle-a'})
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda n: store.decide_opportunity('ai-duplicate', 'approve' if n % 2 else 'reject', str(n)), range(6)))
    saved = store.get_opportunity('ai-duplicate')
    assert len(saved['decision_history']) == 6
    assert {item['reason'] for item in saved['decision_history']} == {str(n) for n in range(6)}


def test_production_and_rejection_cannot_silently_unlink_job(client):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'})
    result = store.create_production('ai-duplicate', job_factory)
    with pytest.raises(store.OpportunityConflict):
        store.decide_opportunity('ai-duplicate', 'reject', 'Resposta tardia')
    saved = store.get_opportunity('ai-duplicate')
    assert saved['status'] == 'in_progress'
    assert saved['job_id'] == result['job_id']


def test_batch_duplicate_ids_are_rejected_before_any_partial_persistence(client):
    plan = {'opportunities': [opportunity('unique'), opportunity('repeat'), opportunity('repeat', main_question='Questão diferente?')]}
    original = deepcopy(plan)
    with pytest.raises(store.OpportunityConflict, match='repetiu'):
        store.save_plan_opportunities(plan, {'id': 'cycle-a'})
    assert not store.list_opportunities()
    assert plan == original


def test_ai_plan_cannot_approve_its_own_new_opportunity(client):
    plan = {'opportunities': [opportunity(status='approved')]}
    store.save_plan_opportunities(plan, {'id': 'cycle-a'}, 'project-a')
    assert plan['opportunities'][0]['status'] == 'proposed'
    assert store.get_opportunity('ai-duplicate')['status'] == 'proposed'


def test_deterministic_fallback_has_stable_scoped_ids_and_no_identical_duplicates():
    finding = {'severity': 'opportunity', 'summary': 'Pergunta necessária', 'suggestion': 'Investigar',
               'related_queries': [], 'related_pages': [], 'evidence': []}
    results = {'business': {'findings': [finding, deepcopy(finding)]}}
    first = coordinator._deterministic_plan({'id': 'cycle-a'}, results)
    retry = coordinator._deterministic_plan({'id': 'cycle-a'}, results)
    other = coordinator._deterministic_plan({'id': 'cycle-b'}, results)
    assert first == retry
    assert len(first['opportunities']) == 1
    assert first['opportunities'][0]['opportunity_id'] != other['opportunities'][0]['opportunity_id']


def test_scoped_ids_are_stable_when_same_cycle_is_saved_again(client):
    store.save_opportunity(opportunity(), {'id': 'cycle-a'}, 'project-a')
    second = opportunity(status='proposed')
    store.save_opportunity(second, {'id': 'cycle-b'}, 'project-a')
    public_id = second['opportunity_id']
    again = opportunity(status='proposed')
    store.save_opportunity(again, {'id': 'cycle-b'}, 'project-a')
    assert again['opportunity_id'] == public_id
    assert len(store.list_opportunities('project-a')) == 2
    assert store.get_opportunity('ai-duplicate')['cycle_id'] == 'cycle-a'


def test_additive_legacy_migration_preserves_payloads_and_can_be_repeated(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    db.init()
    legacy_json = json.dumps(opportunity('legacy', status='rejected'), ensure_ascii=False)
    with db.connect() as c:
        c.execute('''CREATE TABLE opportunities (id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,
                     project_id TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
                     updated_at TEXT NOT NULL,data TEXT NOT NULL)''')
        c.execute('INSERT INTO opportunities VALUES (?,?,?,?,?,?,?)',
                  ('legacy', 'cycle-old', 'project-old', 'rejected', db.now(), db.now(), legacy_json))
    backup = sqlite3.connect(tmp_path / 'backup.sqlite3')
    with db.connect() as c:
        c.backup(backup)
    backup.close()
    store.init()
    store.init()
    saved = store.get_opportunity('legacy')
    assert saved['opportunity_id'] == saved['provider_opportunity_id'] == 'legacy'
    assert saved['project_id'] == 'project-old'
    assert saved['cycle_id'] == 'cycle-old'
    with db.connect() as c:
        assert c.execute('SELECT data FROM opportunities WHERE id=?', ('legacy',)).fetchone()[0] == legacy_json
        assert len(list(c.execute('PRAGMA table_info(opportunities)'))) == 7
        # Baseline writer used positional INSERT VALUES with seven values.
        c.execute('INSERT INTO opportunities VALUES (?,?,?,?,?,?,?)',
                  ('legacy-writer', 'cycle-old', 'project-old', 'proposed', db.now(), db.now(),
                   json.dumps(opportunity('legacy-writer', status='proposed'), ensure_ascii=False)))
    assert store.get_opportunity('legacy-writer')['provider_opportunity_id'] == 'legacy-writer'
    store.init()
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_opportunity_keys').fetchone()[0] == 2
    restored = sqlite3.connect(tmp_path / 'restored.sqlite3')
    with sqlite3.connect(tmp_path / 'backup.sqlite3') as c:
        c.backup(restored)
    assert restored.execute('SELECT data FROM opportunities').fetchone()[0] == legacy_json
    assert 'provider_opportunity_id' not in {row[1] for row in restored.execute('PRAGMA table_info(opportunities)')}
    restored.close()
