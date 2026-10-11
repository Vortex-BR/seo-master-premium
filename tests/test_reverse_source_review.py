"""Offline reverse audits using synthetic cooking and furniture transcripts."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app import db, generation
from app.editorial import reference_contracts, store, video_first, workflow
from app.editorial.article_structure import body_development_passages, is_body_development
from app.editorial.contracts import VideoFidelityReview


@pytest.fixture
def cooking_job():
    return {'id': 'synthetic-reverse-review', 'brief': {'topic': 'Preparar arroz'},
            'article': {'title': 'Preparo do arroz', 'seo_title': 'Preparo do arroz',
                        'slug': 'preparo-arroz', 'meta_description': 'Uma experiência com arroz.',
                        'excerpt': 'O preparo explicado pela criadora.', 'tags': ['arroz'],
                        'markdown': '## Preparo\n\nLavo o arroz antes de levar à panela. [[v1s1]]'},
            'sources': [{'id': 'v1', 'url': 'https://example.test/cooking',
                         'title': 'Arroz na panela', 'author': 'Criadora de exemplo',
                         'segments': [{'id': 'v1s1', 'text': 'Lavo o arroz antes de levar à panela.'}]}],
            'apuration': {'items': [], 'videos': [{'id': 'v1', 'item_ids': [], 'gaps': []}]}}


@pytest.fixture
def offline_review(monkeypatch):
    calls, saved = [], []
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('Provider calls are forbidden'))
    monkeypatch.setattr(db, 'save_job', lambda job: saved.append(deepcopy(job)))
    monkeypatch.setattr(store, 'issues', lambda job: [])
    monkeypatch.setattr(generation, 'deterministic_findings', lambda job: [])

    def run(job, mutate=None, **options):
        def deliver(current, role, schema, instruction, payload, slot, validate):
            calls.append({'schema': schema, 'payload': deepcopy(payload), 'instruction': instruction})
            body = {part['id']: part for part in payload['passages']
                    if part['id'] in payload['_body_development_ids']}
            evidence = [{'source_id': ident, 'excerpt': source['text']}
                        for ident, source in payload['_context_sources'].items()]
            output = {'summary': 'Conferência independente.',
                      'editorial_alignment': {'matches_brief': True, 'reason': 'Responde à pauta.', 'passage': ''},
                      'assessments': [{'passage_id': part['id'],
                                      'status': 'supported' if part['id'] in body else 'not_factual',
                                      'reason': 'Comparado à fala original.',
                                      'evidence': deepcopy(evidence) if part['id'] in body else [],
                                      'used_item_ids': []} for part in payload['passages']],
                      'source_assessments': [{'source_id': ident, 'status': 'complete',
                                              'reason': 'Os detalhes estão desenvolvidos no corpo.',
                                              'passage_ids': list(body), 'missing_details': []}
                                             for ident in payload['source_inventory']],
                      'preservation': {'preserves_supported_details': True, 'regressions': []}
                                      if 'previous_article' in payload else None}
            if mutate:
                mutate(output, payload)
            result = schema.model_validate(output).model_dump()
            validate(result)
            return result
        monkeypatch.setattr(workflow, 'call', deliver)
        return video_first.factual_review(job, **options)

    return run, calls, saved


@pytest.mark.parametrize('text', [
    '# Preparo', '  ## Preparo [[v1s1]]', 'Preparo\n=======', 'Preparo\n-------',
    '[[v1s1]]', '[00:10] [[v1s1]]', '![Panela](panela.png)',
    '```text\nLavo o arroz. [[v1s1]]\n```', '---', '| Etapa | Ação |\n| --- | --- |',
])
def test_headings_citations_and_code_cannot_count_as_body_development(text):
    assert not is_body_development({'id': 'p1', 'field': 'markdown', 'text': text})


@pytest.mark.parametrize('text', [
    '## Preparo\nLavo o arroz antes de levar à panela.',
    'Preparo\n-------\nLavo o arroz antes de levar à panela.',
    '- Lavo o arroz antes de levar à panela.',
    '> Lavo o arroz antes de levar à panela.',
    '| Etapa | Ação |\n| --- | --- |\n| Antes | Lavar o arroz |',
    '#Arroz é o nome usado nesta explicação.',
])
def test_markdown_with_real_body_content_can_be_a_development_candidate(text):
    assert is_body_development({'id': 'p1', 'field': 'markdown', 'text': text})
    assert not is_body_development({'id': 'p1', 'field': 'title', 'text': text})


def test_scoped_source_references_use_the_same_markdown_body_gate():
    passages = [{'id': 'metadata', 'field': 'title', 'text': 'Preparo do arroz'},
                {'id': 'atx', 'field': 'markdown', 'text': '## Preparo'},
                {'id': 'setext', 'field': 'markdown', 'text': 'Preparo\n-------'},
                {'id': 'mixed', 'field': 'markdown', 'text': '## Preparo\nLavo o arroz.'}]
    payload = {'passages': passages, 'source_inventory': ['v1s1'], 'items': []}
    schema = reference_contracts.scope(VideoFidelityReview, VideoFidelityReview, payload)
    valid = {'summary': 'Conferido.', 'assessments': [],
             'editorial_alignment': {'matches_brief': True, 'reason': 'Atende à pauta.', 'passage': ''},
             'source_assessments': [{'source_id': 'v1s1', 'status': 'complete', 'reason': 'Desenvolvido.',
                                     'passage_ids': ['mixed'], 'missing_details': []}],
             'preservation': None}
    assert schema.model_validate(valid)
    for ident in ('metadata', 'atx', 'setext'):
        bad = deepcopy(valid)
        bad['source_assessments'][0]['passage_ids'] = [ident]
        with pytest.raises(ValidationError):
            schema.model_validate(bad)


@pytest.mark.parametrize('markdown', [
    '```text\n\nLavo o arroz antes de cozinhar. [[v1s1]]\n\n```',
    '```text\n' + 'Lavo o arroz antes de cozinhar. ' * 250 + '[[v1s1]]\n```',
    '## ' + 'Preparo do arroz ' * 420,
    'Preparo do arroz ' * 420 + '\n----------------',
])
def test_full_markdown_context_excludes_detached_code_and_long_heading_fragments(cooking_job, markdown):
    cooking_job['article']['markdown'] = markdown
    payload, _, _ = video_first.build_review_payload(cooking_job, cooking_job['article'])
    detached = body_development_passages(payload['passages'])
    assert detached  # At least one fragment looks like prose after partitioning.
    assert body_development_passages(payload['passages'], markdown=markdown) == {}
    assert payload['_body_development_ids'] == []
    schema = reference_contracts.scope(VideoFidelityReview, VideoFidelityReview, payload)
    output = {'summary': 'Conferido.', 'assessments': [],
              'editorial_alignment': {'matches_brief': True, 'reason': 'Atende à pauta.', 'passage': ''},
              'source_assessments': [{'source_id': 'v1s1', 'status': 'complete', 'reason': 'Desenvolvido.',
                                      'passage_ids': [next(iter(detached))], 'missing_details': []}],
              'preservation': None}
    with pytest.raises(ValidationError):
        schema.model_validate(output)


@pytest.mark.parametrize('markdown', [
    '## Preparo\nLavo o arroz antes de levar à panela. [[v1s1]]',
    'Preparo\n-------\nLavo o arroz antes de levar à panela. [[v1s1]]',
    '## Preparo\n\n- Lavo o arroz antes de levar à panela. [[v1s1]]',
    '## Preparo\n\n| Etapa | Ação |\n| --- | --- |\n| Antes | Lavar o arroz. [[v1s1]] |',
    'Lavo o arroz antes de levar à panela. ' * 200 + '[[v1s1]]',
])
def test_full_markdown_context_keeps_real_mixed_list_table_and_long_prose_development(cooking_job, markdown):
    cooking_job['article']['markdown'] = markdown
    payload, _, _ = video_first.build_review_payload(cooking_job, cooking_job['article'])
    body = body_development_passages(payload['passages'], markdown=markdown)
    assert body and list(body) == payload['_body_development_ids']
    assert 'markdown' not in payload  # Only IDs accompany the existing passages.


@pytest.mark.parametrize('code_first', [True, False])
def test_progressive_passage_mapping_distinguishes_identical_code_and_prose(cooking_job, code_first):
    text = 'Lavo o arroz antes de levar à panela. [[v1s1]]'
    code = '```text\n\n' + text + '\n\n```'
    markdown = code + '\n\n' + text if code_first else text + '\n\n' + code
    cooking_job['article']['markdown'] = markdown
    payload, _, _ = video_first.build_review_payload(cooking_job, cooking_job['article'])
    copies = [part['id'] for part in payload['passages'] if part['text'] == text]
    assert len(copies) == 2
    assert payload['_body_development_ids'] == [copies[-1] if code_first else copies[0]]


def test_full_markdown_mapping_rejects_passages_that_do_not_match_the_original():
    with pytest.raises(ValueError, match='ordem do Markdown original'):
        body_development_passages([{'id': 'p1', 'field': 'markdown', 'text': 'Outra explicação.'}],
                                  markdown='Lavo o arroz.')


def test_detached_fenced_paragraph_is_rejected_by_the_real_review_validator(cooking_job, offline_review):
    run, calls, saved = offline_review
    cooking_job['article']['markdown'] = '```text\n\nLavo o arroz. [[v1s1]]\n\n```'
    def cite_detached_code(output, payload):
        detached = next(part['id'] for part in payload['passages'] if part['text'].startswith('Lavo'))
        output['source_assessments'][0]['passage_ids'] = [detached]
    with pytest.raises(generation.GenerationResponseError) as rejected:
        run(cooking_job, cite_detached_code)
    assert rejected.value.reason == 'unknown_reference'
    assert calls[0]['payload']['_body_development_ids'] == [] and saved == []


@pytest.mark.parametrize('heading', ['## Preparo', 'Preparo\n-------'])
def test_heading_only_source_links_are_rejected_locally(cooking_job, offline_review, heading):
    run, calls, saved = offline_review
    cooking_job['article']['markdown'] = heading
    def heading_link(output, payload):
        output['source_assessments'][0]['passage_ids'] = [payload['passages'][-1]['id']]
    with pytest.raises(generation.GenerationResponseError) as rejected:
        run(cooking_job, heading_link)
    assert rejected.value.reason == 'unknown_reference'
    assert len(calls) == 1 and saved == []


def test_heading_and_paragraph_in_one_passage_can_support_complete_source(cooking_job, offline_review):
    run, _, _ = offline_review
    cooking_job['article']['markdown'] = '## Preparo\nLavo o arroz antes de levar à panela. [[v1s1]]'
    review = run(cooking_job)
    assessment, = review['source_coverage']['assessments']
    assert assessment['status'] == 'complete'
    assert len(assessment['passage_ids']) == 1


@pytest.mark.parametrize('status', ['unsupported', 'uncertain', 'not_factual'])
def test_complete_source_claim_cannot_override_unconfirmed_body_verdict(cooking_job, offline_review, status):
    run, calls, _ = offline_review
    def reject_body(output, payload):
        for assessment in output['assessments']:
            if assessment['status'] == 'supported':
                assessment['status'] = status
    review = run(cooking_job, reject_body)
    source, = review['source_coverage']['assessments']
    assert source['status'] == 'uncertain' and source['claimed_status'] == 'complete'
    assert source['coherence']['origin'] == 'coordinator'
    assert source['coherence']['code'] == 'source_passage_verdict_mismatch'
    assert source['missing_details'] == []
    assert len(calls) == 1
    assert any(f['origin'] == 'source_coverage' for f in review['findings'])
    if status in ('unsupported', 'uncertain'):
        assert any(f['origin'] == 'semantic_review' for f in review['findings'])


def test_supported_passage_with_other_source_evidence_does_not_verify_coverage(cooking_job, offline_review):
    run, calls, _ = offline_review
    cooking_job['sources'][0]['segments'].append(
        {'id': 'v1s2', 'text': 'Uso uma xícara de arroz e duas de água.'})
    def unrelated_evidence(output, payload):
        for assessment in output['assessments']:
            assessment['evidence'] = [e for e in assessment['evidence'] if e['source_id'] == 'v1s2']
    review = run(cooking_job, unrelated_evidence)
    first, second = review['source_coverage']['assessments']
    assert first['status'] == 'uncertain' and first['claimed_status'] == 'complete'
    assert second['status'] == 'complete'
    assert len(calls) == 1


def test_one_supported_link_does_not_hide_a_contradictory_link(cooking_job, offline_review):
    run, _, _ = offline_review
    cooking_job['article']['markdown'] += '\n\nToda panela cozinha arroz em cinco minutos.'
    def reject_last_body(output, payload):
        output['assessments'][-1]['status'] = 'unsupported'
    review = run(cooking_job, reject_last_body)
    source, = review['source_coverage']['assessments']
    assert source['status'] == 'uncertain'
    assert len(source['coherence']['passage_ids']) == 1


def test_literal_evidence_still_needs_to_match_the_original_source(cooking_job, offline_review):
    run, _, saved = offline_review
    def invented_evidence(output, payload):
        output['assessments'][-1]['evidence'][0]['excerpt'] = 'Cozinho por cinco minutos.'
    with pytest.raises(generation.GenerationResponseError) as rejected:
        run(cooking_job, invented_evidence)
    assert rejected.value.reason == 'evidence_mismatch' and saved == []


def test_full_original_inventory_and_zero_insight_gaps_reach_the_same_review(cooking_job, offline_review):
    run, calls, _ = offline_review
    gap = 'A ordem do encaixe aparece somente na imagem não analisada.'
    cooking_job['sources'].append({'id': 'v2', 'url': 'https://example.test/furniture',
        'title': 'Montar cadeira', 'author': 'Criador de exemplo',
        'segments': [{'id': 'v2s1', 'text': 'Veja como encaixo esta peça.'}]})
    cooking_job['apuration']['videos'].append({'id': 'v2', 'item_ids': [], 'gaps': [gap]})
    def visual_uncertainty(output, payload):
        output['source_assessments'][1].update(status='uncertain', passage_ids=[],
            reason=gap, missing_details=[])
    review = run(cooking_job, visual_uncertainty)
    payload = calls[0]['payload']
    assert payload['source_inventory'] == ['v1s1', 'v2s1']
    assert payload['items'] == []
    assert payload['video_gaps'][1] == {'video_id': 'v2', 'gaps': [gap]}
    assert payload['_context_sources']['v2s1']['text'] == 'Veja como encaixo esta peça.'
    assert review['source_coverage']['basis'] == 'available_transcript'
    assert review['source_coverage']['full_video_completeness'] == 'unverified'
    assert review['source_coverage']['visual_evidence'] == 'not_reviewed'
    assert review['source_coverage']['video_gaps'] == payload['video_gaps']


def test_all_complete_sources_only_establish_available_transcript_review(cooking_job, offline_review):
    run, _, _ = offline_review
    review = run(cooking_job)
    assert all(row['status'] == 'complete' for row in review['source_coverage']['assessments'])
    assert review['source_coverage']['full_video_completeness'] == 'unverified'


def test_supported_cited_body_can_still_omit_details_from_the_same_source(cooking_job, offline_review):
    run, calls, _ = offline_review
    cooking_job['sources'][0]['segments'][0]['text'] = (
        'Lavo o arroz antes de levar à panela, para tirar o excesso de amido. '
        'Uso uma xícara de arroz e duas xícaras de água nesta panela com a tampa fechada.')
    absent = ['O motivo: tirar o excesso de amido.',
              'Uma xícara de arroz e duas xícaras de água.',
              'A condição: nesta panela com a tampa fechada.']
    def detail_omission(output, payload):
        output['source_assessments'][0].update(status='partial',
            reason='A ação foi desenvolvida, mas seu motivo, medidas e condição ficaram ausentes.',
            missing_details=absent)
    review = run(cooking_job, detail_omission)
    assert review['semantic_coverage']['assessments'][-1]['status'] == 'supported'
    source, = review['source_coverage']['assessments']
    assert source['status'] == 'partial' and source['missing_details'] == absent
    omission, = [finding for finding in review['findings'] if finding['origin'] == 'source_coverage']
    assert omission['source_ids'] == ['v1s1'] and omission['missing_details'] == absent
    assert len(calls) == 1


def test_preflight_payload_and_instruction_match_real_preservation_review(cooking_job, offline_review):
    run, calls, _ = offline_review
    original = deepcopy(cooking_job['article'])
    payload, _, _ = video_first.build_review_payload(cooking_job, cooking_job['article'],
        previous_article=original, no_recovery=True)
    review = run(cooking_job, round_index=1, article=cooking_job['article'], previous_article=original)
    assert calls[0]['payload'] == payload
    assert calls[0]['instruction'] == video_first.review_instruction(original)
    assert video_first.PRESERVATION_REVIEW_POLICY in calls[0]['instruction']
    assert payload['_no_recovery'] is True and payload['previous_article'] == original
    assert payload['previous_article'] is not original
    assert review['preservation']['preserves_supported_details'] is True
    initial, _, _ = video_first.build_review_payload(cooking_job, cooking_job['article'])
    assert 'previous_article' not in initial and '_no_recovery' not in initial
    assert video_first.PRESERVATION_REVIEW_POLICY not in video_first.review_instruction()


def test_missing_source_delivery_is_rejected_even_with_fully_supported_body(cooking_job, offline_review):
    run, _, saved = offline_review
    def omit_source(output, payload):
        output['source_assessments'] = []
    with pytest.raises(generation.GenerationResponseError) as rejected:
        run(cooking_job, omit_source)
    assert rejected.value.reason == 'coverage_mismatch' and saved == []


@pytest.mark.parametrize('changes', [{'passage_ids': []}, {'missing_details': ['A ordem do preparo.']}])
def test_complete_source_needs_development_without_declared_omissions(cooking_job, offline_review, changes):
    run, _, saved = offline_review
    def malformed_complete(output, payload):
        output['source_assessments'][0].update(changes)
    with pytest.raises(ValueError, match='Cobertura completa'):
        run(cooking_job, malformed_complete)
    assert saved == []
