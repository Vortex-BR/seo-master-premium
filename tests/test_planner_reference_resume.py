"""Planner reference failures never discard paid extraction or source uncertainty."""
from collections import Counter
from copy import deepcopy
import json
import re

import httpx
from openai import OpenAI
from pydantic import create_model
import pytest

from app import db, generation, pipeline, spending
from app.editorial import reference_contracts, store
from app.editorial.contracts import (DraftArticle, EditorialPlan, PlanIssuePriority,
                                    SpokenExtraction, VideoFidelityReview)
from conftest import real_structured
from test_response_recovery import response


def reservations(job_id):
    with db.connect() as connection:
        return {row['id']: json.loads(row['data']) for row in connection.execute(
            'SELECT id,data FROM spend_reservations WHERE job_id=?', (job_id,))}


def offline_pipeline(monkeypatch, newsroom_ai, *, legacy_contract=False, bad_reference='transcription'):
    """Use the actual SDK, accounting and coordinator over an in-memory transport.

    The first run can reproduce the pre-fix wire contract. Resumption then uses
    the current contract, as an interrupted production job does after deployment.
    """
    requests, stages, holder = [], [], {}
    state = {'legacy_contract': legacy_contract, 'planner_attempts': 0}
    original_scope = reference_contracts.scope

    def scope(original, wire, payload):
        scoped = original_scope(original, wire, payload)
        if original is EditorialPlan and state['legacy_contract']:
            return create_model('PreFixPlannerPriorities', __base__=scoped,
                issue_priorities=(list[PlanIssuePriority],
                                  deepcopy(EditorialPlan.model_fields['issue_priorities'])))
        return scoped

    monkeypatch.setattr(reference_contracts, 'scope', scope)

    def handle(request):
        if request.url.path.endswith('/responses/input_tokens'):
            return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 1000})
        requests.append(json.loads(request.content))
        result = response(json.dumps(holder['wire']))
        result['id'] = f'resp_offline_{len(requests)}'
        return httpx.Response(200, json=result)

    monkeypatch.setattr(generation, 'client', lambda: OpenAI(api_key='test-only', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))

    def deliver(current, schema, instruction, stage, extra=None):
        stages.append(stage)
        prepared = generation.prepare_structured(current, schema, instruction, stage, extra)
        output = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            state['planner_attempts'] += 1
            output['dispositions'] = {item['item_id']: {key: value for key, value in item.items()
                if key != 'item_id'} for item in output['dispositions']}
            output['issue_priorities'] = []
            if state['planner_attempts'] <= 2:
                issue_id = (next(item['id'] for item in extra['pending'] if item['origin'] == 'transcription')
                            if bad_reference == 'transcription' else 'invented-issue-id')
                output['issue_priorities'] = [{'issue_id': issue_id, 'essential': False,
                    'reason': 'A pendência permanece visível e não autoriza inventar informações.'}]
        elif schema is DraftArticle:
            markdown = output.pop('markdown')
            output['paragraphs'] = [{'markdown': re.sub(r'\[\[[\w-]+\]\]', '', text).strip(),
                'source_ids': re.findall(r'\[\[([\w-]+)\]\]', text)}
                for text in markdown.split('\n\n') if text.strip()]
            output['usage'] = {ident: True for ident in output.pop('used_item_ids')}
        elif schema is VideoFidelityReview:
            output['brief_assessments'] = {
                item['requirement_id']: {key: value for key, value in item.items() if key != 'requirement_id'}
                for item in output['brief_assessments']}
            options = prepared[3]
            assessments = {}
            for item in output['assessments']:
                item = deepcopy(item)
                ident = item.pop('passage_id')
                item['evidence'] = [{'reference': next(key for key, value in options.items()
                    if value == evidence)} for evidence in item['evidence']]
                assessments[ident] = item
            output['assessments'] = assessments
            if prepared[5].get('detail_refs'):
                output['source_details'] = {key: [{'detail': option['excerpt'],
                    **{k: v for k, v in next(item for item in output['source_assessments']
                        if item['source_id'] == option['source_id'])['detail_assessments'][0].items()
                       if k not in ('excerpt', 'detail')}}] for key, option in options.items()}
                for item in output['source_assessments']:
                    del item['detail_assessments']
            output['source_assessments'] = {
                item['source_id']: {key: value for key, value in item.items() if key != 'source_id'}
                for item in output['source_assessments']}
        else:
            assert schema is SpokenExtraction, f'Unexpected paid stage: {stage}'
        holder['wire'] = output
        return real_structured(current, schema, instruction, stage, extra, prepared=prepared)

    newsroom_ai.side_effect = deliver
    return requests, stages, state


@pytest.mark.parametrize('bad_reference', ['transcription', 'invented'])
def test_resume_after_legacy_planner_unknown_reference_keeps_paid_extraction_and_accounting(
        job, newsroom_ai, monkeypatch, bad_reference):
    job['sources'][0].update(medium='audio', transcription_warnings=[
        {'start': 12, 'end': 14, 'reason': 'Confira os termos reconhecidos no áudio original.'}])
    db.save_job(job)
    original_article = deepcopy(job['article'])
    requests, stages, provider_state = offline_pipeline(
        monkeypatch, newsroom_ai, legacy_contract=True, bad_reference=bad_reference)

    pipeline.run(job['id'])
    failed = db.get_job(job['id'])
    assert failed['status'] == 'error', failed.get('error')
    assert 'referência que não pertence à etapa' in failed['error']
    assert failed['article'] == original_article
    assert db.revisions(job['id']) == []
    assert Counter(stages) == {'extractor': 1, 'planner': 2}
    assert len(requests) == failed['editorial']['calls'] == len(failed['usage']) == 3
    completed = deepcopy(failed['editorial']['completed'])
    assert len(completed) == 1
    assert store.get_run(next(iter(completed.values())))['name'] == 'Extrator de conhecimento'
    planner_runs = [item for item in store.report(failed)['runs'] if item['role'] == 'planner']
    assert len(planner_runs) == 2
    assert all(item['status'] == 'failed' and item['data']['error_reason'] == 'unknown_reference'
               for item in planner_runs)
    assert all(item['data']['validation_errors'] == [
        {'type': 'unknown_reference', 'reference_scope': 'Prioridade do plano'}]
        for item in planner_runs)
    recovery = deepcopy(failed['editorial']['response_recoveries'])
    assert len(recovery) == 1
    assert next(iter(recovery.values()))['reason'] == 'unknown_reference'
    paid_before = reservations(job['id'])
    assert len(paid_before) == 3
    assert all(item['state'] == 'completed' for item in paid_before.values())
    finances_before = spending.summary(failed, persist=False)
    issues_before = store.issues(failed)
    assert len(issues_before) == 1
    assert issues_before[0]['origin'] == 'transcription' and issues_before[0]['status'] == 'open'

    provider_state['legacy_contract'] = False
    stages.clear()
    pipeline.run(job['id'], 'resume')

    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_review', saved.get('error')
    assert saved['error'] is None
    assert saved['generation_complete'] and saved['editorial']['initial_complete']
    assert saved['draft_delivery']['complete']
    assert saved['plan']['data']['issue_priorities'] == []
    assert saved['apuration']['version'] == failed['apuration']['version']
    assert saved['editorial']['cycle_id'] == failed['editorial']['cycle_id']
    assert all(saved['editorial']['completed'][key] == value for key, value in completed.items())
    assert saved['editorial']['response_recoveries'] == recovery
    assert Counter(stages) == {'planner': 1, 'writer': 1, 'fact_reviewer': 1}
    assert len(requests) == saved['editorial']['calls'] == len(saved['usage']) == 6
    assert saved['usage'][:3] == failed['usage']
    paid_after = reservations(job['id'])
    assert len(paid_after) == 6
    assert all(paid_after[key] == value for key, value in paid_before.items())
    assert all(item['state'] == 'completed' for item in paid_after.values())
    assert spending.summary(saved, persist=False)['spent_usd'] == pytest.approx(
        2 * finances_before['spent_usd'])
    assert store.issues(saved) == issues_before
    assert any(item.get('issue_id') == issues_before[0]['id'] and item['origin'] == 'pending_issue'
               for item in saved['review']['findings'])
    assert saved['review']['policy']['export_blocking'] is False
    assert requests[3]['text']['format']['schema']['properties']['issue_priorities']['maxItems'] == 0


def test_current_wire_rejects_invented_priorities_without_silently_filtering_them(
        job, newsroom_ai, monkeypatch):
    requests, stages, _ = offline_pipeline(monkeypatch, newsroom_ai, bad_reference='invented')
    pipeline.run(job['id'])
    failed = db.get_job(job['id'])
    assert failed['status'] == 'error'
    assert failed['error'] == generation.INVALID_RESPONSE_MESSAGE
    assert Counter(stages) == {'extractor': 1, 'planner': 2}
    assert len(requests) == 3
    assert not failed.get('plan', {}).get('valid')
    assert len(failed['editorial']['completed']) == 1
    planner_runs = [item for item in store.report(failed)['runs'] if item['role'] == 'planner']
    assert len(planner_runs) == 2
    assert all(item['status'] == 'failed' and item['data']['error_reason'] == 'invalid_output'
               for item in planner_runs)
    assert len(reservations(job['id'])) == 3
    assert all(item['state'] == 'completed' for item in reservations(job['id']).values())
    assert all(request['text']['format']['schema']['properties']['issue_priorities']['maxItems'] == 0
               for request in requests[1:])
