from copy import deepcopy
from unittest.mock import Mock

import pytest
from openai.lib._parsing._responses import type_to_text_format_param

from app import db, generation, pipeline
from app.editorial import engine, guidance, planning, research, store, workflow
from app.editorial.contracts import EditorialPlan
from test_delivery_contracts import prepare
from test_editorial_research import note


def extracted(job, newsroom_ai):
    engine.start(job, 'plan')
    workflow.extract(job)
    job['editorial']['planning_version'] = 1
    return job


def test_global_plan_selects_relevant_items_and_architecture_in_one_call(job, newsroom_ai, monkeypatch):
    extracted(job, newsroom_ai)
    item = deepcopy(job['apuration']['items'][0])
    item.update(id='v1b1k2', topic='Detalhe lateral', statement='A marca do equipamento não altera a observação.')
    job['apuration']['items'].append(item)
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            result['dispositions'][-1].update(status='out_of_scope', reason='Não ajuda a responder à pergunta desta pauta.')
            result['sections'][0]['item_ids'].remove(item['id'])
            result['reader_journey']['video_item_ids'].remove(item['id'])
        return result
    newsroom_ai.side_effect = respond
    route = Mock(side_effect=AssertionError('global plan must not classify topics again'))
    monkeypatch.setattr(workflow, 'route_topics', route)
    before = job['editorial']['calls']
    plan = workflow.plan(job)
    assert job['editorial']['calls'] == before + 1
    assert plan['data']['dispositions'][-1]['status'] == 'out_of_scope'
    assert len(job['apuration']['items']) == 2
    route.assert_not_called()
    section = guidance.article_route(plan['data'])['sections'][0]
    assert section['presentation']['mode'] == 'explanation' and section['presentation']['reason']


def test_complete_global_request_too_large_falls_back_before_provider_call(job, newsroom_ai, monkeypatch):
    extracted(job, newsroom_ai)
    monkeypatch.setattr(generation, 'prepare_structured', Mock(side_effect=generation.ContextLimitExceeded('large')))
    before = job['editorial']['calls']
    newsroom_ai.reset_mock()
    assert planning.plan(job) is None
    assert job['editorial']['calls'] == before
    newsroom_ai.assert_not_called()


def test_global_plan_requires_disposition_for_every_item_and_explicit_presentation(job, newsroom_ai):
    extracted(job, newsroom_ai)
    payload = {'items': [workflow.compact(i) for i in job['apuration']['items']],
               'source_guidance': guidance.source_guide(job)}
    output = newsroom_ai.respond(job, EditorialPlan, '', 'planner', payload)
    output['dispositions'] = {d['item_id']: {k: v for k,v in d.items() if k != 'item_id'} for d in output['dispositions']}
    schema, adapter, _ = prepare(EditorialPlan, payload)
    assert schema.model_validate(output)
    fmt = type_to_text_format_param(schema)
    assert fmt['schema']['additionalProperties'] is False
    assert adapter['field'] == 'dispositions'
    del output['sections'][0]['presentation']
    with pytest.raises(ValueError):
        schema.model_validate(output)


def test_empty_global_research_does_not_plan_again(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            result['research_questions'] = ['Que condição delimita a observação?']
        return result
    newsroom_ai.side_effect = respond
    monkeypatch.setattr(generation, 'research', lambda current: note())
    monkeypatch.setattr(research, 'page_text', Mock(side_effect=ValueError('unavailable')))
    before = job['editorial']['calls']
    assert workflow.plan(job)['valid']
    assert job['editorial']['calls'] == before + 2
    assert not job['editorial'].get('planning_research_pending')


def test_interrupted_global_research_resumes_before_writing(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            result['research_questions'] = ['Que condição delimita a observação?']
        return result
    newsroom_ai.side_effect = respond
    search = Mock(side_effect=[RuntimeError('interrupted'), False])
    monkeypatch.setattr(research, 'run', search)
    with pytest.raises(RuntimeError):
        workflow.plan(job)
    saved = db.get_job(job['id'])
    assert saved['editorial']['planning_research_pending']
    before = saved['editorial']['calls']
    result = workflow.plan(saved)
    assert result['valid'] and saved['editorial']['calls'] == before
    assert search.call_count == 2
    assert not saved['editorial'].get('planning_research_pending')


def test_verified_new_evidence_replans_once_without_nested_research(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    def respond(current, schema, instruction, stage, extra=None):
        result = newsroom_ai.respond(current, schema, instruction, stage, extra)
        if schema is EditorialPlan:
            result['research_questions'] = ['Que condição delimita a observação?']
        return result
    newsroom_ai.side_effect = respond
    search = Mock(return_value=note())
    monkeypatch.setattr(generation, 'research', search)
    monkeypatch.setattr(research, 'page_text', lambda url: 'A observação vale para o método descrito no registro original. ' * 3)
    plan = workflow.plan(job)
    plans = [c for c in newsroom_ai.call_args_list if c.args[1] is EditorialPlan]
    assert len(plans) == 2 and search.call_count == 1
    assert any(i['video_id'].startswith('wpage') for i in job['apuration']['items'])
    assert len(plan['data']['dispositions']) == len(job['apuration']['items'])
    assert plan['valid'] and plan['dependencies']['knowledge'] == job['apuration']['version']
