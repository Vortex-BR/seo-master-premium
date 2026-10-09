from copy import deepcopy
from unittest.mock import Mock

import pytest
from openai.lib._parsing._responses import type_to_text_format_param

from app import db, generation, pipeline
from app.editorial import engine, guidance, research, store, workflow
from app.editorial.contracts import EditorialPlan
from test_delivery_contracts import prepare
from test_editorial_research import background, mock_research


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


def test_complete_global_request_too_large_stops_without_partitioned_planning_or_paid_request(job, newsroom_ai, monkeypatch):
    from test_response_recovery import provider
    from conftest import real_structured
    extracted(job, newsroom_ai)
    job['editorial']['profile']['profile']['context_chars'] = 30000
    job['apuration']['items'][0]['statement'] *= 2000
    requests = provider(monkeypatch, [])
    monkeypatch.setattr(generation, 'structured', real_structured)
    route = Mock(side_effect=AssertionError('Oversized global planning must stop.'))
    monkeypatch.setattr(workflow, 'route_topics', route)
    before = job['editorial']['calls']
    with pytest.raises(generation.ContextLimitExceeded):
        workflow.plan(job)
    assert job['editorial']['calls'] == before
    assert not requests
    route.assert_not_called()


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


def test_legacy_research_flag_plans_once_without_search(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    job['apuration']['items'][0]['topic'] = 'manjericão'
    search = Mock(return_value={'text': '', 'sources': [], 'internal_context_only': True})
    monkeypatch.setattr(generation, 'research', search)
    version = job['apuration']['version']
    before = job['editorial']['calls']
    assert workflow.plan(job)['valid']
    assert job['editorial']['calls'] == before + 1
    assert job['apuration']['version'] == version
    search.assert_not_called()
    assert len([call for call in newsroom_ai.call_args_list if call.args[1] is EditorialPlan]) == 1
    assert not job['editorial'].get('planning_research_pending')


def test_interrupted_legacy_research_is_not_resumed_before_writing(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    job['research'] = {'status': 'unavailable', 'text': 'Notas anteriores.', 'sources': [],
                       'internal_context_only': True}
    job['editorial']['planning_research_pending'] = {'questions': ['Termo antigo.']}
    db.save_job(job)
    search = Mock(side_effect=AssertionError('Interrupted research must not resume.'))
    monkeypatch.setattr(research, 'run', search)
    saved = db.get_job(job['id'])
    assert not saved.get('plan')
    assert saved['apuration']['valid']
    before = saved['editorial']['calls']
    result = workflow.plan(saved)
    assert result['valid'] and saved['editorial']['calls'] == before + 1
    search.assert_not_called()
    assert saved['research']['text'] == 'Notas anteriores.'
    assert not saved['editorial'].get('planning_research_pending')


def test_checked_web_page_never_adds_inventory_or_replans(job, newsroom_ai, monkeypatch):
    job['brief']['research'] = True
    extracted(job, newsroom_ai)
    job['apuration']['items'][0]['topic'] = 'manjericão'
    before = deepcopy(job['apuration'])
    job['research'] = {'status': 'completed', 'text': 'Notas anteriores.', 'sources': [],
                       'internal_context_only': True, 'agent_background_knowledge': background()}
    search, _ = mock_research(monkeypatch, newsroom_ai)
    plan = workflow.plan(job)
    plans = [c for c in newsroom_ai.call_args_list if c.args[1] is EditorialPlan]
    assert len(plans) == 1 and search.call_count == 0
    assert job['apuration'] == before
    assert research.agent_background_knowledge(job) == background()
    assert len(plan['data']['dispositions']) == len(job['apuration']['items'])
    assert plan['valid']
    calls = job['editorial']['calls']
    assert workflow.plan(job)['version'] == plan['version']
    assert job['editorial']['calls'] == calls
