"""Review applied complementary claims without turning web pages into videos."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .. import db, generation
from ..schemas import EditorialAlignment
from . import intelligence as core, intelligence_runtime, store
from .intelligence_contracts import IECSupport


class Assessment(BaseModel):
    passage_id: str
    status: Literal['supported', 'not_factual', 'unsupported', 'uncertain']
    reason: str = Field(max_length=1500)
    supports: list[IECSupport] = Field(max_length=12)
    used_item_ids: list[str]


class ArticleReview(BaseModel):
    summary: str = Field(max_length=2000)
    editorial_alignment: EditorialAlignment
    assessments: list[Assessment]


def context(job):
    """Only server-owned applied claims still present in this article qualify.

    Old research/background notes do not enter. Expired/mismatched originals
    remain visibly unavailable, requiring uncertainty rather than fake support.
    """
    foundations, claims, unavailable = {}, [], []
    with db.connect() as connection:
        rows = connection.execute('SELECT data FROM change_sets WHERE job_id=? AND status=?',
                                  (job['id'], 'applied')).fetchall()
    for row in rows:
        item = json.loads(row['data'])
        if item.get('context_kind') != 'iec.v1' or item['status'] != 'applied':
            continue
        try:
            proof = intelligence_runtime._proof_artifact(job, item['iec_proof_version'])['data']
        except ValueError:
            unavailable.append('applied_proof_unavailable')
            continue
        for candidate in proof['candidates']:
            if candidate['after'] not in job['article']['markdown']:
                continue
            claims.append({'addition': candidate['addition'], 'origin': candidate['origin'],
                           'claim_nature': candidate['claim_nature'], 'supports': candidate['supports'],
                           'limitations': candidate['limitations'], 'truth_status': 'not_independently_verified'})
            for support in candidate['supports']:
                ident = support['reference_id']
                foundation = proof['foundations'].get(ident)
                if not foundation or foundation.get('record_hash') != core.foundation_hash(foundation):
                    unavailable.append(ident)
                    continue
                if foundation['origin'] == 'external_verified':
                    try:
                        expiry = datetime.fromisoformat(foundation['expires_at'])
                        valid = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
                    except (KeyError, TypeError, ValueError):
                        valid = False
                    if not valid:
                        unavailable.append(ident)
                        continue
                    foundations[ident] = deepcopy(foundation)
    return {'complementary_claims': claims, 'external_foundations': foundations,
            'unavailable_foundations': list(dict.fromkeys(unavailable))}


def available(job):
    saved = context(job)
    return bool(saved['complementary_claims'] or saved['unavailable_foundations'])


def factual_review(job, round_index=0):
    from . import workflow
    article, version = job['article'], generation.article_hash(job['article'])
    parts, items = workflow.passages(article), job.get('apuration', {}).get('items', [])
    complementary = context(job)
    foundations = {**core.video_foundations(job), **complementary['external_foundations']}
    payload = {'article': article, 'passages': parts, 'foundations': foundations,
               **complementary, 'items': [workflow.writing_item(item) for item in items],
               '_context_sources': {}, '_iec_review_policy': 1,
               '_iec_review_contract': 1}
    instruction = '''Confira TODO o artigo integral e cada passage_id, incluindo metadados.
As fontes de vídeo sustentam somente o que foi falado, mantendo opinião, relato e condições.
Os complementos identificados em complementary_claims têm origem própria: uma frase externa
fundamentada em external_foundations não precisa existir no vídeo. Não a atribua ao criador.
Não aprove só por existir link ou aprovação antiga: releia a fonte literal e confira significado,
limitações, quantidades, precisão e relevância. Um registro unavailable exige uncertain quando
for necessário para sustentar a afirmação. Conhecimento do modelo não substitui foundations.
Selecione supports com reference_id, excerpt e offsets exatos no text; nunca invente [[v]] para
fato externo. supported exige apoio para TODAS as afirmações do bloco. not_factual apenas para
títulos neutros/transições; demais estados não contam used_item_ids. As avaliações anteriores
não são provas. Nenhuma preferência ou dúvida bloqueia exportação. Não reescreva o artigo.'''

    def validate(output):
        workflow.exact_ids([row['passage_id'] for row in output['assessments']],
                           [part['id'] for part in parts], 'Revisão do artigo enriquecido')
        alignment = output['editorial_alignment']['passage']
        if alignment and not any(alignment in part['text'] for part in parts):
            raise ValueError('A revisão precisa citar o artigo atual literalmente.')
        for assessment in output['assessments']:
            workflow.known_ids(assessment['used_item_ids'], [item['id'] for item in items], 'Cobertura')
            if assessment['status'] == 'supported' and not assessment['supports']:
                raise ValueError('A aprovação precisa de fundamentos literais disponíveis.')
            if assessment['status'] != 'supported' and assessment['used_item_ids']:
                raise ValueError('Informação sem apoio não pode contar como utilizada.')
            for support in assessment['supports']:
                record = foundations.get(support['reference_id'])
                if record is None:
                    raise ValueError('A revisão citou uma fonte indisponível.')
                start, end = support['offset_start'], support['offset_end']
                if not 0 <= start < end <= len(record['text']) or record['text'][start:end] != support['excerpt']:
                    raise ValueError('A citação precisa ser literal e pertencer à fonte recebida.')

    output = workflow.call(job, 'fact_reviewer', ArticleReview, instruction, payload,
        'iec-fidelity:' + generation.article_hash({'article': version, 'context': complementary,
            'request_policy': core.IEC_POLICY, 'instruction': instruction,
            'schema': ArticleReview.model_json_schema()}), validate)
    lookup = {part['id']: part['text'] for part in parts}
    findings, supported, used = [], [], set()
    for assessment in output['assessments']:
        evidence = [{'source_id': support['reference_id'], 'excerpt': support['excerpt'],
                     'origin': foundations[support['reference_id']]['origin']}
                    for support in assessment['supports']]
        if assessment['status'] in ('unsupported', 'uncertain'):
            findings.append({'severity': 'blocking', 'passage': lookup[assessment['passage_id']],
                'reason': assessment['reason'], 'suggestion': 'Confira a origem e os limites da afirmação.',
                'source_ids': [entry['source_id'] for entry in evidence], 'evidence': evidence,
                'origin': 'semantic_review', 'recipient': 'writing', 'export_blocking': False})
        if assessment['status'] == 'supported':
            used.update(assessment['used_item_ids'])
            supported.append({'statement': lookup[assessment['passage_id']],
                              'kind': 'afirmação', 'evidence': evidence})
    coverage = [{**row, 'planned_status': row['status'],
        'status': 'used' if row['item_id'] in used else row['status'] if row['status'] != 'used' else 'pending'}
        for row in (job.get('plan') or {}).get('data', {}).get('dispositions', [])]
    index = {item['id']: item for item in items}
    for row in coverage:
        if row['planned_status'] == 'used' and row['status'] != 'used':
            item = index.get(row['item_id'])
            findings.append({'severity': 'blocking', 'passage': '',
                'reason': 'Informação prevista não foi desenvolvida com apoio: ' +
                          (item['statement'] if item else row['item_id']),
                'suggestion': 'Complete a explicação ou ajuste o plano com uma exclusão justificada.',
                'source_ids': [entry['source_id'] for entry in (item or {}).get('evidence', [])],
                'recipient': 'writing', 'origin': 'coverage', 'export_blocking': False})
    cited = {foundation.get('source_id', support['reference_id'])
             for assessment in output['assessments'] for support in assessment['supports']
             for foundation in [foundations[support['reference_id']]]}
    cited.update(re.findall(r'\[\[([\w-]+)\]\]', article['markdown']))
    for issue in store.issues(job):
        audio_uncertain = issue['origin'] == 'transcription' and bool(set(issue['source_ids']) & cited)
        if issue['status'] == 'open' and (workflow.essential_issue(job, issue) or audio_uncertain):
            findings.append({'severity': 'blocking', 'passage': '', 'reason': issue['reason'],
                'suggestion': 'A dúvida permanece no diagnóstico; a versão salva continua disponível.',
                'source_ids': issue['source_ids'], 'recipient': 'apuration', 'origin': 'pending_issue',
                'issue_id': issue['id'], 'issue_origin': issue['origin'], 'export_blocking': False})
    if not output['editorial_alignment']['matches_brief']:
        findings.append({'severity': 'blocking', 'passage': output['editorial_alignment']['passage'],
            'reason': output['editorial_alignment']['reason'], 'suggestion': 'Confira a direção editorial.',
            'source_ids': [], 'origin': 'editorial_alignment', 'export_blocking': False})
    findings.extend(generation.deterministic_findings(job))
    result = {'evaluated_title': article['title'], 'editorial_alignment': output['editorial_alignment'],
              'summary': output['summary'], 'findings': findings, 'supported_claims': supported,
              'article_hash': version, 'reviewed_at': db.now(), 'coverage': coverage,
              'flow_version': workflow.VERSION, 'iec_review_version': 1,
              'semantic_coverage': {'passages': len(parts), 'assessed': len(parts), 'batches': 1,
                                   'assessments': output['assessments']}}
    job['coverage'] = {'article_hash': version, 'items': coverage,
                      'blocks': job.get('apuration', {}).get('inventory', {}).get('blocks', [])}
    store.artifact(job, 'coverage', version, job['coverage'], workflow.dependencies(job))
    db.save_job(job)
    return result
