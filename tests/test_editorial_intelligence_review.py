"""Later factual review reads complementary originals without fake video IDs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app import db, generation
from app.editorial import engine, intelligence as core, intelligence_review as review, store, workflow


@pytest.fixture
def enriched(job, monkeypatch):
    monkeypatch.setattr(generation, 'client', Mock(side_effect=AssertionError('No live provider.')))
    engine.start(job, 'review')
    source = {'id': 'iecsrc-fixture', 'origin': 'external_verified',
              'text': 'Os furos permitem que o excesso de água saia do recipiente.',
              'url': 'https://example.org/recipiente', 'publisher': 'example.org',
              'fetched_at': datetime.now(timezone.utc).isoformat(),
              'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
              'content_sha256': 'a' * 64, 'limitations': ['Fonte complementar ao vídeo.']}
    source['record_hash'] = core.foundation_hash(source)
    support = {'reference_id': source['id'], 'excerpt': source['text'],
               'offset_start': 0, 'offset_end': len(source['text'])}
    added = source['text'] + ' [Fonte complementar](https://example.org/recipiente)'
    job['article']['markdown'] += '\n\n' + added
    candidate = {'after': added, 'addition': source['text'], 'origin': 'external_verified',
                 'claim_nature': 'assertion', 'supports': [support], 'limitations': ''}
    proof = store.artifact(job, 'iec_proof', 'review-fixture',
        {'candidates': [candidate], 'foundations': {source['id']: source}}, {})
    item = {'id': 'applied-review-fixture', 'context_kind': 'iec.v1', 'status': 'applied',
            'iec_proof_version': proof['version']}
    store.save_changes(job, item)
    job['apuration'] = {'items': [], 'inventory': {'blocks': []}}
    db.save_job(job)
    return job, source, item


def provider(monkeypatch, source, *, mutate=None, status='supported', matches=True):
    seen = []
    def respond(job, schema, instruction, stage, extra=None):
        assert schema is review.ArticleReview
        prepared = generation.prepare_structured(job, schema, instruction, stage, extra)
        seen.append({'payload': deepcopy(extra), 'request': prepared[0]})
        rows = []
        for passage in extra['passages']:
            selected = source['text'] in passage['text']
            rows.append({'passage_id': passage['id'], 'status': status if selected else 'not_factual',
                'reason': 'Fonte externa literal.' if selected else 'Metadado ou passagem neutra da fixture.',
                'supports': [{'reference_id': source['id'], 'excerpt': source['text'],
                              'offset_start': 0, 'offset_end': len(source['text'])}]
                            if selected and status == 'supported' else [], 'used_item_ids': []})
        value = {'summary': 'Artigo integral conferido.', 'editorial_alignment': {
            'matches_brief': matches, 'reason': 'Direção conferida.',
            'passage': '' if matches else extra['passages'][0]['text']}, 'assessments': rows}
        if mutate:
            mutate(value)
        return value
    monkeypatch.setattr(generation, 'structured', Mock(side_effect=respond))
    return seen


def test_later_review_reads_external_literal_and_preserves_video_identity(enriched, monkeypatch):
    job, source, _ = enriched
    seen = provider(monkeypatch, source)
    before = deepcopy(job['article'])
    output = workflow.factual_review(job, 0)
    assert len(seen) == 1 and review.available(job)
    assert seen[0]['payload']['article'] == before
    assert set(seen[0]['payload']['foundations']) == {'v1s1', source['id']}
    assert source['id'] not in generation.evidence_map(job)
    assert seen[0]['payload']['_iec_review_policy'] == 1
    assert core.IEC_POLICY in seen[0]['request']['instructions']
    assert output['semantic_coverage']['assessed'] == len(workflow.passages(before))
    assert output['supported_claims'][0]['evidence'][0]['origin'] == 'external_verified'
    assert not output['findings'] and db.get_job(job['id'])['article'] == before
    assert store.artifacts(job['id'], 'coverage')


def test_only_applied_claim_still_in_article_selects_complementary_review(enriched):
    job, _, item = enriched
    assert review.available(job)
    item['status'] = 'pending'
    store.save_changes(job, item)
    assert not review.available(job)
    item['status'] = 'applied'
    store.save_changes(job, item)
    job['article']['markdown'] = job['article']['markdown'].split('\n\n')[-2]
    assert not review.available(job)


def test_shadow_base_review_keeps_existing_spoken_contract(job, monkeypatch):
    engine.start(job, 'review')
    callback = Mock(return_value={'baseline': True})
    monkeypatch.setattr('app.editorial.video_first.factual_review', callback)
    assert workflow.factual_review(job, 0) == {'baseline': True}
    callback.assert_called_once_with(job, 0)


def test_expired_complement_stays_unavailable_and_uncertain(enriched, monkeypatch):
    job, source, item = enriched
    source['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    source['record_hash'] = core.foundation_hash(source)
    previous = store.artifacts(job['id'], 'iec_proof')[0]['data']
    previous['foundations'][source['id']] = source
    proof = store.artifact(job, 'iec_proof', 'expired-review', previous, {})
    item['iec_proof_version'] = proof['version']
    store.save_changes(job, item)
    seen = provider(monkeypatch, source, status='uncertain')
    output = workflow.factual_review(job, 0)
    assert source['id'] not in seen[0]['payload']['foundations']
    assert seen[0]['payload']['unavailable_foundations'] == [source['id']]
    assert output['findings'][0]['export_blocking'] is False
    assert output['supported_claims'] == []


@pytest.mark.parametrize('corruption', ['unknown_reference', 'wrong_excerpt', 'wrong_offsets',
                                        'missing_passage', 'invented_passage', 'false_alignment'])
def test_enriched_review_rejects_fabricated_proof(enriched, monkeypatch, corruption):
    job, source, _ = enriched
    before = deepcopy(job['article'])
    def mutate(output):
        row = next(row for row in output['assessments'] if row['supports'])
        if corruption == 'unknown_reference':
            row['supports'][0]['reference_id'] = 'legacy-web-note'
        elif corruption == 'wrong_excerpt':
            row['supports'][0]['excerpt'] = 'Frase inventada.'
        elif corruption == 'wrong_offsets':
            row['supports'][0]['offset_start'] = 1
        elif corruption == 'missing_passage':
            output['assessments'].pop()
        elif corruption == 'invented_passage':
            row['passage_id'] = 'not-in-article'
        else:
            output['editorial_alignment']['passage'] = 'Citação inventada do artigo.'
    provider(monkeypatch, source, mutate=mutate)
    with pytest.raises(generation.GenerationResponseError):
        workflow.factual_review(job, 0)
    assert db.get_job(job['id'])['article'] == before


def test_corrupt_proof_does_not_revert_to_video_only_approval(enriched):
    job, source, item = enriched
    with db.connect() as connection:
        connection.execute("DELETE FROM editorial_artifacts WHERE kind='iec_proof' AND job_id=?", (job['id'],))
    context = review.context(job)
    assert review.available(job)
    assert context['unavailable_foundations'] == ['applied_proof_unavailable']
    assert context['external_foundations'] == {}


def test_enriched_review_preserves_pending_issues_and_editorial_alignment(enriched, monkeypatch):
    job, source, _ = enriched
    store.issue(job, 'comparison', 'fixture', 'Condição essencial ainda desconhecida.', essential=True)
    provider(monkeypatch, source, matches=False)
    output = workflow.factual_review(job, 0)
    assert {finding['origin'] for finding in output['findings']} == {'pending_issue', 'editorial_alignment'}
    assert all(finding['export_blocking'] is False for finding in output['findings'])


def test_legacy_notes_are_not_foundations(enriched, monkeypatch):
    job, source, _ = enriched
    job['research'] = {'text': 'Legacy claim', 'pages': [{'id': 'web1', 'text': 'Legacy claim'}]}
    seen = provider(monkeypatch, source)
    workflow.factual_review(job, 0)
    assert 'web1' not in seen[0]['payload']['foundations']
    assert not seen[0]['payload']['_context_sources']


def test_generated_link_exemption_does_not_authorize_other_urls(enriched):
    job, _, item = enriched
    assert not any(row['code'] == 'external_links' for row in generation.deterministic_findings(job))
    job['article']['markdown'] += '\n\n[Fonte complementar](https://untrusted.example/claim)'
    assert any(row['code'] == 'external_links' for row in generation.deterministic_findings(job))
    job['article']['markdown'] = job['article']['markdown'].rsplit('\n\n', 1)[0]
    item['status'] = 'pending'
    store.save_changes(job, item)
    assert any(row['code'] == 'external_links' for row in generation.deterministic_findings(job))


def test_original_video_audio_issue_survives_unclaimed_review_passage(enriched, monkeypatch):
    job, source, _ = enriched
    store.issue(job, 'transcription', 'v1s1', 'Áudio incerto na fonte original.',
                source_ids=['v1s1'], essential=False)
    provider(monkeypatch, source)
    output = workflow.factual_review(job, 0)
    assert any(row.get('issue_origin') == 'transcription' and row['origin'] == 'pending_issue'
               and row['export_blocking'] is False for row in output['findings'])
