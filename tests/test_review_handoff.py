"""A single factual audit owns the final decision and survives local handoff failures."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline
from app.editorial import composition, engine, store, workflow
from app.editorial.contracts import DraftArticle, VideoFidelityReview
from conftest import real_structured
from test_evidence_workflow import prepare
from test_response_recovery import provider, response


def pending_audit(newsroom_ai, current, schema, instruction, stage, extra=None):
    result = newsroom_ai.respond(current, schema, instruction, stage, extra)
    if schema is VideoFidelityReview:
        for assessment in result['assessments']:
            if assessment['status'] == 'supported':
                assessment.update(status='unsupported', used_item_ids=[],
                                  reason='O trecho ampliou a explicação além do que o criador falou.')
    return result


def test_global_fidelity_keeps_all_blockers_coverage_and_literal_evidence_without_chief(job, newsroom_ai):
    prepare(job, newsroom_ai)
    newsroom_ai.reset_mock()
    newsroom_ai.side_effect = lambda *args, **kwargs: pending_audit(newsroom_ai, *args, **kwargs)
    pipeline.run(job['id'], 'review')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review'
    assert saved['review']['coverage'][0]['status'] == 'pending'
    assert {'semantic_review', 'coverage'} <= {f['origin'] for f in saved['review']['findings']}
    assert saved['review']['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert all(a['evidence'] for a in saved['review']['semantic_coverage']['assessments'] if a['status'] == 'unsupported')
    assert [call.args[3] for call in newsroom_ai.call_args_list] == ['fact_reviewer']
    assert not any(call.args[3] == 'chief' for call in newsroom_ai.call_args_list)


def test_complete_factual_audit_persists_before_local_check_failure(job, newsroom_ai, monkeypatch):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    newsroom_ai.side_effect = lambda *args, **kwargs: pending_audit(newsroom_ai, *args, **kwargs)

    def interrupted(current):
        raise RuntimeError('local check interrupted after durable factual audit')

    monkeypatch.setattr(engine.checks, 'blocking_findings', interrupted)
    with pytest.raises(RuntimeError):
        engine.final_review(saved, 0)
    audits = store.artifacts(saved['id'], 'factual_review')
    assert len(audits) == 1
    audit = audits[0]['data']
    assert audit['coverage'][0]['status'] == 'pending'
    assert {'semantic_review', 'coverage'} <= {f['origin'] for f in audit['findings']}
    assert audit['semantic_coverage']['assessed'] == len(workflow.passages(saved['article']))
    assert len(audit['semantic_coverage']['assessments']) == audit['semantic_coverage']['passages']
    assert audit['editorial_alignment']['matches_brief']
    assert saved['editorial']['calls'] == 1


def test_identical_article_plan_and_cycle_reuse_completed_fidelity_without_calls(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    newsroom_ai.reset_mock()
    engine.final_review(saved, 0)
    original = deepcopy(saved['review'])
    completed = deepcopy(saved['editorial']['completed'])
    calls = saved['editorial']['calls']
    engine.final_review(saved, 0)
    assert saved['editorial']['calls'] == calls == newsroom_ai.call_count == 1
    assert saved['editorial']['completed'] == completed
    assert saved['review']['semantic_coverage'] == original['semantic_coverage']
    assert saved['review']['coverage'] == original['coverage']


def test_changed_plan_invalidates_fidelity_cache_and_costs_one_new_call(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    newsroom_ai.reset_mock()
    engine.final_review(saved, 0)
    completed = deepcopy(saved['editorial']['completed'])
    calls = saved['editorial']['calls']
    saved['plan']['version'] = generation.article_hash({'previous': saved['plan']['version'], 'updated': True})
    engine.final_review(saved, 0)
    assert saved['editorial']['calls'] == calls + 1 == 2
    assert newsroom_ai.call_count == 2
    assert saved['editorial']['completed'] != completed


def test_budget_full_blocks_uncached_fidelity_before_provider(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    saved['editorial']['calls'] = saved['editorial']['profile']['profile']['max_calls']
    calls = saved['editorial']['calls']
    newsroom_ai.reset_mock()
    with pytest.raises(workflow.BudgetExceeded):
        engine.final_review(saved, 0)
    newsroom_ai.assert_not_called()
    assert saved['editorial']['calls'] == calls
    assert not [run for run in store.report(saved)['runs'] if run['cycle_id'] == saved['editorial']['cycle_id']]
    assert not store.artifacts(saved['id'], 'factual_review')


def test_cached_fidelity_can_finish_at_budget_cap(job, newsroom_ai):
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    workflow.factual_review(saved, 0)
    saved['editorial']['calls'] = saved['editorial']['profile']['profile']['max_calls']
    calls = saved['editorial']['calls']
    newsroom_ai.reset_mock()
    engine.final_review(saved, 0)
    newsroom_ai.assert_not_called()
    assert saved['editorial']['calls'] == calls
    assert saved['review']['semantic_coverage']['assessed'] > 0


def test_fidelity_context_preflight_refuses_without_provider_calls_usage_or_runs(job, monkeypatch):
    engine.start(job, 'review')
    job['editorial']['profile']['profile']['context_chars'] = 30000
    job['brief']['instructions'] = 'Confira toda a explicação. ' * 5000
    api = Mock(side_effect=AssertionError('Provider must not be opened'))
    monkeypatch.setattr(generation, 'client', api)
    with pytest.raises(generation.ContextLimitExceeded):
        engine.final_review(job, 0)
    api.assert_not_called()
    assert job['editorial']['calls'] == 0 and not job['usage']
    assert not store.report(job)['runs']
    assert not store.artifacts(job['id'], 'factual_review')


def test_active_writer_empty_source_inventory_disallows_citations_over_sdk(job, monkeypatch):
    engine.start(job, 'review')
    payload = {'items': [], '_context_sources': {}, '_local_context': True}
    output = {**{k: v for k, v in job['article'].items() if k != 'markdown'},
              'paragraphs': [{'markdown': 'Esta transição organiza a explicação para o leitor.', 'source_ids': []}],
              'usage': {}}
    requests = provider(monkeypatch, [response(json.dumps(output))])
    delivered = workflow.call(job, 'writer', DraftArticle, composition.INSTRUCTION, payload, 'empty-inventory')
    assert delivered['used_item_ids'] == [] and '[[' not in delivered['markdown']
    definitions = requests[0]['text']['format']['schema']['$defs']
    cited = [definition for definition in definitions.values() if 'source_ids' in definition.get('properties', {})]
    assert cited and all(definition['properties']['source_ids']['maxItems'] == 0 for definition in cited)


def test_empty_inventory_rejects_fabricated_video_or_web_citation(job, monkeypatch):
    engine.start(job, 'review')
    output = {**{k: v for k, v in job['article'].items() if k != 'markdown'},
              'paragraphs': [{'markdown': 'Esta explicação depende de uma fonte ausente.', 'source_ids': ['rn1']}],
              'usage': {}}
    requests = provider(monkeypatch, [response(json.dumps(output)), response(json.dumps(output))])
    with pytest.raises(generation.GenerationResponseError):
        workflow.call(job, 'writer', DraftArticle, composition.INSTRUCTION,
                      {'items': [], '_context_sources': {}}, 'empty-inventory-invalid')
    assert len(requests) == 2 and not job['editorial']['completed']


def test_removed_chief_cannot_open_provider_or_bypass_global_fidelity(job, monkeypatch):
    engine.start(job, 'review')
    api = Mock(side_effect=AssertionError('Provider must not be opened'))
    monkeypatch.setattr(generation, 'client', api)
    with pytest.raises(ValueError, match='retirado'):
        engine.invoke(job, 'chief', {'article': job['article']})
    api.assert_not_called()
    assert job['editorial']['calls'] == 0 and not store.report(job)['runs']
