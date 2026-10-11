"""Quality-repair orchestration at the provider boundary, without paid calls.

The deterministic provider below supplies explicit semantic decisions. These
tests verify source delivery, validation, conservative installation and durable
resume behavior; they do not claim to measure a real model's semantic quality.
"""
from copy import deepcopy
import re
from unittest.mock import Mock

import pytest

from app import db, generation, pipeline, spending
from app.editorial import quality_repair, store, workflow
from app.editorial.contracts import (
    DraftArticle, EditorialPlan, SpokenExtraction, VideoFidelityReview, VoiceProfile,
)


SOURCE_TEXT = (
    'Nesta cadeira, eu encosto a estrutura sobre um pano e aperto dois parafusos '
    'com uma chave manual antes de encaixar o assento. '
    'Faço meia volta em cada parafuso alternadamente para manter o assento nivelado. '
    'Prefiro a chave manual à elétrica neste ajuste para sentir a resistência. '
    'Quando o assento fica nivelado, paro: não forço a madeira.'
)
BROAD_INSIGHT = (
    'Eu aperto dois parafusos com uma chave manual antes de encaixar o assento.'
)
TURN_DETAIL = 'meia volta em cada parafuso alternadamente para manter o assento nivelado'
STOP_DETAIL = 'quando o assento fica nivelado, paro: não forço a madeira'
SUPPORTED_DETAIL = 'apoia a estrutura sobre um pano'
MISSING_DETAILS = [
    'Dar meia volta em cada parafuso alternadamente para manter o assento nivelado.',
    'Parar quando o assento fica nivelado, sem forçar a madeira.',
]
ORIGINAL_BODY = (
    'No vídeo, Marina explica o ajuste desta cadeira. [00:10]\n\n'
    '## Ajuste do assento\n\n'
    'Marina apoia a estrutura sobre um pano e aperta dois parafusos com uma '
    'chave manual antes de encaixar o assento. Ela prefere a chave manual '
    'à elétrica neste ajuste para sentir a resistência. [[v1s1]]'
)
CORRECTED_BODY = (
    ORIGINAL_BODY + '\n\n'
    'No exemplo de Marina, ela dá meia volta em cada parafuso alternadamente '
    'para manter o assento nivelado. A criadora explica: '
    '“Quando o assento fica nivelado, paro: não forço a madeira.” [[v1s1]]'
)


class FurnitureProvider:
    """Purposeful responses for the four core roles and one optional revision."""

    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []
        self.outcome = 'accepted'

    def __call__(self, current, schema, instruction, stage, extra=None):
        extra = extra or {}
        self.calls.append({
            'schema': schema, 'instruction': instruction, 'stage': stage,
            'payload': deepcopy(extra),
            'persisted_article': deepcopy(db.get_job(current['id']).get('article')),
        })
        if schema is SpokenExtraction:
            return {'summary': 'Ajuste descrito pela criadora.', 'videos': [{
                'video_id': 'v1', 'summary': 'Uma experiência com esta cadeira.',
                'gaps': [], 'insights': [{
                    'topic': 'ajuste do assento', 'spoken_explanation': BROAD_INSIGHT,
                    'kind': 'experiência', 'information_type': 'procedimento',
                    'practical_tips': [], 'analogies': [], 'warnings': [],
                    'source_segment_ids': ['v1s1'],
                }],
            }]}
        if schema is EditorialPlan:
            ids = [item['id'] for item in extra['items']]
            return {
                'main_question': 'Como Marina ajusta o assento desta cadeira?',
                'title': self.metadata['title'], 'opening': 'Apresentar Marina e a cadeira.',
                'closing': 'Encerrar a experiência da criadora.', 'ready_to_write': True,
                'sections': [{
                    'id': 's1', 'title': 'Ajuste do assento',
                    'question': 'Como fazer o ajuste descrito?',
                    'purpose': 'Desenvolver a experiência da criadora.',
                    'item_ids': ids, 'prerequisites': [], 'conditions': [],
                    'transition': 'Encerrar a explicação.', 'pending': [],
                    'presentation': {'mode': 'explanation',
                        'reason': 'A experiência cabe em parágrafos contínuos.',
                        'subheadings': []},
                }],
                'pending': [], 'research_questions': [],
                'dispositions': [{'item_id': ident, 'status': 'used',
                    'reason': 'A experiência responde à pauta.'} for ident in ids],
                'reader_journey': {'kind': 'explicativo',
                    'goal': 'Compreender o ajuste descrito por Marina.',
                    'reason': 'A experiência da fonte guia a explicação.',
                    'video_item_ids': ids},
            }
        if schema is DraftArticle:
            is_repair = bool(extra.get('_quality_repair_contract'))
            if is_repair and self.outcome == 'invalid_writer':
                return {**self.metadata, 'markdown': 'curto', 'used_item_ids': ['v1i1']}
            markdown = CORRECTED_BODY if is_repair else ORIGINAL_BODY
            if is_repair and self.outcome == 'worse':
                markdown += '\n\nMarina exige uma chave elétrica com torque de 40 Nm. [[v1s1]]'
            if is_repair and self.outcome == 'lost_detail':
                markdown = markdown.replace(SUPPORTED_DETAIL + ' e ', '')
            if is_repair and self.outcome == 'unchanged':
                markdown = ORIGINAL_BODY
            return {**self.metadata, 'markdown': markdown, 'used_item_ids': ['v1i1']}
        if schema is VideoFidelityReview:
            if extra.get('previous_article') and self.outcome == 'reviewer_failure':
                raise RuntimeError('Deterministic candidate reviewer failure')
            result = self.review(extra)
            if extra.get('previous_article') and self.outcome == 'missing_preservation':
                result['preservation'] = None
            return result
        raise AssertionError(f'Unexpected paid role: {stage}, {schema.__name__}')

    def review(self, extra):
        passages = extra['passages']
        body = [part for part in passages if part['field'] == 'markdown'
                and not re.match(r'^\s*#{1,6}\s', part['text'])]
        body_text = '\n'.join(part['text'] for part in body).lower()
        item_ids = [item['id'] for item in extra['items']]
        evidence = [{'source_id': 'v1s1', 'excerpt': SOURCE_TEXT}]
        assessments = []
        for part in passages:
            supported = part['field'] == 'markdown' and '[[v1s1]]' in part['text']
            unsupported = '40 Nm' in part['text']
            assessments.append({
                'passage_id': part['id'],
                'status': 'unsupported' if unsupported else 'supported' if supported else 'not_factual',
                'reason': 'Torque inventado.' if unsupported else 'Conferido com a fala original.',
                'evidence': evidence if supported or unsupported else [],
                'used_item_ids': item_ids if supported and not unsupported else [],
            })
        missing = [detail for phrase, detail in zip((TURN_DETAIL, STOP_DETAIL), MISSING_DETAILS)
                   if phrase not in body_text]
        if SUPPORTED_DETAIL not in body_text:
            missing.append('Apoiar a estrutura sobre um pano.')
        covered = [part['id'] for part in body if '[[v1s1]]' in part['text']]
        result = {
            'summary': 'Conferência independente dos detalhes originais.',
            'assessments': assessments,
            'editorial_alignment': {'matches_brief': True,
                'reason': 'A experiência responde à pergunta da pauta.', 'passage': ''},
            'source_assessments': [{
                'source_id': 'v1s1', 'status': 'partial' if missing else 'complete',
                'reason': 'Faltam detalhes concretos do ajuste.' if missing else
                    'Os detalhes do ajuste estão desenvolvidos no corpo.',
                'passage_ids': covered, 'missing_details': missing,
            }],
        }
        if extra.get('previous_article'):
            lost = SUPPORTED_DETAIL not in body_text
            result['preservation'] = {
                'preserves_supported_details': not lost,
                'regressions': [{'source_ids': ['v1s1'],
                    'lost_details': ['Apoiar a estrutura sobre um pano.'],
                    'reason': 'O detalhe correto do rascunho foi removido.'}] if lost else [],
            }
        return result


@pytest.fixture
def furniture(job, monkeypatch):
    """Use the actual temporary SQLite fixture; patch only model generation."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key-never-sent-to-a-provider')
    source = job['sources'][0]
    source.update(title='Ajuste de uma cadeira', author='Marina')
    source['segments'] = [{'id': 'v1s1', 'text': SOURCE_TEXT, 'start': 10, 'end': 45}]
    metadata = {
        'title': 'Como Marina ajusta o assento da cadeira',
        'seo_title': 'Ajuste do assento da cadeira com Marina',
        'slug': 'ajuste-assento-cadeira',
        'meta_description': 'A experiência de Marina ao ajustar o assento de uma cadeira.',
        'excerpt': 'Marina descreve o ajuste do assento desta cadeira.',
        'tags': ['cadeira'],
    }
    job['brief'].update(topic='Ajuste do assento da cadeira', keyword='cadeira',
        audience='Quem acompanha a experiência de Marina', research=False,
        target_words=800)
    job['article'] = {**metadata, 'markdown': ORIGINAL_BODY}
    job['review'] = None
    db.set_setting('editorial_profile', VoiceProfile(max_calls=24).model_dump())
    db.save_job(job)
    provider = FurnitureProvider(metadata)
    boundary = Mock(side_effect=provider)
    monkeypatch.setattr(generation, 'structured', boundary)
    return job, provider, boundary


def run_furniture(furniture):
    job, provider, boundary = furniture
    pipeline.run(job['id'])
    return db.get_job(job['id']), provider, boundary


def assert_original_preserved(saved):
    assert saved['article']['markdown'] == ORIGINAL_BODY
    assert saved['review']['article_hash'] == generation.article_hash(saved['article'])
    assert saved['review']['source_coverage']['assessments'][0]['status'] == 'partial'
    assert saved['review']['source_coverage']['assessments'][0]['missing_details'] == MISSING_DETAILS
    assert saved['editorial']['reviewed_hash'] == saved['review']['article_hash']


def assert_resumes_without_provider_calls(saved, boundary):
    calls = boundary.call_count
    ledger = saved['editorial']['calls']
    cycle = saved['editorial']['cycle_id']
    article = deepcopy(saved['article'])
    repair = deepcopy(saved['editorial'].get('quality_repair'))
    for _ in range(2):
        pipeline.run(saved['id'], 'resume')
        resumed = db.get_job(saved['id'])
        assert boundary.call_count == calls
        assert resumed['editorial']['calls'] == ledger
        assert resumed['editorial']['cycle_id'] == cycle
        assert resumed['article'] == article
        assert resumed['editorial'].get('quality_repair') == repair


def test_full_coordinator_recovers_missing_detail_inside_one_used_insight(furniture):
    saved, provider, boundary = run_furniture(furniture)
    report = saved['editorial']['quality_repair']
    assert report['status'] == 'accepted', report
    assert saved['status'] == 'ready', saved.get('error')
    assert saved['article']['markdown'] == CORRECTED_BODY
    assert saved['editorial']['calls'] == boundary.call_count == 6
    assert [call['stage'] for call in provider.calls] == [
        'extractor', 'planner', 'writer', 'fact_reviewer', 'writer', 'fact_reviewer',
    ]
    assert len(store.report(saved)['runs']) == 6
    assert len(saved['apuration']['items']) == 1
    assert saved['apuration']['items'][0]['id'] == 'v1i1'
    assert TURN_DETAIL not in saved['apuration']['items'][0]['statement'].lower()
    assert STOP_DETAIL not in saved['apuration']['items'][0]['statement'].lower()

    initial_review = provider.calls[3]['payload']
    repair_writer = provider.calls[4]['payload']
    candidate_review = provider.calls[5]['payload']
    assert initial_review['source_inventory'] == ['v1s1']
    assert initial_review['_context_sources']['v1s1']['text'] == SOURCE_TEXT
    assert all('check' not in item for item in initial_review['items'])
    source_findings = [finding for finding in repair_writer['repair']['findings']
                       if finding['origin'] == 'source_coverage']
    assert len(source_findings) == 1
    assert source_findings[0]['assessment_status'] == 'partial'
    assert source_findings[0]['missing_details'] == MISSING_DETAILS
    assert not any(finding['origin'] == 'coverage'
                   for finding in repair_writer['repair']['findings'])
    assert repair_writer['_context_sources']['v1s1']['text'] == SOURCE_TEXT
    assert repair_writer['_no_recovery'] is True
    assert repair_writer['_budget_reserve'] == 1
    assert repair_writer['_downstream_usd'] > 0
    assert candidate_review['_no_recovery'] is True
    assert candidate_review['previous_article']['markdown'] == ORIGINAL_BODY
    assert candidate_review['passages'] == workflow.passages(saved['article'])
    assert candidate_review['_context_sources']['v1s1']['text'] == SOURCE_TEXT
    assert 'preservation' in provider.calls[5]['instruction']
    assert all(call['persisted_article']['markdown'] == ORIGINAL_BODY
               for call in provider.calls[4:])

    before_artifact, = store.artifacts(saved['id'], 'quality_repair_before')
    before = before_artifact['data']
    assert before['article']['markdown'] == ORIGINAL_BODY
    assert before['review']['coverage'][0]['status'] == 'used'
    assert before['review']['source_coverage']['assessments'][0]['status'] == 'partial'
    assert before['review']['source_coverage']['assessments'][0]['missing_details'] == MISSING_DETAILS
    assert any(revision['data'] == before['article'] for revision in db.revisions(saved['id']))
    archived = [artifact['data'] for artifact in store.artifacts(saved['id'], 'review_history')
                if artifact['data'].get('article_hash') == report['before_hash']]
    assert any(review['source_coverage']['assessments'][0]['missing_details'] == MISSING_DETAILS
               for review in archived)
    candidate, = store.artifacts(saved['id'], 'quality_repair_candidate')
    audit, = store.artifacts(saved['id'], 'quality_repair_review')
    assert candidate['data'] == saved['article']
    assert audit['data']['article_hash'] == report['after_hash']
    assert report['after_hash'] == generation.article_hash(saved['article'])
    assert report['before_hash'] == generation.article_hash(before['article'])
    assert audit['data']['preservation'] == {'preserves_supported_details': True, 'regressions': []}
    source_assessment = saved['review']['source_coverage']['assessments'][0]
    assert source_assessment['status'] == 'complete'
    assert source_assessment['missing_details'] == []
    passages = {part['id']: part for part in workflow.passages(saved['article'])}
    developed = [passages[ident] for ident in source_assessment['passage_ids']]
    assert all(part['field'] == 'markdown' and not part['text'].startswith('#') for part in developed)
    developed_text = '\n'.join(part['text'] for part in developed).lower()
    assert TURN_DETAIL in developed_text and STOP_DETAIL in developed_text
    assert SUPPORTED_DETAIL in developed_text
    assert_resumes_without_provider_calls(saved, boundary)


@pytest.mark.parametrize('outcome, status, calls', [
    ('invalid_writer', 'failed', 5),
    ('reviewer_failure', 'failed', 6),
    ('missing_preservation', 'failed', 6),
    ('worse', 'rejected', 6),
    ('lost_detail', 'rejected', 6),
    ('unchanged', 'rejected', 5),
])
def test_optional_failure_never_installs_candidate_or_retries(furniture, outcome, status, calls):
    furniture[1].outcome = outcome
    saved, provider, boundary = run_furniture(furniture)
    report = saved['editorial']['quality_repair']
    assert report['status'] == status, report
    assert_original_preserved(saved)
    assert saved['editorial']['calls'] == boundary.call_count == calls
    repair_calls = [call for call in provider.calls if call['payload'].get('_quality_repair_contract')]
    assert len(repair_calls) == 1
    assert repair_calls[0]['payload']['_no_recovery'] is True
    assert not saved['editorial'].get('response_recoveries')
    assert len(store.report(saved)['runs']) == calls
    if outcome == 'lost_detail':
        audit, = store.artifacts(saved['id'], 'quality_repair_review')
        assert audit['data']['preservation']['preserves_supported_details'] is False
        assert audit['data']['preservation']['regressions'][0]['lost_details'] == [
            'Apoiar a estrutura sobre um pano.']
        assert audit['data']['source_coverage']['assessments'][0]['status'] == 'partial'
        assert report['after_score'][3] < report['before_score'][3]
    if outcome == 'invalid_writer':
        assert not store.artifacts(saved['id'], 'quality_repair_candidate')
        assert not store.artifacts(saved['id'], 'quality_repair_review')
    if outcome in ('reviewer_failure', 'missing_preservation'):
        assert store.artifacts(saved['id'], 'quality_repair_candidate')
        assert not store.artifacts(saved['id'], 'quality_repair_review')
    assert_resumes_without_provider_calls(saved, boundary)


def test_four_call_budget_leaves_the_original_omission_explicit(furniture):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=4).model_dump())
    saved, _, boundary = run_furniture(furniture)
    assert saved['editorial']['quality_repair']['status'] == 'deferred'
    assert saved['editorial']['quality_repair']['reason'] == 'call_budget'
    assert saved['editorial']['calls'] == boundary.call_count == 4
    assert_original_preserved(saved)
    assert not store.artifacts(saved['id'], 'quality_repair_candidate')
    assert_resumes_without_provider_calls(saved, boundary)


def test_financial_preflight_prevents_optional_writer_and_reviewer(furniture):
    job, _, _ = furniture
    # Model responses are offline; the actual optional-repair financial bound
    # must still prevent either optional request at this small remaining limit.
    job['financial_budget_usd'] = 0.000001
    db.save_job(job)
    saved, _, boundary = run_furniture(furniture)
    report = saved['editorial']['quality_repair']
    assert report['status'] == 'deferred', report
    assert report['reason'] == 'financial_budget'
    assert report['reserved_review_estimate_usd'] > spending.summary(saved)['remaining_usd']
    assert saved['editorial']['calls'] == boundary.call_count == 4
    assert_original_preserved(saved)
    assert not store.artifacts(saved['id'], 'quality_repair_candidate')
    assert_resumes_without_provider_calls(saved, boundary)


def test_explicit_review_only_never_rewrites_a_partial_source(furniture):
    db.set_setting('editorial_profile', VoiceProfile(max_calls=4).model_dump())
    saved, provider, boundary = run_furniture(furniture)
    original = deepcopy(saved['article'])
    revisions = db.revisions(saved['id'])
    previous_calls = len(provider.calls)
    boundary.reset_mock()
    pipeline.run(saved['id'], 'review')
    reviewed = db.get_job(saved['id'])
    assert reviewed['article'] == original
    assert reviewed['editorial']['mode'] == 'review'
    assert reviewed['editorial']['calls'] == boundary.call_count == 1
    assert 'quality_repair' not in reviewed['editorial']
    assert 'quality_repair_version' not in reviewed['editorial']
    assert [call['stage'] for call in provider.calls[previous_calls:]] == ['fact_reviewer']
    assert db.revisions(saved['id']) == revisions
    assert reviewed['review']['source_coverage']['assessments'][0]['status'] == 'partial'


def reviews_for_acceptance():
    """A score improvement can conceal an identity/detail regression."""
    def review(statuses, missing):
        return {
            'source_coverage': {'assessments': [
                {'source_id': ident, 'status': status,
                 'missing_details': missing.get(ident, [])}
                for ident, status in statuses.items()]},
            'coverage': [{'item_id': 'v1i1', 'planned_status': 'used', 'status': 'used'},
                         {'item_id': 'v1i2', 'planned_status': 'used', 'status': 'pending'}],
            'findings': [], 'editorial_alignment': {'matches_brief': True},
            'preservation': {'preserves_supported_details': True, 'regressions': []},
        }
    before = review({'v1s1': 'partial', 'v1s2': 'partial'},
                    {'v1s1': ['detail A', 'detail B'], 'v1s2': ['detail C']})
    after = review({'v1s1': 'complete', 'v1s2': 'partial'}, {'v1s2': ['detail C']})
    return before, after


@pytest.mark.parametrize('regression', [
    'partial_to_missing', 'partial_to_excluded', 'swapped_used_item',
    'new_source_uncertainty', 'new_claim_uncertainty', 'lost_supported_detail',
])
def test_acceptance_rejects_regressions_even_when_aggregate_omissions_improve(furniture, regression):
    job, _, _ = furniture
    before, after = reviews_for_acceptance()
    if regression in ('partial_to_missing', 'partial_to_excluded', 'new_source_uncertainty'):
        after['source_coverage']['assessments'][1]['status'] = {
            'partial_to_missing': 'missing', 'partial_to_excluded': 'excluded',
            'new_source_uncertainty': 'uncertain',
        }[regression]
    elif regression == 'swapped_used_item':
        after['coverage'][0]['status'] = 'pending'
        after['coverage'][1]['status'] = 'used'
    elif regression == 'new_claim_uncertainty':
        before['findings'] = [
            {'origin': 'semantic_review', 'assessment_status': 'uncertain',
             'severity': 'blocking', 'passage': passage, 'source_ids': ['v1s2'],
             'reason': 'A fala depende de uma imagem indisponível.'}
            for passage in ('Uma ressalva anterior.', 'Outra ressalva anterior.')]
        after['findings'].append({'origin': 'semantic_review', 'assessment_status': 'uncertain',
            'severity': 'blocking', 'passage': 'Uma alegação nova.',
            'source_ids': ['v1s1'], 'reason': 'Não é verificável na fala.'})
    else:
        after['preservation'] = {'preserves_supported_details': False, 'regressions': [{
            'source_ids': ['v1s2'], 'lost_details': ['A correct detail within a partial source'],
            'reason': 'The old supported detail was lost although fewer omissions remain.'}]}
    candidate = {**job['article'], 'markdown': CORRECTED_BODY}
    accepted, old_score, new_score = quality_repair._acceptable(job, before, candidate, after)
    assert new_score[2:4] < old_score[2:4]
    assert all(new <= old for old, new in zip(old_score, new_score))
    assert accepted is False


def test_acceptance_allows_verified_detail_gain_when_existing_partial_details_survive(furniture):
    job, _, _ = furniture
    before, after = reviews_for_acceptance()
    candidate = {**job['article'], 'markdown': CORRECTED_BODY}
    accepted, old_score, new_score = quality_repair._acceptable(job, before, candidate, after)
    assert new_score[2:4] < old_score[2:4]
    assert accepted is True


def test_acceptance_rejects_a_new_uncertain_claim_from_the_same_source(furniture):
    job, _, _ = furniture
    before, after = reviews_for_acceptance()
    previous_passages = (
        'Marina aponta um pequeno risco no assento. [[v1s1]]',
        'Marina compara o apoio com a peça que aparece na imagem. [[v1s1]]',
    )
    new_passage = 'Marina posiciona o assento na marca vermelha mostrada na imagem. [[v1s1]]'
    job['article']['markdown'] = ORIGINAL_BODY + '\n\n' + '\n\n'.join(previous_passages)
    candidate = {**job['article'], 'markdown': CORRECTED_BODY + '\n\n' + new_passage}
    before['source_coverage']['assessments'] = [{
        'source_id': 'v1s1', 'status': 'partial', 'missing_details': MISSING_DETAILS,
    }]
    after['source_coverage']['assessments'] = [{
        'source_id': 'v1s1', 'status': 'complete', 'missing_details': [],
    }]
    before['findings'] = [{
        'origin': 'semantic_review', 'assessment_status': 'uncertain',
        'severity': 'blocking', 'passage': passage, 'source_ids': ['v1s1'],
        'reason': 'A alegação depende de uma imagem indisponível.',
    } for passage in previous_passages]
    after['findings'] = [{
        'origin': 'semantic_review', 'assessment_status': 'uncertain',
        'severity': 'blocking', 'passage': new_passage, 'source_ids': ['v1s1'],
        'reason': 'A alegação depende de uma imagem indisponível.',
    }]
    after['coverage'][1]['status'] = 'used'
    assert after['preservation'] == {'preserves_supported_details': True, 'regressions': []}
    accepted, old_score, new_score = quality_repair._acceptable(job, before, candidate, after)
    assert old_score[1] == 2 and new_score[1] == 1
    assert old_score[2:5] == (1, 2, 1) and new_score[2:5] == (0, 0, 0)
    assert all(new <= old for old, new in zip(old_score, new_score))
    assert accepted is False


@pytest.mark.parametrize('changed_dependency', ['article', 'sources', 'plan', 'cycle'])
def test_stale_optional_failure_report_does_not_overwrite_a_concurrent_edit(furniture, changed_dependency):
    from app.editorial import engine

    job, _, boundary = furniture
    engine.start(job, 'generate')
    job['plan'] = {'version': 'original-plan'}
    db.save_job(job)
    stale = deepcopy(job)
    edited = deepcopy(job)
    if changed_dependency == 'article':
        edited['article']['markdown'] += '\n\nTexto salvo pelo usuário.'
    elif changed_dependency == 'sources':
        edited['sources'][0]['segments'][0]['text'] += ' Uma fala corrigida.'
    elif changed_dependency == 'plan':
        edited['plan']['version'] = 'edited-plan'
    else:
        edited['editorial']['cycle_id'] = 'a-new-generation-cycle'
    db.save_job(edited)
    report = {'before_hash': generation.article_hash(stale['article']),
              'status': 'failed', 'reason': 'RuntimeError', 'version': 1}
    result = quality_repair._save(stale, report)
    assert result['status'] == 'deferred'
    assert result['reason'] == 'dependencies_changed'
    current = db.get_job(job['id'])
    assert current['article'] == edited['article']
    assert current['sources'] == edited['sources']
    assert current['plan'] == edited['plan']
    assert current['editorial']['cycle_id'] == edited['editorial']['cycle_id']
    assert 'quality_repair' not in current['editorial']
    assert stale['article'] == edited['article']
    assert stale['sources'] == edited['sources']
    assert store.artifacts(job['id'], 'quality_repair')[0]['data'] == report
    boundary.assert_not_called()
