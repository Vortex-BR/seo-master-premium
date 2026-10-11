"""Crash recovery and installation rollback against temporary SQLite storage.

Generation is mocked and failure hooks interrupt the actual coordinator. Cached
runs, articles, review history, revisions and financial reservations use real
local persistence, without provider requests or charges.
"""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline, spending
from app.editorial import quality_repair, review_policy, store, workflow
from app.editorial.contracts import VideoFidelityReview
from test_quality_repair_acceptance import (
    CORRECTED_BODY, ORIGINAL_BODY, assert_original_preserved, furniture,
)


class SimulatedProcessCrash(BaseException):
    """A process exit bypasses the optional repair's ordinary error handler."""


@pytest.fixture
def repair_ready(furniture, monkeypatch):
    job, provider, boundary = furniture
    network = Mock(side_effect=AssertionError('No provider client may be opened'))
    monkeypatch.setattr(generation, 'client', network)
    # Complete the four core deliveries before exercising optional-repair
    # interruption points. The initial independent diagnosis remains durable.
    with monkeypatch.context() as setup:
        setup.setattr(quality_repair, 'run', lambda current: None)
        pipeline.run(job['id'])
    current = db.get_job(job['id'])
    assert_original_preserved(current)
    assert current['editorial']['calls'] == boundary.call_count == 4
    assert not current['editorial'].get('quality_repair')
    network.assert_not_called()
    return current, provider, boundary, network


def candidate_runs(job):
    before_hash = generation.article_hash({**job['article'], 'markdown': ORIGINAL_BODY})
    candidate_hash = generation.article_hash({**job['article'], 'markdown': CORRECTED_BODY})
    slots = {
        'writer': f'quality-repair:{quality_repair.VERSION}:{before_hash}',
        'fact_reviewer': 'video-fidelity:' + candidate_hash,
    }
    return {
        role: [run for run in store.report(job)['runs']
               if run['cycle_id'] == job['editorial']['cycle_id']
               and run['data'].get('slot') == slot]
        for role, slot in slots.items()
    }


def test_cached_candidate_and_audit_resume_at_zero_call_and_financial_balance(
        repair_ready, monkeypatch):
    current, provider, boundary, network = repair_ready
    original_article = deepcopy(current['article'])
    original_review = deepcopy(current['review'])

    def crash_before_install(staged, review):
        raise SimulatedProcessCrash('Crash after durable candidate audit')

    with monkeypatch.context() as crash:
        crash.setattr(review_policy, 'annotate_review', crash_before_install)
        with pytest.raises(SimulatedProcessCrash):
            quality_repair.run(current)

    interrupted = db.get_job(current['id'])
    assert interrupted['article'] == original_article
    assert interrupted['review'] == original_review
    assert interrupted['editorial']['quality_repair']['status'] == 'started'
    assert boundary.call_count == interrupted['editorial']['calls'] == 6
    runs = candidate_runs(interrupted)
    assert len(runs['writer']) == len(runs['fact_reviewer']) == 1
    assert all(run['status'] == 'completed' for values in runs.values() for run in values)
    assert store.artifacts(interrupted['id'], 'quality_repair_candidate')
    assert store.artifacts(interrupted['id'], 'quality_repair_review')

    interrupted['editorial']['calls'] = interrupted['editorial']['profile']['profile']['max_calls']
    db.save_job(interrupted)
    # An entirely reserved allowance is a real zero available balance. This
    # synthetic uncertain reservation never invokes a provider and must survive
    # resumption alongside the completed cached editorial deliveries.
    available = spending.summary(interrupted)['remaining_usd']
    reservation = spending.reserve(
        interrupted, available, interrupted['editorial']['model'], 'synthetic_zero_balance')
    spending.finish(reservation)
    financial_before = spending.summary(interrupted)
    assert financial_before['remaining_usd'] == 0
    assert workflow.remaining(interrupted) == 0
    calls_before = boundary.call_count
    ledger_before = interrupted['editorial']['calls']
    revisions_before = db.revisions(interrupted['id'])

    # Re-enter the complete coordinator from a freshly loaded job, including
    # the ordinary initial-review cache lookup before optional repair.
    pipeline.run(interrupted['id'], 'resume')
    resumed = db.get_job(interrupted['id'])
    report = resumed['editorial']['quality_repair']
    assert report['status'] == 'accepted', report
    assert resumed['article']['markdown'] == CORRECTED_BODY
    assert resumed['review']['article_hash'] == generation.article_hash(resumed['article'])
    assert resumed['review']['source_coverage']['assessments'][0]['status'] == 'complete'
    assert resumed['editorial']['calls'] == ledger_before
    assert boundary.call_count == calls_before
    assert spending.summary(resumed) == financial_before
    assert len(db.revisions(resumed['id'])) == len(revisions_before) + 1
    assert any(revision['data'] == original_article for revision in db.revisions(resumed['id']))
    assert candidate_runs(resumed) == runs
    assert len(provider.calls) == calls_before
    network.assert_not_called()


@pytest.mark.parametrize('attempt_status', ['failed', 'running'])
def test_previously_attempted_candidate_reviewer_is_never_sent_again(
        repair_ready, monkeypatch, attempt_status):
    current, provider, boundary, network = repair_ready
    original_article = deepcopy(current['article'])
    original_review = deepcopy(current['review'])
    save_report = quality_repair._save

    with monkeypatch.context() as crash:
        if attempt_status == 'failed':
            provider.outcome = 'reviewer_failure'

            def crash_before_failure_report(job, report):
                if report['status'] == 'failed':
                    raise SimulatedProcessCrash('Failure run recorded before repair report')
                return save_report(job, report)

            crash.setattr(quality_repair, '_save', crash_before_failure_report)
        else:
            def crash_during_candidate_review(job, schema, instruction, stage, extra=None):
                output = provider(job, schema, instruction, stage, extra)
                if schema is VideoFidelityReview and (extra or {}).get('previous_article'):
                    raise SimulatedProcessCrash('Running review interrupted before durable completion')
                return output

            boundary.side_effect = crash_during_candidate_review
        with pytest.raises(SimulatedProcessCrash):
            quality_repair.run(current)

    interrupted = db.get_job(current['id'])
    assert interrupted['editorial']['quality_repair']['status'] == 'started'
    assert interrupted['article'] == original_article
    assert interrupted['review'] == original_review
    runs = candidate_runs(interrupted)
    assert len(runs['writer']) == len(runs['fact_reviewer']) == 1
    assert runs['writer'][0]['status'] == 'completed'
    assert runs['fact_reviewer'][0]['status'] == attempt_status
    assert boundary.call_count == interrupted['editorial']['calls'] == 6
    provider.outcome = 'accepted'
    boundary.side_effect = provider
    calls_before = boundary.call_count
    ledger_before = interrupted['editorial']['calls']
    revisions_before = db.revisions(interrupted['id'])

    for _ in range(2):
        reloaded = db.get_job(interrupted['id'])
        report = quality_repair.run(reloaded)
        assert report['status'] == 'deferred', report
        assert report['reason'] == 'previous_review_unconfirmed'
        saved = db.get_job(interrupted['id'])
        assert saved['article'] == original_article
        assert saved['review'] == original_review
        assert saved['editorial']['calls'] == ledger_before
        assert boundary.call_count == calls_before
        assert candidate_runs(saved) == runs
        assert db.revisions(saved['id']) == revisions_before
        assert not store.artifacts(saved['id'], 'quality_repair_review')
    network.assert_not_called()


def test_annotation_failure_preserves_original_article_and_review(repair_ready, monkeypatch):
    current, _, boundary, network = repair_ready
    original_article = deepcopy(current['article'])
    original_review = deepcopy(current['review'])
    history_before = deepcopy(current.get('review_history', []))
    archives_before = store.artifacts(current['id'], 'review_history')
    revisions_before = db.revisions(current['id'])
    annotate = review_policy.annotate_review

    def fail_after_partial_annotation(staged, review):
        annotate(staged, review)
        staged['article']['markdown'] += '\n\nPartially applied local annotation.'
        review['summary'] = 'Partially annotated candidate.'
        raise RuntimeError('Synthetic annotation failure before installation')

    monkeypatch.setattr(review_policy, 'annotate_review', fail_after_partial_annotation)
    report = quality_repair.run(current)
    saved = db.get_job(current['id'])
    assert report['status'] == 'failed', report
    assert report['reason'] == 'RuntimeError'
    assert current['article'] == saved['article'] == original_article
    assert current['review'] == saved['review'] == original_review
    assert saved.get('review_history', []) == history_before
    assert store.artifacts(saved['id'], 'review_history') == archives_before
    assert db.revisions(saved['id']) == revisions_before
    assert boundary.call_count == saved['editorial']['calls'] == 6
    runs = candidate_runs(saved)
    assert all(run['status'] == 'completed' for values in runs.values() for run in values)
    assert store.artifacts(saved['id'], 'quality_repair_candidate')
    assert store.artifacts(saved['id'], 'quality_repair_review')
    network.assert_not_called()


def test_failed_install_transaction_rolls_back_article_review_and_archive(
        repair_ready, monkeypatch):
    current, _, boundary, network = repair_ready
    original_article = deepcopy(current['article'])
    original_review = deepcopy(current['review'])
    history_before = deepcopy(current.get('review_history', []))
    archives_before = store.artifacts(current['id'], 'review_history')
    revisions_before = db.revisions(current['id'])
    save_job = db.save_job
    uncommitted = {}

    def fail_after_install_write(staged):
        save_job(staged)
        if staged['editorial'].get('quality_repair', {}).get('status') == 'accepted':
            # These writes are visible within the real transaction. Raising
            # after all of them must roll back the entire installation unit.
            uncommitted['job'] = db.get_job(staged['id'])
            uncommitted['archives'] = store.artifacts(staged['id'], 'review_history')
            uncommitted['revisions'] = db.revisions(staged['id'])
            raise RuntimeError('Synthetic failure after installation SQL write')

    monkeypatch.setattr(db, 'save_job', fail_after_install_write)
    report = quality_repair.run(current)
    assert uncommitted['job']['article']['markdown'] == CORRECTED_BODY
    assert uncommitted['job']['review']['article_hash'] == generation.article_hash(
        uncommitted['job']['article'])
    assert uncommitted['job']['editorial']['quality_repair']['status'] == 'accepted'
    assert len(uncommitted['archives']) == len(archives_before) + 1
    assert len(uncommitted['revisions']) == len(revisions_before) + 1
    assert any(archive['data'] == original_review for archive in uncommitted['archives'])

    saved = db.get_job(current['id'])
    assert report['status'] == 'failed', report
    assert report['reason'] == 'RuntimeError'
    assert current['article'] == saved['article'] == original_article
    assert current['review'] == saved['review'] == original_review
    assert saved.get('review_history', []) == history_before
    assert store.artifacts(saved['id'], 'review_history') == archives_before
    assert db.revisions(saved['id']) == revisions_before
    assert saved['editorial']['quality_repair']['status'] == 'failed'
    assert not any(artifact['data']['status'] == 'accepted'
                   for artifact in store.artifacts(saved['id'], 'quality_repair'))
    assert boundary.call_count == saved['editorial']['calls'] == 6
    runs = candidate_runs(saved)
    assert all(run['status'] == 'completed' for values in runs.values() for run in values)
    assert store.artifacts(saved['id'], 'quality_repair_candidate')
    assert store.artifacts(saved['id'], 'quality_repair_review')
    network.assert_not_called()
