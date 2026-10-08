import json

import pytest

from app import generation
from app.editorial import engine, store, workflow
from app.editorial.contracts import DraftSection, PassageAudit
from test_response_recovery import provider, response


def setup_section(job, count=2):
    engine.start(job, 'write')
    job['plan'] = {'version': 'plan-version'}
    section = {'id': 's2', 'title': 'Critérios para decidir', 'item_ids': [f'k{n+1}' for n in range(count)]}
    payload = {'section': section, 'items': [{'id': ident, 'statement': 'Um critério descrito pela fonte.'}
               for ident in section['item_ids']], 'prior_text': 'Uma explicação anterior já salva.',
               '_context_sources': generation.evidence_map(job)}
    return section, payload, 'write:plan-version:s2'


def delivery(section, used):
    return response(json.dumps({'paragraphs': [
        {'markdown': '## Critérios para decidir', 'source_ids': []},
        {'markdown': 'Esta explicação desenvolve um critério e conserva suas condições.', 'source_ids': ['v1s1']}],
        'usage': {ident: ident in used for ident in section['item_ids']}}))


def test_repeated_information_is_not_required_again_over_actual_sdk(job, monkeypatch):
    section, payload, slot = setup_section(job, count=11)
    requests = provider(monkeypatch, [delivery(section, ['k11'])])
    output, missing = workflow.write_section(job, section, payload, slot, section['item_ids'][:10], 2)
    assert output['used_item_ids'] == ['k11'] and missing == []
    assert len(requests) == job['editorial']['calls'] == 1
    material = json.loads(requests[0]['input'])
    assert material['required_item_ids'] == ['k11']
    assert set(material['already_developed_item_ids']) == set(section['item_ids'][:10])
    assert not store.artifacts(job['id'], 'draft_section_partial')


def test_missing_content_uses_one_targeted_repair_and_saved_draft_is_reused(job, monkeypatch):
    section, payload, slot = setup_section(job)
    requests = provider(monkeypatch, [delivery(section, ['k1']), delivery(section, ['k1', 'k2'])])
    output, missing = workflow.write_section(job, section, payload, slot, [], 1)
    assert missing == [] and output['used_item_ids'] == ['k1', 'k2']
    repair = json.loads(requests[1]['input'])['section_repair']
    assert repair['missing_item_ids'] == ['k2']
    assert repair['draft']['used_item_ids'] == ['k1'] and '[[v1s1]]' in repair['draft']['markdown']
    assert store.artifacts(job['id'], 'draft_section_partial')[0]['data']['missing_item_ids'] == ['k2']
    assert all(run['status'] == 'completed' for run in store.report(job)['runs'])
    assert workflow.write_section(job, section, payload, slot, [], 1) == (output, missing)
    assert len(requests) == job['editorial']['calls'] == 2


def test_unresolved_draft_keeps_honest_coverage_without_an_unbounded_retry(job, monkeypatch):
    section, payload, slot = setup_section(job)
    requests = provider(monkeypatch, [delivery(section, ['k1']), delivery(section, ['k1'])])
    output, missing = workflow.write_section(job, section, payload, slot, [], 1)
    assert missing == ['k2'] and output['used_item_ids'] == ['k1']
    record = store.artifacts(job['id'], 'draft_section_repair')[0]['data']
    assert record['missing_item_ids'] == ['k2']
    assert workflow.write_section(job, section, payload, slot, [], 1) == (output, missing)
    assert len(requests) == 2


def test_repair_never_borrows_coverage_from_discarded_text(job, monkeypatch):
    section, payload, slot = setup_section(job)
    requests = provider(monkeypatch, [delivery(section, ['k1']), delivery(section, ['k2'])])
    output, missing = workflow.write_section(job, section, payload, slot, [], 1)
    assert output['used_item_ids'] == ['k2'] and missing == ['k1']
    assert len(requests) == 2


def test_interrupted_repair_resumes_saved_draft_without_charging_first_request_again(job, monkeypatch):
    section, payload, slot = setup_section(job)
    requests = provider(monkeypatch, [delivery(section, ['k1'])])
    client = generation.client
    count = 0
    def interrupted():
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError('Simulated worker interruption')
        return client()
    monkeypatch.setattr(generation, 'client', interrupted)
    with pytest.raises(RuntimeError):
        workflow.write_section(job, section, payload, slot, [], 1)
    checkpoint = store.artifacts(job['id'], 'draft_section_partial')[0]['data']
    assert checkpoint['draft']['used_item_ids'] == ['k1'] and checkpoint['missing_item_ids'] == ['k2']
    prior_run = job['editorial']['completed'][slot + ':v2']
    resumed = provider(monkeypatch, [delivery(section, section['item_ids'])])
    output, missing = workflow.write_section(job, section, payload, slot, [], 1)
    assert not missing and output['used_item_ids'] == section['item_ids']
    assert job['editorial']['completed'][slot + ':v2'] == prior_run
    assert len(requests) == len(resumed) == 1
    assert json.loads(resumed[0]['input'])['section_repair'] == {
        'draft': checkpoint['draft'], 'missing_item_ids': checkpoint['missing_item_ids']}


def test_completed_legacy_section_is_reused_only_with_exact_input_identity(job, monkeypatch):
    section, payload, slot = setup_section(job)
    requests = provider(monkeypatch, [delivery(section, section['item_ids']), delivery(section, section['item_ids'])])
    first = workflow.call(job, 'writer', DraftSection, workflow.WRITE_SECTION, payload, slot)
    prior_run = job['editorial']['completed'][slot]
    job['editorial']['profile']['profile']['max_calls'] = 1
    assert workflow.write_section(job, section, payload, slot, [], 5) == (first, [])
    assert len(requests) == job['editorial']['calls'] == 1
    job['editorial']['profile']['profile']['max_calls'] = 120
    workflow.write_section(job, section, {**payload, 'prior_text': 'Texto anterior diferente.'}, slot, [], 1)
    assert len(requests) == 2 and job['editorial']['completed'][slot] == prior_run


def test_invalid_references_remain_rejected_before_saving_a_partial_draft(job, monkeypatch):
    section, payload, slot = setup_section(job)
    invalid = delivery(section, ['k1'])
    body = json.loads(invalid['output'][0]['content'][0]['text'])
    body['paragraphs'][1]['source_ids'] = ['not-a-source']
    requests = provider(monkeypatch, [response(json.dumps(body)), response(json.dumps(body))])
    with pytest.raises(generation.GenerationResponseError):
        workflow.write_section(job, section, payload, slot, [], 1)
    assert len(requests) == 2 and not store.artifacts(job['id'], 'draft_section_partial')


def test_pending_section_reserve_does_not_count_completed_sections(job, monkeypatch):
    section, payload, slot = setup_section(job)
    job['editorial']['calls'] = 111
    job['editorial']['profile']['profile']['max_calls'] = 120
    requests = provider(monkeypatch, [delivery(section, section['item_ids'])])
    output, missing = workflow.write_section(job, section, payload, slot, [], 2)
    assert not missing and len(requests) == 1 and job['editorial']['calls'] == 112


def test_final_factual_review_still_blocks_a_planned_fact_absent_from_the_article(job, newsroom_ai):
    from test_evidence_workflow import prepare
    saved = prepare(job, newsroom_ai)
    engine.start(saved, 'review')
    def respond(current, schema, instruction, stage, extra=None):
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is PassageAudit:
            for assessment in output['assessments']:
                assessment.update(status='not_factual', used_item_ids=[], evidence=[])
        return output
    newsroom_ai.side_effect = respond
    engine.final_review(saved, 0)
    assert saved['review']['coverage'][0]['status'] == 'pending'
    assert any(f['severity'] == 'blocking' and f['origin'] == 'coverage' for f in saved['review']['findings'])
