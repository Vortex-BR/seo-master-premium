from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import agents, engine, store
from app.editorial.contracts import EditorialDecision
from app.schemas import Review
from test_response_recovery import provider, response
import json


def large_review(job):
    finding = {'severity': 'blocking', 'passage': '', 'reason': 'Falta atribuir a observação.',
               'suggestion': 'Informe de quem é a observação.', 'source_ids': ['v1s1']}
    return {'evaluated_title': job['article']['title'],
            'editorial_alignment': {'matches_brief': True, 'reason': 'Atende.', 'passage': ''},
            'summary': 'Conferido com uma pendência.', 'findings': [finding],
            'supported_claims': [{'statement': job['article']['title'],
                                  'evidence': [{'source_id': 'v1s1', 'excerpt': 'Observação. ' * 1000}]}] * 12,
            'semantic_coverage': {'passages': 12, 'assessed': 12, 'batches': 3,
                                  'assessments': [{'reason': 'Conferência literal. ' * 1000}] * 12},
            'coverage': [{'item_id': 'k1', 'status': 'pending', 'planned_status': 'used'}],
            'article_hash': generation.article_hash(job['article']), 'reviewed_at': db.now()}


def test_decision_report_keeps_all_findings_and_coverage_without_duplicate_evidence(job):
    factual = large_review(job)
    original = deepcopy(factual)
    report = engine.decision_report(factual)
    assert factual == original
    assert report['findings'] == factual['findings']
    assert report['coverage'] == factual['coverage']
    assert report['semantic_coverage'] == {'passages': 12, 'assessed': 12, 'batches': 3}
    assert report['supported_claims_count'] == 12
    assert len(json.dumps(report)) < len(json.dumps(factual)) / 20


def test_chief_small_prompt_keeps_full_durable_review_and_cannot_waive_blocker(job, newsroom_ai, monkeypatch):
    factual = large_review(job)
    original = newsroom_ai.respond
    def respond(current, schema, instruction, stage, extra=None):
        if schema is Review:
            return deepcopy(factual)
        if schema is EditorialDecision:
            data = json.loads(generation.context(current, extra))
            assert len(json.dumps(data)) < 30000
            assert data['fontes_para_conferencia'] == {}
            assert 'plano_editorial' not in data
            assert extra['factual_review']['findings'] == factual['findings']
            assert extra['factual_review']['coverage'] == factual['coverage']
            assert len(store.artifacts(job['id'], 'factual_review')) == 1
        return original(current, schema, instruction, stage, extra)
    monkeypatch.setattr(generation, 'review_article', lambda current: deepcopy(factual))
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'review')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review'
    assert saved['review']['supported_claims'] == factual['supported_claims']
    assert saved['review']['semantic_coverage'] == factual['semantic_coverage']
    assert generation.unresolved_findings(saved) == factual['findings']


def test_completed_decision_reused_when_only_audit_timestamp_changes(job, newsroom_ai, monkeypatch):
    factual = large_review(job)
    monkeypatch.setattr(generation, 'review_article', lambda current: deepcopy(factual))
    engine.start(job, 'review')
    engine.final_review(job, 0)
    calls = job['editorial']['calls']
    # Simulate reconstruction of an identical factual audit during resume.
    def audit(current, role, payload=None, callback=None, slot=None):
        if role == 'fact_reviewer':
            return {**deepcopy(factual), 'reviewed_at': 'later'}, 'same-run'
        return original(current, role, payload, callback, slot)
    original = engine.invoke
    monkeypatch.setattr(engine, 'invoke', audit)
    engine.final_review(job, 0)
    assert job['editorial']['calls'] == calls
    assert len(store.artifacts(job['id'], 'factual_review')) == 1


def test_full_factual_audit_persists_if_chief_fails(job, newsroom_ai, monkeypatch):
    factual = large_review(job)
    monkeypatch.setattr(generation, 'review_article', lambda current: deepcopy(factual))
    def respond(current, schema, instruction, stage, extra=None):
        if stage == 'chief':
            raise RuntimeError('interrupted')
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'], 'review')
    assert db.get_job(job['id'])['status'] == 'error'
    saved = store.artifacts(job['id'], 'factual_review')[0]['data']
    assert saved['findings'] == factual['findings']
    assert saved['semantic_coverage'] == factual['semantic_coverage']
    assert saved['supported_claims'] == factual['supported_claims']


def test_instruction_limit_failure_does_not_count_as_provider_call(job, monkeypatch):
    engine.start(job, 'review')
    job['editorial']['profile']['profile']['context_chars'] = 30000
    spec = {**agents.ROLES['reader'], 'prompt': 'Confira. ' * 5000}
    monkeypatch.setitem(agents.ROLES, 'reader', spec)
    api = Mock(side_effect=AssertionError('Provider must not be opened'))
    monkeypatch.setattr(generation, 'client', api)
    with pytest.raises(generation.ContextLimitExceeded):
        engine.invoke(job, 'reader', {'article': job['article']})
    api.assert_not_called()
    assert job['editorial']['calls'] == 0
    assert db.get_job(job['id'])['editorial']['calls'] == 0
    assert not job['usage']
    assert not job['editorial'].get('response_recoveries')


def test_chief_empty_source_inventory_forbids_fabricated_citations_over_sdk(job, monkeypatch):
    engine.start(job, 'review')
    requests = provider(monkeypatch, [response(json.dumps(
        {'decision': 'ready', 'summary': 'Conferido.', 'findings': []}))])
    engine.invoke(job, 'chief', {'article': job['article'], '_context_sources': {}, '_local_context': True})
    definitions = requests[0]['text']['format']['schema']['$defs']
    observation = definitions['ScopedObservation']
    assert observation['properties']['source_ids']['maxItems'] == 0
