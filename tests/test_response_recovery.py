import json
from copy import deepcopy

import httpx
import pytest
from openai import OpenAI
from openai.types.responses import Response
from pydantic import ValidationError

from app import db, generation, pipeline
from app.editorial import engine, store
from app.schemas import Article


structured = generation.structured


def response(text='', status='completed', reason=None, refusal=False, phase=None):
    return {'id': 'resp_test', 'object': 'response', 'created_at': 1, 'model': 'gpt-4.1-mini',
            'status': status, 'incomplete_details': {'reason': reason} if reason else None,
            'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': status,
                        'phase': phase, 'content': [{'type': 'refusal', 'refusal': 'private refusal text'}] if refusal else
                        [{'type': 'output_text', 'text': text, 'annotations': []}]}],
            'usage': {'input_tokens': 123, 'output_tokens': 8000 if reason == 'max_output_tokens' else 100,
                      'total_tokens': 8123 if reason == 'max_output_tokens' else 223,
                      'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}


def provider(monkeypatch, replies):
    """Exercise the installed SDK and request serialization over an in-memory HTTP transport."""
    requests = []
    replies = iter(replies)
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))
    monkeypatch.setattr(generation, 'client', lambda: OpenAI(api_key='test-only', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    return requests


def parsed(raw):
    # The SDK's normal constructor also constructs nested output types.
    from openai._models import construct_type
    return construct_type(value=raw, type_=Response)


def test_completed_response_uses_strict_schema_and_records_usage(job, monkeypatch):
    requests = provider(monkeypatch, [response(json.dumps(job['article']))])
    assert structured(job, Article, 'Escreva sobre a horta.', 'writing') == job['article']
    assert len(requests) == 1
    fmt = requests[0]['text']['format']
    assert fmt['type'] == 'json_schema' and fmt['strict']
    assert fmt['schema']['additionalProperties'] is False
    assert set(fmt['schema']['required']) == set(Article.model_fields)
    assert requests[0]['store'] is False
    assert db.get_job(job['id'])['usage'][-1]['response_status'] == 'completed'


@pytest.mark.parametrize('raw,reason,retryable', [
    (response('{"title":"private unfinished text', 'incomplete', 'max_output_tokens'), 'max_output_tokens', True),
    (response('{"title":"private unfinished text'), 'invalid_output', True),
    (response('{"unknown":"private invalid fields"}'), 'invalid_output', True),
    (response('', refusal=True), 'refusal', False),
    (response('{"title":"partial', 'incomplete', 'content_filter'), 'content_filter', False),
    (response('', 'failed'), 'incomplete', False),
    (response('', 'incomplete', 'max_output_tokens', refusal=True), 'refusal', False),
    (response(''), 'missing_output', True),
    (response('{"title":"commentary"}', phase='commentary'), 'missing_output', True),
])
def test_response_failures_are_classified_before_parsing(raw, reason, retryable):
    with pytest.raises(generation.GenerationResponseError) as error:
        generation.parse_structured_response(parsed(raw), Article)
    assert error.value.reason == reason
    assert error.value.retryable is retryable
    message = pipeline.safe_error(error.value)
    assert not any(secret in message for secret in ('private', 'input_value', 'pydantic', 'JSON'))


def test_truncated_writer_retries_once_and_keeps_both_usage_records(job, monkeypatch):
    engine.start(job, 'generate')
    job['dossier'] = {}
    requests = provider(monkeypatch, [response('{"title":"unfinished', 'incomplete', 'max_output_tokens'),
                                     response(json.dumps(job['article']))])
    article, run_id = engine.invoke(job, 'writer', callback=generation.write_article)
    assert article == job['article']
    assert [r['max_output_tokens'] for r in requests] == [8000, 12000]
    assert 'tentativa anterior' in requests[1]['instructions']
    assert 'unfinished' not in requests[1]['input']
    assert job['editorial']['calls'] == 2
    assert job['editorial']['completed']['writer'] == run_id
    runs = store.report(job)['runs']
    assert {r['status'] for r in runs} == {'completed', 'failed'}
    assert len(job['usage']) == 2
    assert job['usage'][0]['incomplete_reason'] == 'max_output_tokens'
    assert all(len(r['data']['usage']) == 1 for r in runs)
    assert 'unfinished' not in json.dumps(runs)
    assert generation.agent_scope.get() is None


def test_repeated_truncation_preserves_article_and_resume_skips_completed_roles(job, newsroom_ai, monkeypatch):
    original = deepcopy(job['article'])
    requests = provider(monkeypatch, [response('{"title":"private', 'incomplete', 'max_output_tokens'),
                                     response('{"title":"private', 'incomplete', 'max_output_tokens'),
                                     response(json.dumps(original))])
    stages = []
    def deliver(current, schema, instruction, stage, extra=None):
        stages.append(stage)
        if schema is Article:
            return structured(current, schema, instruction, stage, extra)
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = deliver
    pipeline.run(job['id'])
    failed = db.get_job(job['id'])
    assert failed['status'] == 'error'
    assert failed['article'] == original
    assert failed['error'] == generation.INVALID_RESPONSE_MESSAGE
    assert len(requests) == 2
    completed = deepcopy(failed['editorial']['completed'])
    assert {store.get_run(run_id)['name'] for run_id in completed.values()} == {
        'Extrator de conhecimento', 'Checador das fontes', 'Editor de pauta'}
    assert len(completed) == 8
    assert db.revisions(job['id']) == []
    stages.clear()
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'ready', saved.get('error')
    assert all(saved['editorial']['completed'][k] == v for k, v in completed.items())
    assert not set(stages) & {'dossier', 'source_checker', 'planner'}
    assert stages[0] == 'writing'
    assert saved['editorial']['calls'] == 20
    assert len(requests) == 3


@pytest.mark.parametrize('reason', ['refusal', 'content_filter', 'budget'])
def test_no_retry_for_refusal_filter_or_exhausted_budget(job, monkeypatch, reason):
    engine.start(job, 'generate')
    job['dossier'] = {}
    if reason == 'budget':
        job['editorial']['calls'] = job['editorial']['profile']['profile']['max_calls'] - 1
    raw = response('', refusal=True) if reason == 'refusal' else response('', 'incomplete',
                'max_output_tokens' if reason == 'budget' else 'content_filter')
    requests = provider(monkeypatch, [raw])
    with pytest.raises(generation.GenerationResponseError):
        engine.invoke(job, 'writer', callback=generation.write_article)
    assert len(requests) == 1
    assert 'writer' not in job['editorial']['completed']
    assert 'response_recoveries' not in job['editorial']
    assert len(db.get_job(job['id'])['usage']) == 1


def test_validation_errors_and_saved_legacy_messages_do_not_expose_input(authed, job):
    with pytest.raises(ValidationError) as error:
        Article.model_validate_json('{"title":"private article')
    assert pipeline.safe_error(error.value) == generation.INVALID_RESPONSE_MESSAGE
    legacy = str(error.value)
    job['error'] = legacy
    job['events'] = [{'time': db.now(), 'message': legacy}]
    db.save_job(job)
    returned = authed.get(f'/api/jobs/{job["id"]}').json()
    assert returned['error'] == generation.INVALID_RESPONSE_MESSAGE
    assert returned['events'][0]['message'] == generation.INVALID_RESPONSE_MESSAGE
    assert authed.get('/api/jobs').json()[0]['error'] == generation.INVALID_RESPONSE_MESSAGE
    assert db.get_job(job['id'])['error'] == legacy  # read-time redaction, not a data migration
