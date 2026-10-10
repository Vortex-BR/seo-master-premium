"""IEC provenance/edits are pure; semantic acceptance remains independent."""
from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app import db, generation
from app.editorial import changes, intelligence as core
from app.editorial.intelligence_contracts import (IECDiagnosis, IECProposalBatch, IECRequest,
                                                 IECSupport, IECValidation, safe_https_url)


@pytest.fixture
def pure_job(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('Pure IEC must not read DB or open providers.'))
    for name in ('connect', 'init', 'get_setting', 'save_job'):
        monkeypatch.setattr(db, name, forbidden)
    monkeypatch.setattr(generation, 'client', forbidden)
    return {'id': 'pure-iec', 'brief': {'topic': 'Manjericão', 'audience': 'Iniciantes'},
        'sources': [{'id': 'v1', 'video_id': 'abcdefghijk', 'title': 'Como regar',
                     'author': 'Ana Exemplo', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                     'segments': [{'id': 'v1s1', 'text': 'Observe a terra antes de regar.', 'start': 10, 'end': 20},
                         {'id': 'v1s2', 'text': 'Terra úmida conserva água; a observação evita rega desnecessária.',
                          'start': 30.7, 'end': 40},
                         {'id': 'v1s3', 'text': 'No meu caso, eu testei esta rotina e observei folhas saudáveis.',
                          'start': 80, 'end': 100}]}],
        'article': {'title': 'Como regar manjericão', 'seo_title': 'Como regar manjericão',
                    'slug': 'regar-manjericao', 'meta_description': 'Observe a terra antes de regar.',
                    'excerpt': 'Observe a terra.', 'tags': ['horta'],
                    'markdown': 'Ana Exemplo mostra os cuidados com o manjericão. [[v1s1]]\n\n'
                                '## Regar quando necessário\n\nObserve a terra antes de regar. [[v1s1]]\n\n'
                                'Acompanhe o desenvolvimento das folhas.'},
        'plan': {'version': 'saved-plan', 'data': {'main_question': 'Quando devo regar?'}},
        'apuration': {'version': 'saved-apuration', 'valid': False, 'items': [], 'videos': [], 'comparisons': []}}


def opportunity(job, *, block_index=2, identifier='gap-1', **fields):
    return {'id': identifier, 'block_id': core.article_blocks(job['article'])[block_index]['id'],
            'kind': 'missing_reason', 'question': 'Por que observar a terra?',
            'benefit': 'Evitar rega desnecessária.', 'evidence_ids': ['v1s2'],
            'external_query': '', 'already_explained': False, **fields}


def diagnosis(job, **fields):
    return {'status': 'opportunities', 'summary': 'Motivo útil disponível na fala.',
            'opportunities': [opportunity(job, **fields)]}


def support(foundation, *, reference_id=None, excerpt=None):
    text = foundation['text']
    excerpt = text if excerpt is None else excerpt
    start = text.index(excerpt)
    return {'reference_id': reference_id or foundation.get('id') or foundation['reference_id'],
            'excerpt': excerpt, 'offset_start': start, 'offset_end': start + len(excerpt)}


def batch(job, foundations, *, reference_id='v1s2', **fields):
    value = {'opportunity_id': 'gap-1', 'block_id': core.article_blocks(job['article'])[2]['id'],
             'addition': 'A observação evita rega desnecessária porque a terra úmida conserva água.',
             'origin': 'video', 'claim_nature': 'assertion',
             'supports': [support(foundations[reference_id], reference_id=reference_id)], 'limitations': '', **fields}
    return {'summary': 'Explicação localizada.', 'proposals': [value]}


def prepared(job, **fields):
    foundations = core.video_foundations(job)
    return core.prepare_proposals(job, diagnosis(job), batch(job, foundations, **fields), foundations)


def external_foundation(**fields):
    value = {'id': 'iecsrc-source', 'origin': 'external_verified',
             'text': 'A observação evita rega desnecessária porque a terra úmida conserva água.',
             'url': 'https://extension.example.edu/water', 'publisher': 'extension.example.edu',
             'fetched_at': '2026-10-10T12:00:00+00:00', 'expires_at': '2026-10-11T12:00:00+00:00',
             'verification_basis': 'readable_page_literal_anchor_pending_semantic',
             'limitations': ['Leitura literal não demonstra apoio semântico.'], **fields}
    value['record_hash'] = core.foundation_hash(value)
    return value


def test_video_candidate_is_local_anchored_not_truth_and_does_not_mutate(pure_job):
    before = json.dumps(pure_job, ensure_ascii=False, sort_keys=True)
    result = prepared(pure_job)
    assert not result['rejections'] and len(result['changes']) == 1
    candidate = result['accepted_candidates'][0]
    assert candidate['semantic_verification'] == 'pending'
    assert candidate['independent_truth_verified'] is False
    assert candidate['base_article_hash'] == generation.article_hash(pure_job['article'])
    assert '[00:30] [[v1s2]]' in candidate['after']
    edited = changes.preview(pure_job['article'], result['changes'])
    assert edited['markdown'].startswith(pure_job['article']['markdown'].split('\n\n')[0])
    assert edited['markdown'].endswith('Acompanhe o desenvolvimento das folhas.')
    assert candidate['references'][0]['offset_end'] == len(pure_job['sources'][0]['segments'][1]['text'])
    assert json.dumps(pure_job, ensure_ascii=False, sort_keys=True) == before


def test_original_cue_cites_parent_segment_and_original_cue_time(pure_job):
    segment = pure_job['sources'][0]['segments'][1]
    segment['original_cues'] = [{'id': 'v1c9', 'text': segment['text'], 'start': 33.2, 'end': 35}]
    foundations = core.video_foundations(pure_job, ['v1c9'])
    result = core.prepare_proposals(pure_job, diagnosis(pure_job),
                                   batch(pure_job, foundations, reference_id='v1c9'), foundations)
    assert not result['rejections']
    after = result['changes'][0]['after']
    assert '[00:33] [[v1s2]]' in after and '[[v1c9]]' not in after


def test_absent_time_does_not_invent_timestamp(pure_job):
    pure_job['sources'][0]['segments'][1].pop('start')
    pure_job['sources'][0]['segments'][1].pop('end')
    result = prepared(pure_job)
    assert not result['rejections'] and '[00:' not in result['changes'][0]['after']


def test_changed_original_source_rejects_saved_foundation_even_if_text_same(pure_job):
    foundations = core.video_foundations(pure_job)
    old_batch = batch(pure_job, foundations)
    pure_job['sources'][0]['author'] = 'Autor alterado'
    result = core.prepare_proposals(pure_job, diagnosis(pure_job), old_batch, foundations)
    assert result['rejections'][0]['code'] == 'video_source_changed_or_unresolved'


def test_ambiguous_reference_is_not_foundation(pure_job):
    pure_job['sources'][0]['segments'].append(deepcopy(pure_job['sources'][0]['segments'][1]))
    assert 'v1s2' not in core.video_foundations(pure_job)


def test_external_link_is_server_owned_without_fake_video_citation(pure_job):
    source = external_foundation(url='https://extension.example.edu/water_(plants)?lang=pt')
    sources = {source['id']: source}
    proposal = batch(pure_job, sources, reference_id=source['id'], origin='external_verified')
    result = core.prepare_proposals(pure_job, diagnosis(pure_job), proposal, sources)
    assert not result['rejections']
    added = result['changes'][0]['after'].removeprefix(result['changes'][0]['before'])
    assert '[Fonte complementar](https://extension.example.edu/water_%28plants%29?lang=pt)' in added
    assert '[[' not in added and '[00:' not in added
    assert result['accepted_candidates'][0]['references'][0]['publisher'] == source['publisher']


@pytest.mark.parametrize('field,value,code', [
    ('text', 'Fonte modificada.', 'foundation_hash_mismatch'),
    ('url', 'https://evil.example.org/source', 'foundation_hash_mismatch'),
    ('fetched_at', '2026-10-11T12:00:00+00:00', 'foundation_hash_mismatch'),
    ('internal_context_only', True, 'foundation_origin_mismatch'),
    ('origin', 'editorial_inference', 'foundation_origin_mismatch'),
])
def test_tampered_or_internal_foundation_is_rejected(pure_job, field, value, code):
    foundations = core.video_foundations(pure_job)
    proposal = batch(pure_job, foundations)
    foundations['v1s2'][field] = value
    result = core.prepare_proposals(pure_job, diagnosis(pure_job), proposal, foundations)
    assert result['rejections'][0]['code'] == code and not result['changes']


@pytest.mark.parametrize('changes_to_support', [
    {'excerpt': 'A observação evita problemas.', 'offset_end': 32},
    {'offset_start': 1}, {'offset_end': 100000},
])
def test_literal_anchor_and_offsets_must_match(pure_job, changes_to_support):
    foundations = core.video_foundations(pure_job)
    proposal = batch(pure_job, foundations)
    proposal['proposals'][0]['supports'][0].update(changes_to_support)
    result = core.prepare_proposals(pure_job, diagnosis(pure_job), proposal, foundations)
    assert result['rejections'][0]['code'] == 'foundation_excerpt_mismatch'


@pytest.mark.parametrize('fields,code', [
    ({'publisher': ''}, 'external_provenance_missing'),
    ({'fetched_at': None}, 'external_retrieval_metadata_missing'),
    ({'fetched_at': '2026-10-10T12:00:00'}, 'external_retrieval_metadata_invalid'),
    ({'expires_at': '2026-10-09T12:00:00+00:00'}, 'external_freshness_invalid'),
    ({'url': 'http://extension.example.edu/water'}, 'external_url_invalid'),
    ({'url': 'https://127.0.0.1/source'}, 'external_url_invalid'),
    ({'verified': False}, 'external_provenance_missing'),
])
def test_external_provenance_is_required(pure_job, fields, code):
    source = external_foundation(**fields)
    sources = {source['id']: source}
    result = core.prepare_proposals(pure_job, diagnosis(pure_job),
        batch(pure_job, sources, reference_id=source['id'], origin='external_verified'), sources)
    assert not result['changes'] and result['rejections'][0]['code'] == code


@pytest.mark.parametrize('addition,code', [
    ('Complemento.\n\nOutro parágrafo.', 'addition_multiple_blocks'),
    ('Complemento. [[v1s2]]', 'addition_unsafe_markup_or_citation'),
    ('Complemento. [[rn1]]', 'addition_unsafe_markup_or_citation'),
    ('Complemento. https://example.org', 'addition_unsafe_markup_or_citation'),
    ('Veja [esta página](javascript:alert(1)).', 'addition_unsafe_markup_or_citation'),
    ('<script>alert(1)</script> Explicação.', 'addition_unsafe_markup_or_citation'),
    ('### Novo título', 'addition_unsafe_markup_or_citation'),
    ('- Novo passo', 'addition_unsafe_markup_or_citation'),
    ('Complemento aos [08:00].', 'addition_unsafe_markup_or_citation'),
    ('Eu testei e obtive ótimos resultados.', 'invented_first_person'),
    ('Nosso método dá resultados comprovados.', 'invented_first_person'),
    ('Testamos esta solução para nossos clientes.', 'invented_first_person'),
    ('Observe a terra antes de regar.', 'already_explained'),
    ('São necessários 20 litros de água.', 'unsupported_new_number'),
])
def test_unsafe_redundant_or_unanchored_additions_are_rejected(pure_job, addition, code):
    result = prepared(pure_job, addition=addition)
    assert not result['changes'] and result['rejections'][0]['code'] == code


def test_full_article_later_explanation_is_not_duplicated(pure_job):
    addition = 'A observação evita rega desnecessária porque a terra úmida conserva água.'
    pure_job['article']['markdown'] += '\n\n## Motivo da rotina\n\n' + addition
    result = prepared(pure_job, addition=addition)
    assert result['rejections'][0]['code'] == 'already_explained'


def test_proposal_cannot_rewrite_original_block(pure_job):
    addition = 'Observe a terra antes de regar. Isso evita rega desnecessária.'
    # Existing original text is embedded alongside a new explanation.
    result = prepared(pure_job, addition=addition)
    assert not result['changes'] and result['rejections'][0]['code'] in (
        'already_explained', 'addition_rewrites_original_block')


@pytest.mark.parametrize('nature,addition,code', [
    ('hypothesis', 'Talvez a terra úmida conserve água por esta rotina.', 'unverified_hypothesis'),
    ('experience', 'Este método garante folhas saudáveis.', 'unattributed_personal_claim'),
    ('opinion', 'Este método sempre evita folhas secas.', 'unattributed_personal_claim'),
    ('assertion', 'Este método garante folhas saudáveis.', 'personal_source_generalized'),
    ('analogy', 'A terra é uma esponja real e absorve toda água.', 'analogy_generalized'),
])
def test_personal_claims_and_hypotheses_not_universalized(pure_job, nature, addition, code):
    foundations = core.video_foundations(pure_job)
    result = core.prepare_proposals(pure_job, diagnosis(pure_job),
        batch(pure_job, foundations, reference_id='v1s2' if nature == 'analogy' else 'v1s3',
              claim_nature=nature, addition=addition), foundations)
    assert not result['changes'] and result['rejections'][0]['code'] == code


def test_attributed_experience_remains_provisional_and_original_nature(pure_job):
    result = prepared(pure_job, reference_id='v1s3', claim_nature='experience',
                      addition='No relato da criadora, esta rotina acompanhou folhas saudáveis.')
    assert not result['rejections']
    assert result['accepted_candidates'][0]['claim_nature'] == 'experience'


def test_material_condition_is_not_dropped(pure_job):
    pure_job['sources'][0]['segments'][1]['text'] = 'Regue somente se a terra estiver seca.'
    result = prepared(pure_job, addition='Regue a terra diariamente para conservar água.')
    assert result['rejections'][0]['code'] == 'material_condition_lost'
    qualified = prepared(pure_job, addition='Regue somente se a terra estiver seca; a condição orienta a rotina.')
    assert not qualified['rejections']


@pytest.mark.parametrize('addition', ['Como explicado no vídeo, a terra conserva água.',
                                    'Ana Exemplo explica que a terra úmida conserva água.',
                                    'O apresentador considera esta observação necessária.'])
def test_external_context_is_never_attributed_to_video_creator(pure_job, addition):
    source = external_foundation()
    sources = {source['id']: source}
    result = core.prepare_proposals(pure_job, diagnosis(pure_job),
        batch(pure_job, sources, reference_id=source['id'], origin='external_verified', addition=addition), sources)
    assert result['rejections'][0]['code'] == 'external_attributed_to_creator'


def test_numbers_must_appear_in_selected_literal_support(pure_job):
    pure_job['sources'][0]['segments'][1]['text'] = 'Observe a terra por 2 dias antes de ajustar a rega.'
    foundations = core.video_foundations(pure_job)
    proposal = batch(pure_job, foundations, addition='A rotina inclui observar a terra por 2 dias.')
    accepted = core.prepare_proposals(pure_job, diagnosis(pure_job), proposal, foundations)
    assert not accepted['rejections']
    proposal['proposals'][0]['supports'] = [support(foundations['v1s2'], excerpt='antes de ajustar a rega')]
    rejected = core.prepare_proposals(pure_job, diagnosis(pure_job), proposal, foundations)
    assert rejected['rejections'][0]['code'] == 'unsupported_new_number'


def test_old_block_id_cannot_edit_new_paragraph(pure_job):
    foundations = core.video_foundations(pure_job)
    old_diagnosis, old_batch = diagnosis(pure_job), batch(pure_job, foundations)
    pure_job['article']['markdown'] = pure_job['article']['markdown'].replace('Observe a terra', 'Confira a umidade')
    result = core.prepare_proposals(pure_job, old_diagnosis, old_batch, foundations)
    assert result['rejections'][0]['code'] == 'block_missing_or_changed'


@pytest.mark.parametrize('block_index', [0, 1])
def test_header_or_wrong_opportunity_target_is_rejected(pure_job, block_index):
    foundations = core.video_foundations(pure_job)
    selected = core.article_blocks(pure_job['article'])[block_index]['id']
    result = core.prepare_proposals(pure_job, diagnosis(pure_job),
        batch(pure_job, foundations, block_id=selected), foundations)
    assert result['rejections'][0]['code'] == 'block_missing_or_changed'


def test_heading_is_not_an_enrichment_target(pure_job):
    foundations = core.video_foundations(pure_job)
    selected = core.article_blocks(pure_job['article'])[1]['id']
    result = core.prepare_proposals(pure_job, diagnosis(pure_job, block_index=1),
        batch(pure_job, foundations, block_id=selected), foundations)
    assert result['rejections'][0]['code'] == 'target_not_paragraph'


def test_repeated_identical_paragraph_cannot_receive_ambiguous_edit(pure_job):
    pure_job['article']['markdown'] += '\n\nObserve a terra antes de regar. [[v1s1]]'
    result = prepared(pure_job)
    assert result['rejections'][0]['code'] == 'edit_conflict'


def test_duplicate_proposals_to_same_block_all_rejected(pure_job):
    foundations = core.video_foundations(pure_job)
    first = batch(pure_job, foundations)['proposals'][0]
    second = {**deepcopy(first), 'opportunity_id': 'gap-2'}
    diagnosed = diagnosis(pure_job)
    diagnosed['opportunities'].append(opportunity(pure_job, identifier='gap-2'))
    result = core.prepare_proposals(pure_job, diagnosed,
        {'summary': '', 'proposals': [first, second]}, foundations)
    assert not result['changes'] and len(result['rejections']) == 2
    assert all(row['code'] == 'duplicate_or_overlapping_proposal' for row in result['rejections'])


def test_multiple_candidate_dedup_is_global(pure_job):
    foundations = core.video_foundations(pure_job)
    first = batch(pure_job, foundations)['proposals'][0]
    second = {**deepcopy(first), 'opportunity_id': 'gap-2',
              'block_id': core.article_blocks(pure_job['article'])[3]['id']}
    diagnosed = diagnosis(pure_job)
    diagnosed['opportunities'].append(opportunity(pure_job, identifier='gap-2', block_index=3))
    result = core.prepare_proposals(pure_job, diagnosed,
        {'summary': '', 'proposals': [first, second]}, foundations)
    assert len(result['changes']) == 1 and result['rejections'][0]['code'] == 'already_explained'


def test_diagnosed_already_explained_opportunity_skips_candidate(pure_job):
    foundations = core.video_foundations(pure_job)
    result = core.prepare_proposals(pure_job, diagnosis(pure_job, already_explained=True),
                                   batch(pure_job, foundations), foundations)
    assert result['rejections'][0]['code'] == 'already_explained'


def test_no_change_is_empty_without_rewrite(pure_job):
    result = core.prepare_proposals(pure_job, {'status': 'no_change', 'summary': 'Já claro.', 'opportunities': []},
                                   {'summary': '', 'proposals': []}, {})
    assert result == {'changes': [], 'accepted_candidates': [], 'rejections': []}


def test_article_index_is_exhaustive_literal_with_crlf_and_no_truncation():
    paragraphs = [f'Parágrafo {number}. ' + 'texto ' * 1600 for number in range(200)]
    article = {'markdown': '\r\n\r\n'.join(paragraphs)}
    blocks = core.article_blocks(article)
    assert len(blocks) == 200
    assert len(blocks[-1]['text']) > 8000
    for block, paragraph in zip(blocks, paragraphs):
        assert block['text'] == paragraph
        assert article['markdown'][block['start']:block['end']] == paragraph
    assert len({block['id'] for block in blocks}) == 200


def test_context_hash_pins_semantic_sources_but_not_review_and_operation(pure_job):
    before = core.context_hash(pure_job)
    pure_job.update(review={'summary': 'Mudou.'}, status='ready', editorial={'calls': 3})
    pure_job['plan'].update(valid=False, reviewed_at='today')
    assert core.context_hash(pure_job) == before
    pure_job['plan']['data']['main_question'] = 'Pergunta nova'
    assert core.context_hash(pure_job) != before
    changed_plan = core.context_hash(pure_job)
    pure_job['sources'][0]['author'] = 'Outra pessoa'
    assert core.context_hash(pure_job) != changed_plan


def test_full_context_uses_original_sources_never_historic_research(pure_job):
    pure_job['research'] = {'sources': [{'id': 'rn1', 'text': 'SEGREDO DA WEB ANTIGA', 'verified': True}]}
    pure_job['sources'].append({'id': 'internal', 'internal_context_only': True, 'segments': [
        {'id': 'internal-1', 'text': 'SEGREDO DO BASTIDOR'}]})
    before = deepcopy(pure_job)
    payload = core.diagnostic_payload(pure_job)
    assert payload['article'] == pure_job['article']
    assert payload['original_videos'][0]['segments'][-1]['text'] == pure_job['sources'][0]['segments'][-1]['text']
    assert len(payload['original_videos']) == 1
    serialized = json.dumps(payload, ensure_ascii=False)
    assert 'SEGREDO DA WEB ANTIGA' not in serialized and 'SEGREDO DO BASTIDOR' not in serialized
    assert payload['human_knowledge']['independent_source'] is False
    assert pure_job == before


def test_shadow_only_reports_explicit_signals_and_existing_gaps(pure_job):
    before = deepcopy(pure_job)
    clear = core.shadow(pure_job)
    assert clear['status'] == 'no_change' and clear['provider_calls'] == 0
    assert clear['semantic_verification'] == 'not_performed' and clear['article_modified'] is False
    assert pure_job == before
    pure_job['article']['markdown'] += '\n\nO motivo não foi esclarecido nesta fala.'
    pure_job['apuration']['videos'] = [{'id': 'v1', 'gaps': ['Dependência visual não analisada.']}]
    noticed = core.shadow(pure_job)
    assert noticed['status'] == 'opportunities'
    assert noticed['opportunities'][0]['evidence_ids'] == []
    assert noticed['existing_gaps'] == ['Dependência visual não analisada.']
    assert noticed['semantic_verification'] == 'not_performed' and not noticed['export_blocking']


@pytest.mark.parametrize('budget', ['NaN', 'Infinity', '-Infinity', True, '-0.01', 'banana'])
def test_request_rejects_nonfinite_invalid_budget(budget):
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, budget_usd=budget)


def test_request_default_shadow_and_explicit_active_budget():
    shadow = IECRequest(article_hash='a' * 64)
    assert shadow.mode == 'shadow' and shadow.budget_usd == 0 and not shadow.allow_external
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, mode='apply')
    active = IECRequest.model_validate({'article_hash': 'a' * 64, 'mode': 'suggest', 'budget_usd': '.25'})
    assert active.budget_usd == Decimal('.25')


@pytest.mark.parametrize('url', ['http://example.org', 'https://user:pass@example.org',
    'https://example.org:444/a', 'https://localhost/a', 'https://127.0.0.1/a',
    'https://10.0.0.1/a', 'https://[::1]/a', 'https://host.internal/a',
    'https://example.org/evil\nheader', 'https://example.org\\@private/a'])
def test_request_sources_must_be_public_https_syntax(url):
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, external_urls=[url])


def test_trusted_domains_normalized_explicit_without_wildcards():
    request = IECRequest(article_hash='a' * 64,
                         trusted_domains=['Docs.Example.org', 'docs.example.org.'])
    assert request.trusted_domains == ['docs.example.org']
    assert safe_https_url('https://example.org/page#section') == 'https://example.org/page'
    for value in ['*.example.org', 'example.org/path', 'https://example.org', 'localhost', '10.0.0.1']:
        with pytest.raises(ValidationError):
            IECRequest(article_hash='a' * 64, trusted_domains=[value])


def test_external_authorization_and_domain_boundary_are_checked_before_calls():
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, external_urls=['https://example.org/page'])
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, allow_external=True)
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, allow_external=True,
                   trusted_domains=['example.org'], external_urls=['https://badexample.org/page'])
    valid = IECRequest(article_hash='a' * 64, allow_external=True,
                       trusted_domains=['example.org'], external_urls=['https://docs.example.org/page'])
    assert valid.external_urls == ['https://docs.example.org/page']


@pytest.mark.parametrize('field,value', [('max_calls', 0), ('max_calls', 7), ('max_calls', True),
    ('max_output_tokens', 255), ('max_output_tokens', 8001), ('freshness_hours', 0),
    ('freshness_hours', 721), ('allow_external', 'true')])
def test_operational_bounds_are_strict(field, value):
    with pytest.raises(ValidationError):
        IECRequest(article_hash='a' * 64, **{field: value})


def test_contract_limits_and_coherent_no_change(pure_job):
    with pytest.raises(ValidationError):
        IECDiagnosis(**{**diagnosis(pure_job), 'status': 'no_change'})
    with pytest.raises(ValidationError):
        IECDiagnosis(status='opportunities', summary='', opportunities=[])
    with pytest.raises(ValidationError):
        IECDiagnosis(status='opportunities', summary='', opportunities=[opportunity(pure_job)] * 2)
    with pytest.raises(ValidationError):
        IECProposalBatch(**batch(pure_job, core.video_foundations(pure_job), addition='x' * 1801))
    with pytest.raises(ValidationError):
        IECSupport(reference_id='v1s1', excerpt='texto', offset_start=True, offset_end=5)
    with pytest.raises(ValidationError):
        IECSupport(reference_id='v1s1', excerpt='texto', offset_start=5, offset_end=5)


@pytest.mark.parametrize('criterion', ['precision', 'attribution', 'noncontradiction', 'redundancy',
                                      'cohesion', 'usefulness'])
def test_semantic_acceptance_requires_every_criterion(criterion):
    assessment = {'opportunity_id': 'gap-1', 'status': 'accept', 'reason': 'Conferido.',
                  'precision': True, 'attribution': True, 'noncontradiction': True,
                  'redundancy': True, 'cohesion': True, 'usefulness': True}
    assessment[criterion] = False
    with pytest.raises(ValidationError):
        IECValidation(summary='', assessments=[assessment])
    assessment['status'] = 'uncertain'
    assert IECValidation(summary='', assessments=[assessment]).assessments[0].status == 'uncertain'


def test_semantic_validation_does_not_allow_duplicate_approvals():
    assessment = {'opportunity_id': 'gap-1', 'status': 'accept', 'reason': 'Conferido.',
                  'precision': True, 'attribution': True, 'noncontradiction': True,
                  'redundancy': True, 'cohesion': True, 'usefulness': True}
    with pytest.raises(ValidationError):
        IECValidation(summary='', assessments=[assessment, assessment])
