from copy import deepcopy
import json
import re
from collections import Counter

from .. import db, generation
from ..schemas import Article
from . import store


def preview(article, edits):
    result = deepcopy(article)
    for edit in edits:
        field, before, after = edit['field'], edit['before'], edit['after']
        current = result[field]
        if field == 'markdown' and before.startswith('#') and '\n' not in before and '\n' in after:
            raise ValueError('Uma mudança de título não pode inserir o conteúdo da seção. Selecione os blocos completos que serão substituídos.')
        if not before:
            if current:
                raise ValueError('A alteração tentou inserir texto sem identificar um trecho único.')
            result[field] = after
        elif current.count(before) != 1:
            raise ValueError('O trecho da alteração não existe ou aparece mais de uma vez. O texto foi preservado.')
        else:
            result[field] = current.replace(before, after, 1)
    def sentences(text):
        return Counter(generation.normalize(s) for s in re.split(r'(?<=[.!?])\s+|\n', text)
                       if len(s.split()) >= 8)
    original_sentences, new_sentences = sentences(article['markdown']), sentences(result['markdown'])
    if any(count > 1 and count > original_sentences.get(sentence, 0) for sentence, count in new_sentences.items()):
        raise ValueError('A proposta duplicou uma frase do artigo. Substitua o bloco original sem repetir os trechos vizinhos.')
    return Article.model_validate(result).model_dump()


def validate_numbers(job, result):
    def numbers(value):
        return set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?(?!\w)', value))
    before = '\n'.join(v for v in job['article'].values() if isinstance(v, str))
    after = '\n'.join(v for v in result.values() if isinstance(v, str))
    sources = '\n'.join(s['text'] for s in generation.evidence_map(job).values())
    added = numbers(after) - numbers(before) - numbers(sources)
    if added:
        raise ValueError('A proposta acrescentou números ausentes do artigo e das fontes: ' + ', '.join(sorted(added)) + '. Preserve a informação comprovada.')


def propose(job, role, plan, run_id):
    # Idempotent recovery after a process interruption between proposal and application.
    change_id = generation.article_hash({'run': run_id, 'role': role})[:32]
    existing = store.get_changes(job['id'], change_id)
    if existing:
        return existing
    current = deepcopy(job['article'])
    item = {'id': change_id, 'role': role, 'summary': plan['summary'], 'changes': plan['changes'],
            'base_hash': generation.article_hash(current), 'before_article': current, 'status': 'pending',
            'context_hash': generation.article_hash({'brief': job['brief'], 'sources': generation.evidence_map(job)})}
    try:
        after = preview(current, plan['changes'])
        validate_numbers(job, after)
        # New structural/reference defects may never enter through an automatic edit.
        before_issues = {(f['reason'], f['passage']) for f in generation.deterministic_findings(job)}
        new_issues = {(f['reason'], f['passage']) for f in generation.deterministic_findings({**job, 'article': after})}
        if new_issues - before_issues:
            raise ValueError('A proposta introduz uma referência ou estrutura inválida.')
        item.update(after_article=after, result_hash=generation.article_hash(after))
        if item['result_hash'] == item['base_hash']:
            item['status'] = 'unchanged'
    except ValueError as exc:
        item.update(status='invalid', error=str(exc)[:600])
    store.save_changes(job, item)
    return item


def decide(job, item, action, expected_hash, automatic=False):
    current_hash = generation.article_hash(job['article'])
    if expected_hash != current_hash:
        raise ValueError('O artigo mudou. Atualize a página antes de aplicar a decisão.')
    if action == 'apply' and (job.get('article_needs_generation') or item.get('context_hash') != generation.article_hash(
            {'brief': job['brief'], 'sources': generation.evidence_map(job)})):
        raise ValueError('As fontes ou a direção mudaram. Gere novas propostas antes de aplicar.')
    if action == 'reject':
        if item['status'] != 'pending':
            raise ValueError('Esta proposta não está pendente.')
        item['status'] = 'rejected'
    else:
        undo = action == 'undo'
        expected_status, expected_version = ('applied', item.get('result_hash')) if undo else ('pending', item['base_hash'])
        if item['status'] != expected_status or current_hash != expected_version:
            raise ValueError('A proposta pertence a outra versão ou já foi resolvida. O texto foi preservado.')
        previous_article = deepcopy(job['article'])
        job['article'] = deepcopy(item['before_article'] if undo else item['after_article'])
        if not automatic:
            store.invalidate(job, 'Uma alteração editorial mudou a versão do artigo.')
            job['status'] = 'needs_review'
        else:
            job['review'] = None
        item['status'] = 'undone' if undo else 'applied'
    item['decided_at'] = db.now()
    job['updated_at'] = db.now()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not row or generation.article_hash(json.loads(row['data'])['article']) != expected_hash:
            raise ValueError('O artigo mudou durante a decisão. Atualize a página.')
        if action != 'reject':
            c.execute('INSERT INTO revisions (job_id,created_at,data) VALUES (?,?,?)',
                      (job['id'], db.now(), json.dumps(previous_article, ensure_ascii=False)))
        c.execute('UPDATE jobs SET status=?,updated_at=?,data=? WHERE id=?',
                  (job['status'], job['updated_at'], json.dumps(job, ensure_ascii=False), job['id']))
        c.execute('UPDATE change_sets SET status=?,data=? WHERE id=? AND job_id=?',
                  (item['status'], json.dumps(item, ensure_ascii=False), item['id'], job['id']))
    return item
