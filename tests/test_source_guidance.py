from copy import deepcopy
import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import db, generation, pipeline
from app.editorial import engine, guidance, store, workflow
from app.editorial.contracts import DraftArticle, DraftSection, PlanStructure, VoiceProfile
from test_delivery_contracts import plan_delivery, prepare


def information(ident, source_id, statement='Uma contribuição factual da fonte.', status='supported'):
    return {'id': ident, 'video_id': 'untrusted-label', 'block_id': 'b1', 'topic': 'assunto',
            'statement': statement, 'kind': 'fato', 'information_type': 'conceito', 'method': '',
            'conditions': [], 'quantities': [], 'restrictions': [], 'limitations': [],
            'evidence': [{'source_id': source_id, 'excerpt': statement}],
            'check': {'status': status, 'reason': 'Conferido contra a fonte.'}}


def test_video_guide_uses_evidence_membership_and_source_order_not_names_or_web_volume():
    job = {'sources': [{'id': 'user-media', 'title': 'Uma explicação', 'segments':
        [{'id': 'later-name', 'text': 'Primeiro trecho.'}, {'id': 'earlier-name', 'text': 'Segundo trecho.'}]}],
        'research': {'sources': [{'id': 'v1s1', 'text': 'Pesquisa.'}]},
        'apuration': {'items': [information('last', 'earlier-name'), information('first', 'later-name'),
                               information('uncertain', 'later-name', status='uncertain'),
                               *[information(f'web{i}', 'v1s1') for i in range(48)],
                               information('unknown', 'absent')],
                      'videos': [{'id': 'user-media', 'relations': [
                          {'item_ids': ['first', 'last'], 'relation': 'sequence', 'explanation': 'Uma depende da outra.',
                           'check': {'status': 'supported', 'reason': 'Conferido.'}},
                          {'item_ids': ['first', 'last'], 'relation': 'condition', 'explanation': 'Não confirmada.',
                           'check': {'status': 'uncertain', 'reason': 'Insuficiente.'}}]}]}}
    before = deepcopy(job)
    guide = guidance.source_guide(job)
    assert job == before
    video = guide['videos'][0]
    assert video['ordered_item_ids'] == ['first', 'uncertain', 'last']
    assert video['supported_item_ids'] == ['first', 'last']
    assert len(video['relations']) == 1 and video['relations'][0]['relation'] == 'sequence'
    assert guide['research_item_ids'] == []
    assert guide['excluded_internal_context_item_ids'] == [f'web{i}' for i in range(48)]
    assert guide['unclassified_item_ids'] == ['unknown']
    assert guidance.video_item_ids(job) == {'first', 'last'}


def test_video_guide_rejects_mixed_web_evidence_and_flagged_internal_items():
    mixed = information('mixed', 'original')
    mixed['evidence'].append({'source_id': 'web', 'excerpt': 'Dado de fora do vídeo.'})
    internal = {**information('internal', 'original'), 'internal_context_only': True}
    job = {'sources': [{'id': 'video', 'segments': [{'id': 'original', 'text': 'Explicação falada.'}]}],
           'research': {'sources': [{'id': 'web', 'verified': True}]},
           'apuration': {'items': [information('video-only', 'original'), mixed, internal]}}
    guide = guidance.source_guide(job)
    assert guide['videos'][0]['supported_item_ids'] == ['video-only']
    assert guide['research_item_ids'] == []
    assert guide['excluded_internal_context_item_ids'] == ['mixed', 'internal']


def test_plan_requires_reader_journey_based_on_video_items_not_research_ids():
    guide = {'videos': [{'supported_item_ids': ['primary', 'excluded']}], 'research_item_ids': ['web']}
    payload = {'dispositions': [{'item_id': i, 'status': 'used'} for i in ('primary', 'web')],
               'source_guidance': guide}
    schema, _, _ = prepare(PlanStructure, payload)
    result = plan_delivery(['primary', 'web'])
    with pytest.raises(ValidationError):
        schema.model_validate(result)
    journey = {'kind': 'sequencial', 'goal': 'Executar uma tarefa com segurança.',
               'reason': 'A dúvida solicita uma tarefa que as fontes explicam em ordem.', 'video_item_ids': ['primary']}
    result['reader_journey'] = journey
    assert schema.model_validate(result).reader_journey.kind == 'sequencial'
    for invalid in (None, *({**journey, 'video_item_ids': value} for value in ([], ['web'], ['excluded'], ['source-id']))):
        with pytest.raises(ValidationError):
            schema.model_validate({**result, 'reader_journey': invalid})


def test_reader_journey_is_mandatory_and_scoped_over_actual_sdk(job, monkeypatch):
    from test_response_recovery import provider, response
    engine.start(job, 'plan')
    result = plan_delivery(['primary'])
    result['reader_journey'] = {'kind': 'comparativo', 'goal': 'Escolher entre alternativas.',
        'reason': 'Os vídeos comparam alternativas com critérios verificáveis.', 'video_item_ids': ['primary']}
    requests = provider(monkeypatch, [response(json.dumps(result))])
    parsed = workflow.call(job, 'planner', PlanStructure, 'Planeje a resposta.',
        {'source_guidance': {'videos': [{'supported_item_ids': ['primary']}], 'research_item_ids': []},
         'dispositions': [{'item_id': 'primary', 'status': 'used'}]}, 'grounded-plan')
    assert parsed['reader_journey'] == result['reader_journey']
    schema = requests[0]['text']['format']['schema']
    assert 'reader_journey' in schema['required']
    assert schema['$defs']['GroundedReaderJourney']['properties']['video_item_ids']['items']['const'] == 'primary'
    assert len(requests) == 1


@pytest.mark.parametrize('topic,genre,instructions', [
    ('Configurar uma impressora', 'explicação', 'Explique a tarefa em etapas e como verificar o resultado.'),
    ('Comparar dois notebooks', 'comparação', 'Compare pelos critérios demonstrados nos vídeos.'),
    ('Entender frações', 'explicação', 'Explique conceitos e exemplos sem inventar um procedimento.'),
    ('Analisar uma mudança de transporte', 'análise', 'Organize argumentos, efeitos e limitações das fontes.'),
    ('Resenha de um livro', 'resenha', 'Diferencie descrição da obra e avaliações atribuídas.'),
])
def test_context_is_specific_to_current_brief_and_preserves_all_five_videos(job, topic, genre, instructions):
    job['brief'].update(topic=topic, genre=genre, instructions=instructions)
    job['sources'] = [{'id': f'source{n}', 'title': topic, 'url': 'https://example.com/video',
                       'segments': [{'id': f'segment{n}', 'text': f'Contribuição {n} para {topic}.'}]}
                      for n in range(1, 6)]
    job['apuration'] = {'valid': True, 'items': [information(f'item{n}', f'segment{n}') for n in range(1, 6)],
                        'videos': [], 'pending': []}
    engine.start(job, 'plan')
    scope, _, _ = engine.invocation_inputs(job, 'planner', {'_context_sources': {}}, None, 'plan')
    token = generation.agent_scope.set(scope)
    try:
        data = json.loads(generation.context(job, {'_context_sources': {}}))
    finally:
        generation.agent_scope.reset(token)
    assert data['briefing']['topic'] == topic
    assert data['briefing']['genre'] == genre
    assert data['briefing']['instructions'] == instructions
    videos = data['equipe_editorial']['source_guidance']['videos']
    assert [v['supported_item_ids'] for v in videos] == [[f'item{n}'] for n in range(1, 6)]
    assert data['equipe_editorial']['profile']['brand_name'] == job['editorial']['profile']['brand_name']


def test_new_plan_records_basis_and_rejects_web_only_reader_journey(job, newsroom_ai):
    pipeline.run(job['id'], 'plan')
    saved = db.get_job(job['id'])
    assert saved['status'] == 'plan_ready', saved.get('error')
    assert store.artifacts(job['id'], 'source_guidance')
    plan = deepcopy(saved['plan']['data'])
    assert plan['reader_journey']['video_item_ids'] == [saved['apuration']['items'][0]['id']]
    plan['reader_journey']['video_item_ids'] = []
    assert workflow.validate_plan(saved, plan)['ready_to_write'] is False
    plan['reader_journey']['video_item_ids'] = ['absent']
    with pytest.raises(generation.GenerationResponseError):
        workflow.validate_plan(saved, plan)


def test_unified_writer_receives_complete_article_route_and_all_video_explanations(job, newsroom_ai):
    from test_evidence_workflow import set_sources
    set_sources(job, count=2, segments=3, width=80)
    db.set_setting('editorial_profile', VoiceProfile(max_calls=8, context_chars=180000).model_dump())
    seen = []
    def respond(current, schema, instruction, stage, extra=None):
        if schema is DraftArticle:
            assert extra['article_route'] == guidance.article_route(current['plan']['data'])
            used = {d['item_id'] for d in current['plan']['data']['dispositions'] if d['status'] == 'used'}
            assert {item['id'] for item in extra['items']} == used
            assert {item['video_id'] for item in extra['items']} == {'v1', 'v2'}
            assert set(extra['_context_sources']) <= set(generation.evidence_map(current))
            assert all(item.get('source_spoken_insight') for item in extra['items'])
            seen.append(extra['article_route'])
        return newsroom_ai.respond(current, schema, instruction, stage, extra)
    newsroom_ai.side_effect = respond
    pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['status'] in ('ready', 'needs_review'), saved.get('error')
    assert len(seen) == 1 and saved['editorial']['calls'] == 4
    assert not any(call.args[1] is DraftSection for call in newsroom_ai.call_args_list)


def test_internal_prompt_literals_do_not_embed_the_example_industry():
    # Inspect application literals only; customer inputs and test cases may use any topic.
    forbidden = ('cultivo', 'cannabis', 'germinação', 'sementes', 'dormência', 'substrato', 'tempos de molho')
    paths = [Path('app/generation.py'), *Path('app/editorial').glob('*.py'), *Path('app/strategy').glob('*.py')]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(word in node.value.casefold() for word in forbidden), (path, node.lineno)


def test_research_receives_video_context_and_specific_questions_without_promoting_notes_to_evidence(job, monkeypatch):
    from test_response_recovery import provider, response
    engine.start(job, 'plan')
    source = job['sources'][0]
    job['apuration'] = {'valid': True, 'items': [information('primary', source['segments'][0]['id'])],
                       'videos': [{'id': source['id'], 'summary': 'Uma comparação por critérios explícitos.'}]}
    requests = provider(monkeypatch, [response('Uma nota sem citação utilizável.')])
    result, _ = engine.invoke(job, 'extractor',
        {'findings': [{'reason': 'Esclareça manjericão.'}],
         'mentioned_terms': [{'term': 'manjericão', 'source_segment_ids': ['v1s1']}],
         '_local_context': True, '_context_sources': {}, 'research_tool_budget': 2},
        callback=generation.research, slot='focused-research')
    data = json.loads(requests[0]['input'])
    assert data['source_guidance']['videos'][0]['supported_item_ids'] == ['primary']
    assert data['video_contexts'] == [{'source_id': source['id'], 'summary': 'Uma comparação por critérios explícitos.'}]
    assert data['pedidos_da_equipe'] == [{'reason': 'Esclareça manjericão.'}]
    assert data['mentioned_terms'] == [{'term': 'manjericão', 'source_segment_ids': ['v1s1']}]
    assert data['briefing'] == job['brief']
    assert result['sources'] == [] and result['status'] == 'unavailable'
    assert result['internal_context_only'] is True
    assert job['editorial']['calls'] == 3 and job['editorial']['tool_calls'] == 2 and len(requests) == 1


def test_research_context_refusal_happens_before_provider_and_preserves_budget(job, monkeypatch):
    from test_response_recovery import provider
    engine.start(job, 'plan')
    job['editorial']['profile']['profile']['context_chars'] = 30000
    job['apuration'] = {'valid': True, 'items': [],
                       'videos': [{'id': job['sources'][0]['id'], 'summary': 'Contexto extenso. ' * 3000}]}
    requests = provider(monkeypatch, [])
    with pytest.raises(generation.ContextLimitExceeded):
        engine.invoke(job, 'extractor', {'_local_context': True, '_context_sources': {}},
                      callback=generation.research, slot='oversized-research')
    assert not requests and not job.get('usage')
    assert job['editorial']['calls'] == 0


def test_repeated_headings_rejected_but_common_subheadings_under_different_parents_allowed(job):
    job['article']['markdown'] = '## Primeiro\n\n### Limites\n\nUma observação. [[v1s1]]\n\n## Segundo\n\n### Limites\n\nOutra observação. [[v1s1]]'
    assert not generation.deterministic_findings(job)
    job['article']['markdown'] += '\n\n## Segundo\n\nTexto adicional. [[v1s1]]'
    assert any('título' in f['reason'] for f in generation.deterministic_findings(job))
