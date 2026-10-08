from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app import generation
from app.editorial import agents, context_packing, engine, store
from test_response_recovery import provider, response


def unpack(article, blocks, passages=None):
    return {field: (''.join(part if isinstance(part, str) else blocks[part['edit_block']]['text']
                           if 'edit_block' in part else passages[part['article_passage']]
                           for part in value['content_parts'])
                    if isinstance(value, dict) and 'content_parts' in value else value)
            for field, value in article.items()}


def test_packing_keeps_order_whitespace_unindexed_text_and_original_fields():
    text = 'Uma explicação sobre arquitetura, com condições e ressalvas. ' * 8
    tail = 'Trecho final fora do índice de edição. ' * 12
    article = {'title': text, 'markdown': '\n' + text + '\n \n' + text + '\r\n\r\n' + tail,
               'tags': ['projeto'], 'excerpt': ''}
    blocks = {'b1': {'field': 'title', 'text': text}, 'b2': {'field': 'markdown', 'text': text}}
    before = deepcopy((article, blocks))
    packed = context_packing.pack_article(article, blocks)
    assert unpack(packed, blocks) == article
    assert (article, blocks) == before
    assert tail in packed['markdown']['content_parts'][-1]
    assert len(json.dumps(packed)) < len(json.dumps(article)) / 2


def test_packing_preserves_regex_characters_overlap_and_small_fields():
    text = 'Condições [A+B] (C?) **D** e ressalvas literais. ' * 10
    blocks = {'b1': {'field': 'markdown', 'text': text[:90]},
              'b2': {'field': 'markdown', 'text': text},
              'b3': {'field': 'title', 'text': 'Curto'}}
    article = {'title': 'Curto', 'markdown': text + '\n\nFinal preservado.'}
    packed = context_packing.pack_article(article, blocks)
    assert packed['title'] == 'Curto'
    assert packed['markdown']['content_parts'][0] == {'edit_block': 'b2'}
    assert unpack(packed, blocks) == article


def test_review_packing_preserves_every_character_outside_bounded_passage_index():
    text = 'Uma explicação completa com condições e ressalvas. ' * 12
    tail = 'Trecho final fora do índice; continua disponível para revisão. ' * 15
    article = {'title': 'Título', 'markdown': text + '\n\n' + tail, 'tags': ['arquitetura']}
    passages = {'p0': '', 'p1': text}
    packed = context_packing.pack_article(article, {}, passages)
    assert unpack(packed, {}, passages) == article
    assert packed['markdown']['content_parts'][0] == {'article_passage': 'p1'}
    assert packed['markdown']['content_parts'][-1] == '\n\n' + tail


def test_report_compaction_keeps_all_findings_and_their_full_literal_passages():
    text = 'Uma ressalva contextual que precisa ser conferida na redação. ' * 8
    findings = [{'passage': text + '\nUma frase fora do índice.', 'severity': 'blocking',
                 'reason': f'Conferir requisito {number}.', 'source_ids': ['s1']} for number in range(12)]
    original = deepcopy(findings)
    passages = {'p0': '', 'p1': text}
    packed = context_packing.pack_findings(findings, {}, passages)
    assert [unpack(finding, {}, passages) for finding in packed] == original
    assert findings == original and len(packed) == len(original)
    assert len(json.dumps(packed)) < len(json.dumps(original)) / 2


def test_large_editor_request_fits_without_raising_limit_or_losing_text(job, monkeypatch):
    job['article']['markdown'] = '\n\n'.join(
        f'Parágrafo {n}: ' + ('Uma explicação completa com condições próprias e exemplos concretos ' * 40)
        for n in range(12))
    engine.start(job, 'review')
    original = deepcopy(job['article'])
    payload = {'article': job['article']}
    scope, _, _ = engine.invocation_inputs(job, 'voice_editor', payload, None, 'voice_editor:0')
    spec = agents.ROLES['voice_editor']
    token = generation.agent_scope.set(scope)
    try:
        request = generation.prepare_structured(job, spec['schema'], spec['prompt'], 'voice_editor', payload)[0]
        material = json.loads(request['input'])
        assert unpack(material['artigo_para_revisar'], material['equipe_editorial']['edit_blocks']) == original
        assert material['equipe_editorial']['article_passage_refs'] == scope['article_passage_refs']
        assert material['fontes_para_conferencia'] == generation.evidence_map(job)
        assert len(request['input']) + len(request['instructions']) + len(json.dumps(request['text']['format'])) < 90000
        with monkeypatch.context() as patch:
            patch.setattr(context_packing, 'pack_article', lambda article, blocks, passages=None: article)
            with pytest.raises(generation.ContextLimitExceeded):
                generation.prepare_structured(job, spec['schema'], spec['prompt'], 'voice_editor', payload)
    finally:
        generation.agent_scope.reset(token)
    assert job['article'] == original and job['editorial']['calls'] == 0


def test_real_sdk_edit_ids_still_resolve_and_completed_delivery_is_reused(job, monkeypatch):
    engine.start(job, 'review')
    payload = {'article': deepcopy(job['article'])}
    scope, _, _ = engine.invocation_inputs(job, 'voice_editor', payload, None, 'edit')
    ident, block = next((i, b) for i, b in scope['edit_blocks'].items() if b['field'] == 'markdown')
    requests = provider(monkeypatch, [response(json.dumps({'summary': 'Ajuste local.', 'findings': [],
        'changes': [{'field': 'markdown', 'before': ident, 'after': block['text'] + ' ',
                     'reason': 'Espaçamento.', 'source_ids': [], 'rule_ids': []}]}))])
    result, run_id = engine.invoke(job, 'voice_editor', payload, slot='edit')
    assert result['changes'][0]['before'] == block['text']
    data = json.loads(requests[0]['input'])
    assert unpack(data['artigo_para_revisar'], data['equipe_editorial']['edit_blocks']) == payload['article']
    assert engine.invoke(job, 'voice_editor', payload, slot='edit') == (result, run_id)
    assert len(requests) == 1 and job['editorial']['calls'] == 1


def test_full_schema_is_budgeted_before_creating_a_run_or_provider_call(job, monkeypatch):
    engine.start(job, 'review')
    job['editorial']['profile']['profile']['context_chars'] = 30000
    original = generation.type_to_text_format_param
    monkeypatch.setattr(generation, 'type_to_text_format_param',
                        lambda schema: {**original(schema), 'description': 'x' * 30000})
    client = Mock(side_effect=AssertionError('No provider requests allowed'))
    monkeypatch.setattr(generation, 'client', client)
    with pytest.raises(generation.ContextLimitExceeded):
        engine.invoke(job, 'reader', {'article': job['article']})
    assert job['editorial']['calls'] == 0 and not job['usage']
    assert not store.report(job)['runs']
    client.assert_not_called()
