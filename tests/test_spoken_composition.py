from copy import deepcopy

import pytest
from pydantic import ValidationError

from app import generation
from app.editorial import composition, drafts, engine, source_processing, store, text_checks, workflow
from app.editorial.contracts import BackgroundKnowledge, SpokenExtraction, VoiceProfile


def test_spoken_cleaning_preserves_explanation_analogy_experience_warning_and_originals():
    value = 'Isso é como abrir uma torneira. Na primeira vez que eu tentei, falhou. Cuidado para não apagar o original.'
    segments = [{'id': 'v1s1', 'start': 3.2, 'end': 21.8,
                 'text': 'Fala pessoal! Deixe o like e se inscreve no canal. ' + value,
                 'confidence': {'notes': ['original']}},
                {'id': 'v1s2', 'start': 22, 'end': 24, 'text': 'Solta a vinheta! Ative o sininho!'},
                {'id': 'v1s3', 'start': 25, 'end': 28, 'text': 'Ajuste a porta para 8080, né? Depois teste a conexão, tá ligado?'}]
    original = deepcopy(segments)
    cleaned = source_processing.clean_spoken_transcript(segments)
    assert segments == original
    assert [s['id'] for s in cleaned] == ['v1s1', 'v1s3']
    assert cleaned[0]['text'] == value
    assert cleaned[0]['start'] == 3.2 and cleaned[0]['end'] == 21.8
    assert cleaned[1]['text'] == 'Ajuste a porta para 8080. Depois teste a conexão.'
    cleaned[0]['confidence']['notes'].append('copy')
    assert segments == original


@pytest.mark.parametrize('text,expected', [
    ('Deixa o like, cuidado para não usar a senha padrão.', 'cuidado para não usar a senha padrão.'),
    ('O link na descrição permite baixar os arquivos para conferir a instalação.',
     'O link na descrição permite baixar os arquivos para conferir a instalação.'),
    ('Link na descrição. Na primeira tentativa, o conector estava invertido.',
     'Na primeira tentativa, o conector estava invertido.'),
    ('Não dê like em mensagens que pedem sua senha.', 'Não dê like em mensagens que pedem sua senha.'),
    ('Evite a frase "deixe o like" no texto de ajuda.', 'Evite a frase "deixe o like" no texto de ajuda.'),
    ('Fala pessoal! Isso é como "abrir uma torneira". Cuidado para não fechar o acesso.',
     'Isso é como "abrir uma torneira". Cuidado para não fechar o acesso.'),
    ('Link na descrição: cuidado para não substituir o original.', 'cuidado para não substituir o original.'),
    ('O circuito tá ligado? Confira a tensão antes de continuar.',
     'O circuito tá ligado? Confira a tensão antes de continuar.'),
    ('O token expira em 12.5 segundos; o código HTTP é 401.',
     'O token expira em 12.5 segundos; o código HTTP é 401.'),
    ('Antes de começar, deixe o like.', ''),
    ('Fala pessoal, bom, vamos lá. Sem mais delongas, solta a vinheta!', ''),
])
def test_spoken_cleaning_conservatively_handles_mixed_or_substantive_utterances(text, expected):
    result = source_processing.clean_spoken_transcript([{'id': 'v1s1', 'text': text}])
    assert (result[0]['text'] if result else '') == expected


def test_spoken_contract_carries_creator_didactics_and_only_internal_web_background():
    insight = {'topic': 'Conexão', 'spoken_explanation': 'É como abrir uma torneira.',
               'practical_tips': ['Teste a conexão.'], 'analogies': ['Abrir uma torneira.'],
               'warnings': ['Não apague o original.'], 'source_segment_ids': ['v1s1']}
    extracted = SpokenExtraction.model_validate({'summary': 'Explicação do criador.', 'videos': [
        {'video_id': 'v1', 'summary': 'Conexão.', 'insights': [insight], 'gaps': []}]})
    assert extracted.videos[0].insights[0].analogies == insight['analogies']
    background = {'terms': [{'term': 'HTTP', 'explanation': 'Contexto para entender o termo.',
                             'source_segment_ids': ['v1s1'], 'internal_context_only': True}],
                  'internal_context_only': True}
    assert BackgroundKnowledge.model_validate(background).internal_context_only is True
    with pytest.raises(ValidationError):
        BackgroundKnowledge.model_validate({**background, 'internal_context_only': False})
    with pytest.raises(ValidationError):
        BackgroundKnowledge.model_validate({**background, 'evidence': [{'source_id': 'rn1'}]})
    with pytest.raises(ValidationError):
        BackgroundKnowledge.model_validate({**background, 'terms': [{**background['terms'][0], 'section': 'Novo tópico'}]})


def test_video_first_profile_and_cost_estimate_do_not_scale_paid_calls_with_blocks(job):
    profile = VoiceProfile().model_dump()
    assert profile['max_calls'] == 24 and profile['max_spend_usd'] == 1.00
    assert profile['max_rounds'] == 0 and profile['research_tool_calls'] == 2
    for cap in (3, 25, 120):
        with pytest.raises(ValidationError):
            VoiceProfile(max_calls=cap)
    source = job['sources'][0]
    job['sources'] = [{**deepcopy(source), 'id': f'v{n}', 'segments': [
        {'id': f'v{n}s1', 'text': 'Explicação com contexto. ' * 1800, 'start': 0, 'end': 120}]} for n in range(1, 5)]
    estimate = source_processing.estimate(job, profile)
    assert estimate['blocks'] > 4
    assert estimate['estimated_calls_min'] == 4 and estimate['estimated_calls_max'] == 8
    assert estimate['fits_minimum']


def composition_setup(job, monkeypatch):
    insight = {'topic': 'Observação', 'spoken_explanation': 'Veja como as folhas crescem.',
               'practical_tips': ['Observe as folhas.'], 'analogies': [], 'warnings': [],
               'source_segment_ids': ['v1s1']}
    item = {'id': 'v1k1', 'video_id': 'v1', 'topic': 'Observação',
            'statement': job['sources'][0]['segments'][0]['text'], 'kind': 'experiência',
            'information_type': 'afirmação', 'method': '', 'conditions': [], 'quantities': [],
            'restrictions': [], 'limitations': [], 'check': {'status': 'supported'},
            'source_spoken_insight': insight,
            'evidence': [{'source_id': 'v1s1', 'excerpt': 'observa o desenvolvimento das folhas'}]}
    plan = {'main_question': 'Como observar a horta?', 'opening': 'Situar a observação.',
            'closing': 'Encerrar.', 'sections': []}
    job['editorial'] = {'video_first': True}
    job['plan'] = {'version': 'plan-version', 'data': plan}
    job['apuration'] = {'items': [item], 'videos': [], 'comparisons': []}
    recorded, artifacts, checkpoints = [], [], []
    monkeypatch.setattr(engine, 'cached_invocation', lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, 'invocation_inputs', lambda *args: ({}, [], 'fingerprint'))
    monkeypatch.setattr(generation, 'prepare_structured', lambda *args: None)
    monkeypatch.setattr(workflow, 'reserve', lambda *args: None)
    monkeypatch.setattr(workflow, 'dependencies', lambda *args: {'inputs': 'original'})
    monkeypatch.setattr(store, 'artifact', lambda current, kind, key, value, deps:
                        artifacts.append({'kind': kind, 'data': value}))
    monkeypatch.setattr(drafts, 'checkpoint', lambda current, article, **kwargs: checkpoints.append(article))
    monkeypatch.setattr(text_checks, 'analyze', lambda current: {'findings': [
        {'severity': 'blocking', 'code': 'deterministic_check', 'auto_repair': True}]})

    def deliver(current, role, schema, instruction, payload, slot, validate):
        recorded.append({'role': role, 'payload': payload, 'instruction': instruction})
        output = {**current['article'], 'used_item_ids': []}
        validate(output)
        return output

    monkeypatch.setattr(workflow, 'call', deliver)
    return plan, item, recorded, artifacts, checkpoints


def test_video_first_composes_once_and_saves_unresolved_checks_without_paid_repair(job, monkeypatch):
    plan, item, recorded, artifacts, checkpoints = composition_setup(job, monkeypatch)
    article = composition.write(job, plan, [item])
    assert article == job['article'] and len(recorded) == 1 and len(checkpoints) == 1
    assert recorded[0]['role'] == 'writer'
    payload = recorded[0]['payload']
    assert payload['items'][0]['source_spoken_insight'] == item['source_spoken_insight']
    assert payload['creator_voice'][0]['author'] == 'Autor de exemplo'
    assert payload['creator_voice'][0]['source_references'] == [
        {'source_id': 'v1s1', 'timestamp_reference': '[00:10]'}]
    assert set(payload['_context_sources']) == {'v1s1'}
    assert not any(a['kind'].startswith('composition_repair') for a in artifacts)
    coverage = next(a['data'] for a in artifacts if a['kind'] == 'draft_coverage')
    assert coverage['missing_item_ids'] == ['v1k1'] and coverage['delivery_checks']['findings']


def test_video_first_never_falls_back_to_sections_when_complete_article_exceeds_context(job, monkeypatch):
    plan, item, recorded, artifacts, checkpoints = composition_setup(job, monkeypatch)

    def too_large(*args):
        raise generation.ContextLimitExceeded('Nenhuma chamada foi feita.')

    monkeypatch.setattr(generation, 'prepare_structured', too_large)
    with pytest.raises(generation.ContextLimitExceeded):
        composition.write(job, plan, [item])
    assert not recorded and not artifacts and not checkpoints


def test_composition_refuses_web_claims_and_filters_web_counterpoints(job, monkeypatch):
    plan, item, recorded, _, _ = composition_setup(job, monkeypatch)
    web = {**deepcopy(item), 'id': 'webk1', 'video_id': 'research',
           'evidence': [{'source_id': 'rn1', 'excerpt': 'Explicação da web.'}]}
    job['apuration']['items'].append(web)
    job['apuration']['comparisons'] = [{'rows': [{'item_ids': [item['id'], web['id']]}]}]
    with pytest.raises(ValueError, match='trechos originais dos vídeos'):
        composition.write(job, plan, [web])
    assert not recorded
    composition.write(job, plan, [item])
    assert recorded[0]['payload']['counterpoints'] == []
    assert 'rn1' not in recorded[0]['payload']['_context_sources']


def test_creator_reference_uses_available_original_times_and_does_not_invent_missing_values():
    job = {'sources': [{'id': 'v1', 'author': '', 'title': 'Fonte', 'url': 'https://example.com/video',
                        'segments': [{'id': 'v1s1', 'start': 3723.9}, {'id': 'v1s2'},
                                     {'id': 'v1s3', 'start': float('nan')}, {'id': 'v1s4', 'start': -2}]}]}
    metadata = composition.creator_voice(job, {'v1s1', 'v1s2', 'v1s3', 'v1s4'})
    assert metadata[0]['author'] == ''
    assert [s['timestamp_reference'] for s in metadata[0]['source_references']] == ['[01:02:03]', None, None, None]
