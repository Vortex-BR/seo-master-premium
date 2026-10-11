"""Requirement fidelity and automatic-length repair with benign source material."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app import db, generation, pipeline, spending
from app.editorial import brief_contract, delivery_contracts, engine, quality_repair, reference_contracts, store, video_first
from app.editorial.contracts import DraftArticle, VideoFidelityReview
from test_quality_repair_acceptance import CORRECTED_BODY, furniture, run_furniture
from test_reverse_source_review import cooking_job, offline_review


def enable_brief(job):
    job['editorial'] = {'brief_contract_version': 1}
    job['brief'].update(main_question='Qual é a primeira ação? Por que fazer isso?',
                        exclusions='- Não citar marcas.\n- Não incluir anúncios.',
                        instructions='Explique a ação e a razão, sem prometer resultados.')
    return job


def assessments(output, payload):
    body = payload['_body_development_ids']
    for source in output['source_assessments']:
        source['detail_assessments'] = [{'excerpt': payload['_context_sources'][source['source_id']]['text'][:2400],
            'status': 'covered', 'passage_ids': source['passage_ids'], 'reason': 'Conferência simulada.'}]
    output['brief_assessments'] = [{'requirement_id': r['id'], 'status': 'met',
        'reason': 'Requisito atendido no exemplo controlado.', 'passage_ids': body[:1],
        'correction': '', 'excluded_source_ids': []} for r in payload['brief_requirements']]


def requirement(output, payload, field, offset=0):
    ident = [r['id'] for r in payload['brief_requirements'] if r['field'] == field][offset]
    return next(a for a in output['brief_assessments'] if a['requirement_id'] == ident)


def test_all_questions_and_each_constraint_survive_without_semantic_rewriting(cooking_job):
    job = enable_brief(cooking_job)
    requirements = brief_contract.requirements(job)
    assert [r['text'] for r in requirements if r['field'] == 'main_question'] == [
        'Qual é a primeira ação?', 'Por que fazer isso?']
    assert [r['text'] for r in requirements if r['field'] == 'exclusions'] == [
        'Não citar marcas.', 'Não incluir anúncios.']
    assert len({r['id'] for r in requirements}) == len(requirements)
    assert requirements == brief_contract.requirements(deepcopy(job))
    del job['editorial']['brief_contract_version']
    assert brief_contract.requirements(job) == []


@pytest.mark.parametrize('status,field', [('violated', 'exclusions'), ('missing', 'main_question'),
                                       ('partial', 'instructions'), ('conflict', 'instructions')])
def test_positive_global_verdict_cannot_override_an_unmet_requirement(cooking_job, offline_review, status, field):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        requirement(output, payload, field).update(status=status,
            reason='O requisito não foi cumprido no cenário simulado.',
            correction='Corrija o aspecto apontado usando a fonte, sem acrescentar fatos.')
    review = offline_review[0](job, respond)
    assert review['brief_compliance']['satisfied'] is False
    assert review['editorial_alignment']['matches_brief'] is False
    assert review['editorial_alignment']['claimed_matches_brief'] is True
    finding = next(f for f in review['findings'] if f['origin'] == 'brief_compliance')
    assert finding['assessment_status'] == status and finding['requirement']
    assert 'requisito(s) sem atendimento confirmado' in review['summary']


def test_heading_alone_does_not_answer_question(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        heading = next(p['id'] for p in payload['passages'] if p['text'].startswith('## '))
        requirement(output, payload, 'main_question')['passage_ids'] = [heading]
    with pytest.raises(ValueError, match='não responde'):
        offline_review[0](job, respond)


def test_unsupported_answer_cannot_be_declared_fulfilled(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        body = next(a for a in output['assessments'] if a['status'] == 'supported')
        body.update(status='unsupported', used_item_ids=[])
    review = offline_review[0](job, respond)
    questions = {r['id'] for r in review['brief_compliance']['requirements'] if r['field'] == 'main_question'}
    assert all(a['status'] == 'uncertain' for a in review['brief_compliance']['assessments'] if a['requirement_id'] in questions)
    assert review['editorial_alignment']['matches_brief'] is False


def test_excluded_promotion_is_not_an_omission_to_reinsert(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    job['sources'][0]['segments'].append({'id': 'v1s2', 'text': 'Compre a marca Patrocínio Exemplo.'})
    def respond(output, payload):
        assessments(output, payload)
        for passage in output['assessments']:
            passage['evidence'] = [e for e in passage['evidence'] if e['source_id'] != 'v1s2']
        source = next(s for s in output['source_assessments'] if s['source_id'] == 'v1s2')
        source.update(status='missing', passage_ids=[], missing_details=['Menção ao patrocinador.'])
        requirement(output, payload, 'exclusions').update(excluded_source_ids=['v1s2'], passage_ids=[])
    review = offline_review[0](job, respond)
    assert review['brief_compliance']['satisfied'] is True
    source = next(s for s in review['source_coverage']['assessments'] if s['source_id'] == 'v1s2')
    assert source['status'] == 'excluded' and source['excluded_by_brief']
    assert not any(f.get('source_ids') == ['v1s2'] for f in review['findings'])


def test_false_exclusion_cannot_hide_a_source_still_used_in_body(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        requirement(output, payload, 'exclusions')['excluded_source_ids'] = ['v1s1']
    review = offline_review[0](job, respond)
    assert review['brief_compliance']['satisfied'] is False
    assert review['brief_compliance']['excluded_source_ids'] == []
    assert review['source_coverage']['assessments'][0]['status'] == 'complete'


def test_wire_requires_every_requirement_and_restricts_identifiers(cooking_job):
    job = enable_brief(cooking_job)
    payload, _, _ = video_first.build_review_payload(job, job['article'])
    scoped = reference_contracts.scope(VideoFidelityReview, VideoFidelityReview, payload)
    child = scoped.model_fields['brief_assessments'].annotation.__args__[0]
    valid = {'requirement_id': payload['brief_requirements'][0]['id'], 'status': 'met',
             'reason': 'Cumprido no texto.', 'passage_ids': payload['_body_development_ids'][:1],
             'correction': '', 'excluded_source_ids': [], 'excluded_item_ids': []}
    child.model_validate(valid)
    for patch in ({'requirement_id': 'foreign'}, {'passage_ids': ['foreign']}, {'excluded_source_ids': ['foreign']}):
        with pytest.raises(ValidationError):
            child.model_validate({**valid, **patch})
    wire, adapter = delivery_contracts.prepare(VideoFidelityReview, scoped, payload)
    required = wire.model_fields['brief_assessments'].annotation
    assert set(required.model_fields) == {r['id'] for r in payload['brief_requirements']}
    assert adapter['requirement_ids'] == tuple(required.model_fields)
    with pytest.raises(ValidationError):
        required.model_validate({})


def test_auto_length_with_omission_is_corrected_and_resume_does_not_repay(furniture, monkeypatch):
    job, provider, boundary = furniture
    job['brief']['target_words'] = None
    db.save_job(job)
    network = Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr(generation, 'client', network)
    saved, _, _ = run_furniture(furniture)
    assert saved['editorial']['quality_repair']['status'] == 'accepted'
    assert saved['article']['markdown'] == CORRECTED_BODY
    assert saved['brief']['target_words'] is None
    assert boundary.call_count == 6
    assert provider.calls[4]['payload']['target_words_total'] is None
    pipeline.run(job['id'], 'resume')
    assert boundary.call_count == 6
    network.assert_not_called()


def test_brief_violation_alone_drives_revision_even_when_source_coverage_is_complete(furniture):
    job, provider, boundary = furniture
    job['brief'].update(exclusions='Não citar marcas.', target_words=None)
    db.save_job(job)
    def deliver(current, schema, instruction, stage, extra=None):
        output = provider(current, schema, instruction, stage, extra)
        extra = extra or {}
        if schema is DraftArticle and not extra.get('repair'):
            output['markdown'] = CORRECTED_BODY + '\n\nA marca Exemplo é citada nesta apresentação.'
        if schema is VideoFidelityReview:
            # Both drafts contain all source details. Only the exclusion differs.
            for a in output['source_assessments']:
                a.update(status='complete', missing_details=[])
            if not extra.get('previous_article'):
                violation = next(p for p in extra['passages'] if 'marca Exemplo' in p['text'])
                requirement(output, extra, 'exclusions').update(status='violated',
                    passage_ids=[violation['id']], reason='O texto inclui a marca excluída.',
                    correction='Retire a menção à marca e preserve a explicação da fonte.')
        return output
    boundary.side_effect = deliver
    saved, _, _ = run_furniture(furniture)
    assert saved['editorial']['quality_repair']['status'] == 'accepted', saved['editorial']['quality_repair']
    assert saved['review']['brief_compliance']['satisfied']
    assert 'marca Exemplo' not in saved['article']['markdown']
    assert boundary.call_count == 6
    assert any(f['origin'] == 'brief_compliance' for f in provider.calls[4]['payload']['repair']['findings'])


def test_repair_failure_is_visible_and_does_not_claim_success(furniture):
    _, provider, _ = furniture
    provider.outcome = 'reviewer_failure'
    saved, _, boundary = run_furniture(furniture)
    report = saved['editorial']['quality_repair']
    assert report['status'] == 'failed' and report['phase'] == 'reviewing'
    assert 'falha técnica' in saved['review']['summary']
    assert saved['editorial']['decision']['decision'] == 'revise'
    assert len([f for f in saved['review']['findings'] if f.get('code') == 'automatic_correction_incomplete']) == 1
    pipeline.run(saved['id'], 'resume')
    assert boundary.call_count == 6


def test_legacy_cycles_keep_paid_cache_contract(cooking_job):
    job = cooking_job
    payload, _, _ = video_first.build_review_payload(job, job['article'])
    assert 'brief_requirements' not in payload
    assert 'REQUISITOS A ATENDER' not in generation.editorial_instructions(job)


def test_resume_recovers_old_auto_length_preflight_failure_without_replaying_core_calls(furniture, monkeypatch):
    job, _, boundary = furniture
    job['brief']['target_words'] = None
    db.save_job(job)
    def old_failure(current):
        report = {'version': 1, 'status': 'failed', 'reason': 'TypeError', 'max_revisions': 1,
            'before_hash': generation.article_hash(current['article']), 'started_at': db.now(),
            'inputs_version': store.inputs_version(current), 'plan_version': current['plan']['version']}
        current['editorial']['quality_repair'] = report
        db.save_job(current)
        return report
    with monkeypatch.context() as old_code:
        old_code.setattr(quality_repair, 'run', old_failure)
        pipeline.run(job['id'])
    assert boundary.call_count == 4
    pipeline.run(job['id'], 'resume')
    saved = db.get_job(job['id'])
    assert saved['editorial']['quality_repair']['status'] == 'accepted'
    assert saved['editorial']['quality_repair']['recovered_preflight_error'] == 'automatic_length'
    assert boundary.call_count == 6
    pipeline.run(job['id'], 'resume')
    assert boundary.call_count == 6


def test_explicit_new_failure_phase_cannot_be_retried_as_legacy_bug(furniture, monkeypatch):
    job, _, boundary = furniture
    job['brief']['target_words'] = None
    db.save_job(job)
    def fail_locally(*args, **kwargs):
        raise TypeError('Different error with tracked phase')
    with monkeypatch.context() as broken:
        broken.setattr(quality_repair, '_request', fail_locally)
        pipeline.run(job['id'])
    saved = db.get_job(job['id'])
    assert saved['editorial']['quality_repair']['phase'] == 'preflight'
    assert boundary.call_count == 4
    pipeline.run(job['id'], 'resume')
    assert db.get_job(job['id'])['editorial']['quality_repair']['status'] == 'failed'
    assert boundary.call_count == 4


@pytest.mark.parametrize('status', ['partial', 'missing'])
def test_relevant_source_gap_prevents_blanket_approval(cooking_job, offline_review, status):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        output['source_assessments'][0].update(status=status, missing_details=['Explicar a ação inicial.'])
        if status == 'missing':
            output['source_assessments'][0]['passage_ids'] = []
    review = offline_review[0](job, respond)
    assert all(a['status'] == 'met' for a in review['brief_compliance']['assessments'])
    assert not review['brief_compliance']['satisfied']
    assert review['brief_compliance']['source_gaps'][0]['source_id'] == 'v1s1'
    assert not review['editorial_alignment']['matches_brief']


def test_planner_and_writer_receive_original_segments_missing_from_extraction(furniture):
    job, provider, _ = furniture
    job['sources'][0]['segments'].append({'id': 'v1s2', 'text': 'Antes de começar, confira o alinhamento.'})
    db.save_job(job)
    run_furniture(furniture)
    for call in provider.calls:
        if call['schema'] is DraftArticle or call['stage'] == 'planner':
            assert call['payload']['_context_sources']['v1s2']['text'] == 'Antes de começar, confira o alinhamento.'


def test_new_brief_contract_changes_cache_identity_but_absent_flag_does_not(furniture):
    saved, _, _ = run_furniture(furniture)
    args = ('writer', {'_local_context': True}, None, 'cache-check')
    enabled = engine.invocation_inputs(saved, *args)
    saved['editorial'].pop('brief_contract_version')
    legacy = engine.invocation_inputs(saved, *args)
    assert enabled != legacy
    assert 'brief_contract_version' not in legacy[0]
    saved['editorial']['brief_contract_version'] = None
    assert engine.invocation_inputs(saved, *args) == legacy


def test_preflight_count_can_fit_existing_budget_without_generation(monkeypatch):
    request = {'model': 'gpt-4o', 'input': 'source ' * 4000, 'max_output_tokens': 1000}
    count = Mock(return_value=SimpleNamespace(input_tokens=1000))
    create = Mock(side_effect=AssertionError('No paid generation'))
    api = SimpleNamespace(responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count), create=create))
    monkeypatch.setattr(generation, 'client', lambda: api)
    remaining = spending.amount('.06')
    assert spending.text_request_bound(request) * 2 > remaining
    bounds = quality_repair._preflight_bounds([(request, 0), (request, 5000)], remaining)
    assert sum(bounds) <= remaining and bounds[1] > bounds[0]
    assert count.call_count == 2
    create.assert_not_called()


@pytest.mark.parametrize('result', [None, True, -1, '100', 0])
def test_invalid_count_retains_offline_budget_bound(monkeypatch, result):
    request = {'model': 'gpt-4o', 'input': 'source ' * 4000, 'max_output_tokens': 1000}
    count = Mock(return_value=SimpleNamespace(input_tokens=result))
    api = SimpleNamespace(responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count)))
    monkeypatch.setattr(generation, 'client', lambda: api)
    bound, = quality_repair._preflight_bounds([(request, 0)], spending.amount('.03'))
    assert bound == spending.text_request_bound(request)
    count.assert_called_once()


def test_unaffordable_output_skips_even_token_count(monkeypatch):
    client = Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr(generation, 'client', client)
    request = {'model': 'gpt-4o', 'input': 'source', 'max_output_tokens': 1000}
    bound, = quality_repair._preflight_bounds([(request, 0)], spending.amount('.000001'))
    assert bound == spending.text_request_bound(request)
    client.assert_not_called()


def test_citation_suffix_is_not_duplicated_but_markdown_links_survive():
    result = {'paragraphs': [
        {'markdown': 'Texto. [v1s1]', 'source_ids': ['v1s1']},
        {'markdown': '[Documento](https://example.test) [v1s1]', 'source_ids': ['v1s1']},
        {'markdown': 'Outro. [nota]', 'source_ids': ['v1s1']},
    ], 'usage': {}}
    resolved = delivery_contracts.resolve(result, {'field': 'paragraphs', 'ids': []})
    assert resolved['markdown'] == ('Texto. [[v1s1]]\n\n[Documento](https://example.test) [[v1s1]]'
                                    '\n\nOutro. [nota] [[v1s1]]')


def test_detail_omission_overrides_coarse_complete_source_and_brief(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    job['sources'][0]['segments'][0]['text'] += ' Se encontrar impurezas, retire antes de cozinhar.'
    def respond(output, payload):
        assessments(output, payload)
        output['source_assessments'][0]['detail_assessments'].append({
            'excerpt': 'Se encontrar impurezas, retire antes de cozinhar.',
            'status': 'missing', 'passage_ids': [], 'reason': 'O cuidado não foi desenvolvido.'})
    review = offline_review[0](job, respond)
    source = review['source_coverage']['assessments'][0]
    assert source['status'] == 'partial' and source['claimed_status'] == 'complete'
    assert 'Se encontrar impurezas, retire antes de cozinhar.' in source['missing_details']
    assert not review['brief_compliance']['satisfied']
    assert not review['editorial_alignment']['matches_brief']
    assert any(f.get('origin') == 'source_coverage' for f in quality_repair.repair_findings({**job, 'review': review}))


@pytest.mark.parametrize('change', ['invented_quote', 'no_body'])
def test_detail_coverage_requires_literal_source_and_actual_body(cooking_job, offline_review, change):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        detail = output['source_assessments'][0]['detail_assessments'][0]
        detail.update({'excerpt': 'Uma fala inventada.'} if change == 'invented_quote' else {'passage_ids': []})
    with pytest.raises((ValueError, generation.GenerationResponseError)):
        offline_review[0](job, respond)


def test_wire_covers_every_source_reference_and_server_copies_quotes(cooking_job):
    from app.editorial import evidence_selection
    job = enable_brief(cooking_job)
    job['sources'][0]['segments'].append({'id': 'v1s2', 'text': 'Confira o apoio da panela antes de cozinhar.'})
    payload, _, sources = video_first.build_review_payload(job, job['article'])
    wire, options = evidence_selection.prepare(VideoFidelityReview, sources, sources)
    wire = reference_contracts.scope(VideoFidelityReview, wire, payload)
    wire, adapter = delivery_contracts.prepare(VideoFidelityReview, wire, payload, sources, evidence_options=options)
    required = wire.model_fields['source_details'].annotation
    assert set(required.model_fields) == set(options)
    valid = {key: [{'detail': option['excerpt'], 'status': 'missing', 'passage_ids': [],
                   'reason': 'Detalhe ausente.'}] for key, option in options.items()}
    required.model_validate(valid)
    with pytest.raises(ValidationError):
        required.model_validate({})
    with pytest.raises(ValidationError):
        required.model_validate({**valid, 'foreign': valid[next(iter(valid))]})
    raw = {'source_details': valid, 'source_assessments': {
        ident: {'status': 'missing', 'reason': 'Ausente.', 'passage_ids': [], 'missing_details': ['Detalhe ausente.']}
        for ident in sources}, 'brief_assessments': {}, 'assessments': {}}
    resolved = delivery_contracts.resolve(raw, {**adapter, 'requirement_ids': (), 'ids': ()})
    for source in resolved['source_assessments']:
        assert source['detail_assessments'][0]['excerpt'] == sources[source['source_id']]['text']
    assert 'source_details' not in resolved


def test_prohibited_item_can_be_excluded_without_excluding_mixed_source(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    job['sources'][0]['segments'][0]['text'] += ' Compre a marca Patrocínio Exemplo.'
    job['apuration']['items'] = [{'id': 'i2', 'video_id': 'v1', 'topic': 'Patrocinador',
        'statement': 'Compre a marca Patrocínio Exemplo.', 'kind': 'opinião', 'information_type': 'recomendação',
        'method': '', 'conditions': [], 'quantities': [], 'limitations': [], 'restrictions': [],
        'check': {'status': 'supported'},
        'evidence': [{'source_id': 'v1s1', 'excerpt': 'Compre a marca Patrocínio Exemplo.'}]}]
    job['plan'] = {'data': {'main_question': 'Como preparar o arroz?', 'title': 'Preparo',
        'opening': 'Introduzir o preparo.', 'closing': 'Concluir.', 'sections': [],
        'dispositions': [{'item_id': 'i2', 'status': 'used', 'reason': 'Plano antigo.'}]}}
    def respond(output, payload):
        assessments(output, payload)
        requirement(output, payload, 'exclusions').update(excluded_item_ids=['i2'], passage_ids=[])
    review = offline_review[0](job, respond)
    assert review['source_coverage']['assessments'][0]['status'] == 'complete'
    assert review['brief_compliance']['excluded_source_ids'] == []
    assert review['brief_compliance']['excluded_item_ids'] == ['i2']
    assert review['coverage'][0]['status'] == 'excluded'
    assert not any(f['origin'] == 'coverage' for f in review['findings'])
    before = deepcopy(review)
    before['brief_compliance']['excluded_item_ids'] = []
    before['brief_compliance']['assessments'][0]['status'] = 'violated'
    before['coverage'][0]['status'] = 'used'
    before['editorial_alignment']['matches_brief'] = False
    before['findings'].append({'severity': 'blocking', 'origin': 'brief_compliance'})
    review['preservation'] = {'preserves_supported_details': True, 'regressions': []}
    assert quality_repair._acceptable(job, before, job['article'], review)[0]


def test_coarse_exclusion_cannot_hide_an_uncertain_detail(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        source = output['source_assessments'][0]
        source['status'] = 'excluded'
        source['detail_assessments'][0]['status'] = 'uncertain'
    review = offline_review[0](job, respond)
    assert review['source_coverage']['assessments'][0]['status'] == 'uncertain'
    assert not review['brief_compliance']['satisfied']


def test_late_source_coherence_check_refreshes_brief_approval(cooking_job, offline_review):
    job = enable_brief(cooking_job)
    job['article']['markdown'] += '\n\nA apresentação termina aqui.'
    def respond(output, payload):
        assessments(output, payload)
        last = payload['passages'][-1]['id']
        output['assessments'][-1].update(status='not_factual', evidence=[], used_item_ids=[])
        source = output['source_assessments'][0]
        source['detail_assessments'][0]['passage_ids'] = source['passage_ids'][:1]
        assert last in source['passage_ids']
    review = offline_review[0](job, respond)
    assert review['source_coverage']['assessments'][0]['status'] == 'uncertain'
    assert all(a['status'] == 'met' for a in review['brief_compliance']['assessments'])
    assert not review['brief_compliance']['satisfied']
    assert not review['editorial_alignment']['matches_brief']


@pytest.mark.parametrize('status,details,expected', [
    ('complete', ['Falta desenvolver um cuidado da fonte.'], 'partial'),
    ('partial', [], 'uncertain'),
])
def test_conflicting_source_summary_is_reconciled_without_paid_format_retry(cooking_job, offline_review, status, details, expected):
    job = enable_brief(cooking_job)
    def respond(output, payload):
        assessments(output, payload)
        output['source_assessments'][0].update(status=status, missing_details=details)
    review = offline_review[0](job, respond)
    assert review['source_coverage']['assessments'][0]['status'] == expected
    assert not review['brief_compliance']['satisfied']
    assert len(offline_review[1]) == 1


def test_compact_wire_keeps_real_title_property_and_required_schema_rules(furniture):
    saved, provider, _ = run_furniture(furniture)
    payload = provider.calls[2]['payload']
    request = quality_repair._request(saved, 'writer', DraftArticle, 'Redija com as fontes.', payload, 'schema-check')
    schema = request['text']['format']['schema']
    assert len(request['text']['format']['name']) <= 64
    assert 'title' in schema['properties'] and 'title' in schema['required']
    def check(value):
        if isinstance(value, dict):
            if value.get('type') == 'object':
                assert set(value.get('required', [])) <= set(value.get('properties', {}))
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(schema)
