from copy import deepcopy
import json
import re
from collections import Counter

from .. import db, generation
from ..schemas import Article
from . import store


class EditConflict(ValueError):
    """An optional edit cannot safely be applied to its pinned article version."""


def preview(article, edits):
    result = deepcopy(article)
    spans = {}
    for edit in edits:
        field, before, after = edit['field'], edit['before'], edit['after']
        current = article[field]
        if field == 'markdown' and before.startswith('#') and '\n' not in before and '\n' in after:
            raise EditConflict('Uma mudança de título não pode inserir o conteúdo da seção. Selecione os blocos completos que serão substituídos.')
        if not before:
            if current:
                raise EditConflict('A alteração tentou inserir texto sem identificar um trecho único.')
            start, end = 0, 0
        elif current.count(before) != 1:
            raise EditConflict('O trecho da alteração não existe ou aparece mais de uma vez. O texto foi preservado.')
        else:
            start = current.index(before)
            end = start + len(before)
        selected = spans.setdefault(field, [])
        if (start, end, after) in selected:
            continue  # Repeated identical proposals have the same effect only once.
        if any((start, end) == (a, b) or start < b and end > a for a, b, _ in selected):
            raise EditConflict('A proposta contém mudanças conflitantes para o mesmo trecho. Reúna a correção em uma única alteração por bloco.')
        selected.append((start, end, after))
    # Apply all spans against the original version; inserted text never becomes another target.
    for field, selected in spans.items():
        for start, end, after in sorted(selected, reverse=True):
            result[field] = result[field][:start] + after + result[field][end:]
    def sentences(text):
        return Counter(generation.normalize(s) for s in re.split(r'(?<=[.!?])\s+|\n', text)
                       if len(s.split()) >= 8)
    original_sentences, new_sentences = sentences(article['markdown']), sentences(result['markdown'])
    if any(count > 1 and count > original_sentences.get(sentence, 0) for sentence, count in new_sentences.items()):
        raise EditConflict('A proposta duplicou uma frase do artigo. Substitua o bloco original sem repetir os trechos vizinhos.')
    return Article.model_validate(result).model_dump()


def validate_numbers(job, result):
    def numbers(value):
        return set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?(?!\w)', value))
    def content(article):
        from markdown_it import MarkdownIt
        # Ordered-list markers are presentation, not factual quantities. Parse
        # Markdown so new list numbering is not rejected as invented source data.
        tokens = MarkdownIt().parse(article['markdown'])
        parts = []
        for position, token in enumerate(tokens):
            if token.type not in ('inline', 'fence', 'code_block', 'html_block'):
                continue
            text = token.content
            if position and tokens[position - 1].type == 'heading_open':
                # H3 step numbering is presentation too. Keep years, quantities
                # and every other number in the heading subject to verification.
                text = re.sub(r'^(?:(?:Passo|Etapa)\s+\d+\s*[:.)-]?\s+|\d+[.)]\s+)', '', text, flags=re.I)
            parts.append(text)
        body = '\n'.join(parts)
        return '\n'.join([body, *(v for k, v in article.items() if k != 'markdown' and isinstance(v, str))])
    before = content(job['article'])
    after = content(result)
    sources = '\n'.join(s['text'] for s in generation.evidence_map(job).values())
    added = numbers(after) - numbers(before) - numbers(sources)
    if added:
        raise EditConflict('A proposta acrescentou números ausentes do artigo e das fontes: ' + ', '.join(sorted(added)) + '. Preserve a informação comprovada.')


def propose(job, role, plan, run_id):
    # Idempotent recovery after a process interruption between proposal and application.
    change_id = generation.article_hash({'run': run_id, 'role': role})[:32]
    existing = store.get_changes(job['id'], change_id)
    if existing:
        return existing
    current = deepcopy(job['article'])
    item = {'id': change_id, 'role': role, 'summary': plan['summary'], 'changes': plan['changes'],
            'base_hash': generation.article_hash(current), 'before_article': current, 'status': 'pending',
            'context_hash': generation.article_hash({'brief': job['brief'], 'sources': generation.evidence_map(job),
                                                     'plan': (job.get('plan') or {}).get('version')})}
    try:
        after = preview(current, plan['changes'])
        validate_numbers(job, after)
        # New structural/reference defects may never enter through an automatic edit.
        before_issues = {(f['reason'], f['passage']) for f in generation.deterministic_findings(job)}
        new_issues = {(f['reason'], f['passage']) for f in generation.deterministic_findings({**job, 'article': after})}
        if new_issues - before_issues:
            raise ValueError('A proposta introduz uma referência ou estrutura inválida.')
        item.update(after_article=after, result_hash=generation.article_hash(after))
        from .review_policy import verified_local_change
        proof = verified_local_change(job, plan['changes'])
        item.update(automatic_eligible=bool(proof), correction_proof=proof)
        if item['result_hash'] == item['base_hash']:
            item['status'] = 'unchanged'
    except ValueError as exc:
        item.update(status='invalid', error=str(exc)[:600])
    store.save_changes(job, item)
    return item


def decide(job, item, action, expected_hash, automatic=False):
    # The article, source context and proposal status must come from the same
    # locked database version. In-process locks alone cannot protect workers.
    with db.job_transaction(job['id']) as current:
        if current is None:
            raise EditConflict('O artigo não está mais disponível.')
        saved = store.get_changes(job['id'], item['id'])
        if saved is None:
            raise EditConflict('A proposta não está mais disponível.')
        result = _decide_locked(current, saved, action, expected_hash, automatic)
    previous_state = job.get('editorial')
    if isinstance(previous_state, dict) and isinstance(current.get('editorial'), dict):
        previous_state.clear()
        previous_state.update(current['editorial'])
        current['editorial'] = previous_state
    job.clear()
    job.update(current)
    item.clear()
    item.update(result)
    return item


def _decide_locked(job, item, action, expected_hash, automatic=False):
    current_hash = generation.article_hash(job['article'])
    if expected_hash != current_hash:
        raise ValueError('O artigo mudou. Atualize a página antes de aplicar a decisão.')
    if item.get('context_kind') == 'iec.v1':
        from .intelligence import context_hash
        current_context = context_hash(job)
        from ..pipeline import ACTIVE
        if job.get('status') in ACTIVE:
            raise EditConflict('A geração do artigo está em execução; a proposta foi preservada.')
    else:
        current_context = generation.article_hash(
            {'brief': job['brief'], 'sources': generation.evidence_map(job), 'plan': (job.get('plan') or {}).get('version')})
    if action == 'apply' and (job.get('article_needs_generation') or item.get('context_hash') != current_context):
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
        if undo and item.get('context_kind') == 'iec.v1' and generation.article_hash(item.get('before_article')) != item['base_hash']:
            raise EditConflict('A versão original do histórico foi alterada; o artigo foi preservado.')
        if item.get('context_kind') == 'iec.v1' and not undo:
            from .intelligence_runtime import validate_change
            validate_change(job, item)
        elif automatic and not undo:
            from .review_policy import verified_local_change
            proof = verified_local_change(job, item['changes'])
            if (not proof or item.get('result_hash') != generation.article_hash(preview(job['article'], item['changes']))
                    or item.get('result_hash') != generation.article_hash(item.get('after_article'))):
                raise EditConflict('A observação do revisor não comprova esta alteração. A versão anterior foi preservada.')
            item['correction_proof'] = proof
        previous_article = deepcopy(job['article'])
        if not automatic:
            store.invalidate(job, 'Uma alteração editorial mudou a versão do artigo.')
            job['status'] = 'needs_review'
        else:
            store.archive_review(job, 'Uma correção local comprovada mudou a versão do artigo.')
            job['review'] = None
        job['article'] = deepcopy(item['before_article'] if undo else item['after_article'])
        item['status'] = 'undone' if undo else 'applied'
    item['decided_at'] = db.now()
    job['updated_at'] = db.now()
    with db.connect() as c:
        if action != 'reject':
            c.execute('INSERT INTO revisions (job_id,created_at,data) VALUES (?,?,?)',
                      (job['id'], db.now(), json.dumps(previous_article, ensure_ascii=False)))
        db.save_job(job)
        c.execute('UPDATE change_sets SET status=?,data=? WHERE id=? AND job_id=?',
                  (item['status'], json.dumps(item, ensure_ascii=False), item['id'], job['id']))
    return item
