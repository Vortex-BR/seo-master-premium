from copy import deepcopy

import pytest

from app import db, generation, pipeline
from app.editorial import engine, reference_contracts, store
from app.editorial.contracts import BackgroundKnowledge, SpokenExtraction


def test_spoken_wire_allows_only_received_videos_and_transcript_ids():
    payload = {'videos': [{'id': 'v1', 'segments': [{'id': 'v1s1'}]}]}
    wire = reference_contracts.scope(SpokenExtraction, SpokenExtraction, payload)
    output = {'summary': 'Explicação.', 'videos': [{'video_id': 'v1', 'summary': 'Método.', 'gaps': [],
        'insights': [{'topic': 'método', 'spoken_explanation': 'A explicação do criador.',
                     'practical_tips': [], 'analogies': [], 'warnings': [], 'source_segment_ids': ['v1s1']}]}]}
    wire.model_validate(output)
    for key, invalid in [('video_id', 'v2'), ('source_segment_ids', ['rn1'])]:
        candidate = deepcopy(output)
        if key == 'video_id':
            candidate['videos'][0][key] = invalid
        else:
            candidate['videos'][0]['insights'][0][key] = invalid
        with pytest.raises(ValueError):
            wire.model_validate(candidate)


def test_background_wire_cannot_introduce_term_or_web_evidence():
    payload = {'mentioned_terms': [{'term': 'Docker'}], 'video_segments': {'v1s1': 'Uso Docker.'}}
    wire = reference_contracts.scope(BackgroundKnowledge, BackgroundKnowledge, payload)
    output = {'internal_context_only': True, 'terms': [{'term': 'Docker', 'explanation': 'Entendimento interno.',
              'source_segment_ids': ['v1s1'], 'internal_context_only': True}]}
    wire.model_validate(output)
    for field, value in [('term', 'Outro assunto'), ('source_segment_ids', ['rn1']), ('internal_context_only', False)]:
        candidate = deepcopy(output)
        candidate['terms'][0][field] = value
        with pytest.raises(ValueError):
            wire.model_validate(candidate)


@pytest.mark.parametrize('flag,kind', [(True, 'transcript'), (False, 'research_note'), (True, 'web_excerpt')])
def test_web_text_cannot_impersonate_transcript_in_context(job, flag, kind):
    engine.start(job, 'review')
    payload = {'_context_sources': {'v1s1': {'text': 'EXTERNAL PAYLOAD', 'kind': kind, 'internal_context_only': flag}}}
    scope, _, _ = engine.invocation_inputs(job, 'fact_reviewer', payload, None, 'boundary')
    assert scope['context_sources'] == {}
    token = generation.agent_scope.set(scope)
    try:
        assert 'EXTERNAL PAYLOAD' not in generation.context(job, payload)
    finally:
        generation.agent_scope.reset(token)


def test_only_engagement_video_requires_content_before_paid_work(job, newsroom_ai):
    job['sources'][0]['segments'][0]['text'] = 'Fala pessoal! Deixe o like! Se inscreve no canal! Ative o sininho!'
    original = deepcopy(job['article'])
    db.save_job(job)
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] == 'needs_input'
    assert saved['article'] == original
    assert saved['editorial']['calls'] == 0
    newsroom_ai.assert_not_called()


def test_new_generation_does_not_keep_handled_research_from_previous_cycle(job):
    job['research_requests_completed'] = ['previous-request']
    job['research_request_results'] = {'previous-request': {'text': 'Old notes'}}
    engine.start(job, 'generate')
    assert 'research_requests_completed' not in job
    assert 'research_request_results' not in job
    cycle = job['editorial']['cycle_id']
    job['research_requests_completed'] = ['current-request']
    engine.start(job, 'resume')
    assert job['editorial']['cycle_id'] == cycle
    assert job['research_requests_completed'] == ['current-request']
