"""Inspect schemas actually prepared for Responses, without provider requests."""
import inspect

import pytest
from pydantic import BaseModel, ValidationError

from app import generation, schemas
from app.editorial import contracts as editorial_contracts
from app.strategy import agents
from app.strategy.contracts import (ContentArchitecture, ContentCuration, PerformanceAnalysis,
                                    Opportunity, ResultsAnalysis, StrategyPlan)


def schema_objects(node, path='$'):
    if isinstance(node, dict):
        if node.get('type') == 'object':
            yield path, node
        for key, value in node.items():
            yield from schema_objects(value, f'{path}.{key}')
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from schema_objects(value, f'{path}[{index}]')


def assert_strict_objects(spec):
    assert spec['type'] == 'json_schema'
    assert spec['strict'] is True
    objects = list(schema_objects(spec['schema']))
    assert objects
    for path, obj in objects:
        assert obj.get('additionalProperties') is False, path
        assert set(obj.get('required', [])) == set(obj.get('properties', {})), path


@pytest.mark.parametrize('role', [*agents.ROLES, 'coordinator'])
def test_strategic_request_contains_closed_strict_objects(client, role, monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('No provider call allowed'))
    schema = agents.ROLES[role]['schema'] if role != 'coordinator' else StrategyPlan
    request, *_ = generation.prepare_structured(
        {'id': 'offline', 'brief': {}, 'sources': []}, schema, 'Teste offline',
        f'strategy_{role}', {'project': {'project_id': 'offline'}})
    assert_strict_objects(request['text']['format'])


def test_missing_performance_metrics_stay_unknown():
    result = PerformanceAnalysis(summary='Dados indisponíveis.').model_dump()
    assert result['total_clicks'] is None
    assert result['total_impressions'] is None
    opportunity = Opportunity(opportunity_id='op1', action='investigate', main_question='O que falta?',
                              justification='Ainda sem dados para priorizar.')
    assert opportunity.priority_score is None


def test_all_editorial_model_schemas_are_closed_using_runtime_sdk_helper():
    models = {
        value for module in (schemas, editorial_contracts)
        for _, value in inspect.getmembers(module, inspect.isclass)
        if issubclass(value, BaseModel) and value is not BaseModel
    }
    for model in models:
        assert_strict_objects(generation.type_to_text_format_param(model))


_DYNAMIC_SCHEMAS = [schemas.Dossier, schemas.Review, *(
    getattr(editorial_contracts, name) for name in (
        'Audit', 'EditPlan', 'EditorialDecision', 'SpokenExtraction', 'BackgroundKnowledge',
        'BlockKnowledge', 'KnowledgeAudit', 'VideoContext', 'TopicRouting', 'TopicComparison',
        'TopicPlan', 'PlanStructure', 'EditorialPlan', 'DraftArticle', 'DraftSection',
        'PassageAudit', 'VideoFidelityReview', 'ResearchResolution'))]


@pytest.mark.parametrize('schema', _DYNAMIC_SCHEMAS, ids=lambda schema: schema.__name__)
def test_actual_dynamic_editorial_requests_have_closed_objects(job, schema, monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('No provider call allowed'))
    sources = generation.evidence_map(job)
    scope = {
        'context_sources': sources, 'role': 'offline-integrity',
        'article_passages': ['O autor observa o desenvolvimento das folhas.', ''],
        'article_passage_refs': {'p0': '', 'p1': 'O autor observa o desenvolvimento das folhas.'},
        'article_title': job['article']['title'],
        'source_excerpts_by_id': {'v1s1': [job['sources'][0]['segments'][0]['text']]},
        'knowledge': {'rules': [{'id': 'local.rule'}]},
        'edit_blocks': {'b1': {'field': 'markdown', 'text': job['article']['markdown']}},
    }
    payload = {
        'items': [{'id': 'k1', 'check': {'status': 'supported'}}],
        'video_index': [{'id': 'k1'}], 'topic_index': [{'id': 'k1'}],
        'dispositions': [{'item_id': 'k1', 'status': 'used'}],
        'section': {'item_ids': ['k1']}, 'passages': [{'id': 'p1'}],
        'issues': [{'id': 'issue1'}], 'pending': [{'id': 'issue1', 'origin': 'comparison'}],
        'mentioned_terms': [{'term': 'folhas'}], 'video_segments': sources,
        'videos': [{'id': 'v1', 'segments': job['sources'][0]['segments']}],
        'source_guidance': {'videos': [{'supported_item_ids': ['k1']}]},
    }
    token = generation.agent_scope.set(scope)
    try:
        request, wire, *_ = generation.prepare_structured(job, schema, 'Sonda offline', 'offline', payload)
    finally:
        generation.agent_scope.reset(token)
    assert wire is not schema  # Exercise the generated contracts, not a mock schema.
    assert_strict_objects(request['text']['format'])


def test_nested_metrics_null_zero_and_internal_link_aliases_are_preserved():
    result = PerformanceAnalysis(summary='Sem todas as métricas.', top_queries=[
        {'query': 'horta', 'clicks': 0}], growing_pages=[{'url': 'https://example.org/horta'}]).model_dump()
    assert result['top_queries'][0]['clicks'] == 0
    assert result['top_queries'][0]['impressions'] is None
    assert result['top_queries'][0]['position'] is None
    assert result['growing_pages'][0]['clicks'] is None
    curation = ContentCuration(summary='Vídeo disponível.', selected_videos=[{'video_id': 'abcdefghijk'}])
    assert curation.model_dump()['selected_videos'][0]['transcript_available'] is None
    results = ResultsAnalysis(summary='Sem comparação.', interventions_reviewed=[{'opportunity_id': 'op1'}])
    assert results.model_dump()['interventions_reviewed'][0]['baseline_clicks'] is None
    links = ContentArchitecture(summary='Arquitetura.', internal_link_suggestions=[
        {'from': 'https://example.org/a', 'to': 'https://example.org/b', 'anchor': 'Leia'}]).model_dump()
    assert links['internal_link_suggestions'][0]['from'] == 'https://example.org/a'
    assert links['internal_link_suggestions'][0]['to'] == 'https://example.org/b'


@pytest.mark.parametrize('metric,value', [
    ('clicks', -1), ('clicks', True), ('impressions', 1.5),
    ('ctr', 1.01), ('ctr', float('nan')), ('position', float('inf'))])
def test_nested_measurements_reject_invalid_values(metric, value):
    with pytest.raises(ValidationError):
        PerformanceAnalysis(summary='Valor inválido.', top_queries=[{'query': 'horta', metric: value}])


def test_unknown_new_output_keys_are_rejected_instead_of_silently_dropped():
    with pytest.raises(ValidationError):
        PerformanceAnalysis(summary='Métrica fora do contrato.',
                            top_queries=[{'query': 'horta', 'fabricated_metric': 42}])
