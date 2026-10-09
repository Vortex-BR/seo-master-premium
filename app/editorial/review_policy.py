"""Contextual diagnostics and bounded corrections backed by observable article data.

A reviewer's assertion never constitutes proof of a defect. Only the local,
reproducible transformations below can authorize an unattended article edit.
"""
from collections import Counter
from copy import deepcopy
import re

from markdown_it import MarkdownIt

from .. import db, generation

VERSION = 1
LOCAL_PASSES = 1
_KINDS = ('escaped_paragraphs', 'undefined_footnotes', 'adjacent_duplicate_paragraph')
_RECOMMENDATION_ORIGINS = {'editorial_alignment', 'reader', 'readability_reviewer', 'chief',
                           'voice_editor', 'seo_editor', 'strategist', 'yoast_analyst',
                           'proposal_validation', 'correction_deferred'}
_UNCERTAINTY_ORIGINS = {'semantic_review', 'semantic_coverage', 'coverage', 'pending_issue',
                        'budget', 'review_unavailable', 'review_service', 'model_evidence'}


def _blocks(markdown):
    lines = markdown.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    for token in MarkdownIt().parse(markdown):
        if token.type not in ('paragraph_open', 'heading_open') or token.level or not token.map:
            continue
        start, end = (offsets[n] for n in token.map)
        result.append({'start': start, 'end': end, 'type': token.type,
                       'text': markdown[start:end]})
    return result


def _outside_code(text, start, end):
    # Fenced and indented code are absent from _blocks. Protect inline examples.
    return not any(start < match.end() and end > match.start()
                   for match in re.finditer(r'(`+)(.*?)\1', text, re.S))


def _edit(before, after, reason, source_ids=None):
    return {'field': 'markdown', 'before': before, 'after': after, 'reason': reason,
            'rule_ids': [], 'source_ids': source_ids or []}


def _stage_plan(job, kind):
    markdown = job['article']['markdown']
    blocks = _blocks(markdown)
    edits = []
    if kind == 'adjacent_duplicate_paragraph':
        index = 0
        while index < len(blocks):
            first = blocks[index]
            last = index
            if first['type'] == 'paragraph_open' and len(first['text'].split()) >= 8:
                while last + 1 < len(blocks):
                    previous, following = blocks[last], blocks[last + 1]
                    if (following['type'] != 'paragraph_open' or
                            following['text'].rstrip('\r\n') != first['text'].rstrip('\r\n') or
                            markdown[previous['end']:following['start']].strip()):
                        break
                    last += 1
            if last > index:
                before = markdown[first['start']:blocks[last]['end']]
                # A unique surrounding run is required by the pinned edit contract.
                if markdown.count(before) == 1:
                    edits.append(_edit(before, first['text'],
                        'Removida apenas a cópia adjacente e literal de um parágrafo; a versão com as mesmas referências foi preservada.'))
            index = last + 1
    else:
        mapping = generation.evidence_map(job)
        defined = set(re.findall(r'^\s*\[\^([^\]]+)\]:', markdown, re.M))
        for block in blocks:
            before = block['text']
            replacements, source_ids = [], []
            if kind == 'escaped_paragraphs':
                for match in re.finditer(r'\\n\s*\\n', before):
                    left, right = before[:match.start()].rstrip(), before[match.end():].lstrip()
                    heading = bool(re.match(r'^#{1,6}\s+\S', left.splitlines()[-1] if left else ''))
                    # Literal examples and ambiguous prose remain untouched. A
                    # completed sentence or serialized heading supplies the boundary.
                    if (left and right and (left[-1] in '.!?' or heading) and
                            re.match(r'(?:\w|#{1,6}\s+\S)', right) and
                            _outside_code(before, match.start(), match.end())):
                        replacements.append((match.start(), match.end(), '\n\n'))
            elif kind == 'undefined_footnotes':
                for match in re.finditer(r'\[\^([^\]]+)\](?!:)', before):
                    ident = match.group(1)
                    if ident not in defined and ident in mapping and _outside_code(before, match.start(), match.end()):
                        replacements.append((match.start(), match.end(), '[[' + ident + ']]'))
                        source_ids.append(ident)
            after = before
            for start, end, replacement in reversed(replacements):
                after = after[:start] + replacement + after[end:]
            if after != before and markdown.count(before) == 1:
                edits.append(_edit(before, after,
                    'Quebras escapadas foram convertidas em parágrafos sem mudar as palavras.' if kind == 'escaped_paragraphs'
                    else 'Nota sem definição convertida para o ID existente da fonte original, sem inventar referência.',
                    list(dict.fromkeys(source_ids))))
    return {'summary': 'Correção local comprovada: ' + kind, 'changes': edits[:20], 'findings': []}


def verified_local_change(job, edits):
    """Recompute proof from the pinned article; supplied model metadata is ignored."""
    if not edits:
        return None
    def signature(values):
        return sorted((e['field'], e['before'], e['after']) for e in values)
    selected = signature(edits)
    for kind in _KINDS:
        reproducible = _stage_plan(job, kind)['changes']
        available = signature(reproducible)
        if selected and len(set(selected)) == len(selected) and all(edit in available for edit in selected):
            return {'kind': 'local_validation', 'code': kind, 'verified': True,
                    'article_hash': generation.article_hash(job['article']),
                    'source_version': generation.article_hash(generation.evidence_map(job))}
    return None


def repair_local(job, round_index=0):
    """One free pass; retain every earlier article in the ordinary change history."""
    from . import changes
    state = job.get('editorial')
    if not state or not job.get('article'):
        return None
    context = {'policy_version': VERSION, 'article': generation.article_hash(job['article']),
               'sources': generation.evidence_map(job), 'brief': job.get('brief'),
               'plan': (job.get('plan') or {}).get('version')}
    fingerprint = generation.article_hash(context)
    previous = state.get('local_corrections')
    if previous and fingerprint in (previous['input_hash'], previous['result_input_hash']):
        return previous
    # Equivalent repeated finalization cannot start another repair pass.
    if round_index >= LOCAL_PASSES or previous:
        return previous
    report = {'input_hash': fingerprint, 'before_hash': context['article'], 'round': round_index,
              'max_rounds': LOCAL_PASSES, 'calls': 0, 'changes': [], 'status': 'completed'}
    kinds = _KINDS
    if job.get('article_needs_generation'):
        # A saved version is still deliverable, but changed upstream context is
        # not a reason to modify it during a review-only request.
        kinds = ()
        report.update(status='skipped', reason='sources_or_direction_changed')
    for kind in kinds:
        preserved = deepcopy(job)
        try:
            plan = _stage_plan(job, kind)
            if not plan['changes']:
                continue
            run_id = generation.article_hash({'cycle': job['editorial']['cycle_id'], 'input': fingerprint, 'kind': kind})
            item = changes.propose(job, 'local_correction', plan, run_id)
            if item['status'] == 'pending' and item.get('automatic_eligible'):
                changes.decide(job, item, 'apply', item['base_hash'], automatic=True)
            report['changes'].append({'id': item['id'], 'code': kind, 'status': item['status'],
                                      'before_hash': item['base_hash'], 'after_hash': item.get('result_hash'),
                                      'proof': item.get('correction_proof')})
        except Exception:
            # Optional correction must never prevent the factual review. The
            # transactional change machinery keeps storage at its last version.
            try:
                persisted = db.get_job(job['id'])
                if persisted:
                    preserved = persisted
            except Exception:
                pass
            shared_state = job.get('editorial')
            job.clear()
            job.update(preserved)
            if shared_state is not None and job.get('editorial'):
                shared_state.clear()
                shared_state.update(job['editorial'])
                job['editorial'] = shared_state
            report['changes'].append({'code': kind, 'status': 'deferred',
                                      'reason': 'A correção não pôde ser aplicada com segurança; a versão anterior foi preservada.'})
    report['after_hash'] = generation.article_hash(job['article'])
    report['changed'] = report['after_hash'] != report['before_hash']
    report['result_input_hash'] = generation.article_hash({**context, 'article': report['after_hash']})
    job['editorial']['local_corrections'] = report
    db.save_job(job)
    return report


def annotate_review(job, review):
    """Add compatible diagnostics, preserving severity and every original assertion."""
    article = job.get('article')
    if not article:
        return review
    version = generation.article_hash(article)
    current = not review.get('article_hash') or review['article_hash'] == version
    local = generation.deterministic_findings(job) if current else []
    mapping = generation.evidence_map(job)
    for finding in review.get('findings', []):
        observed = next((candidate for candidate in local if
                         candidate['reason'] == finding.get('reason') and
                         candidate.get('passage', '') == finding.get('passage', '')), None)
        origin = finding.get('origin', '')
        category = 'factual_uncertainty'
        if observed:
            category = observed['category']
        elif origin in _RECOMMENDATION_ORIGINS or finding.get('severity') in ('warning', 'info'):
            category = 'recommendation'
        if origin in _UNCERTAINTY_ORIGINS:
            category = 'factual_uncertainty'
        verified_evidence = []
        for evidence in finding.get('evidence', []):
            source = mapping.get(evidence.get('source_id'))
            excerpt = evidence.get('excerpt', '')
            if source and excerpt.strip() and generation.normalize(excerpt) in generation.normalize(source['text']):
                verified_evidence.append({'source_id': evidence['source_id'], 'excerpt': excerpt,
                                          'excerpt_verified': True})
        article_text = '\n'.join(v for v in article.values() if isinstance(v, str))
        passage = finding.get('passage', '')
        possible_false_positive = bool(not current or passage and
                                       generation.normalize(passage) not in generation.normalize(article_text))
        finding.update(category=category, export_blocking=False, auto_repair=False,
                       error_verified=bool(observed and category == 'objective'),
                       verified_evidence=verified_evidence, false_positive_possible=possible_false_positive)
        finding['verification'] = {'kind': 'local_validation' if observed else 'reviewer_observation',
                                   'verified': bool(observed), 'article_hash': version,
                                   'current_article': current,
                                   'source_ids_available': [ident for ident in finding.get('source_ids', []) if ident in mapping],
                                   'literal_evidence_verified': bool(verified_evidence),
                                   'notice': 'A presença de uma fonte ou a opinião do revisor não comprova, sozinha, que o trecho está errado.'}
    counts = Counter(finding['category'] for finding in review.get('findings', [])
                     if not (finding.get('resolution') or {}).get('dismissed'))
    review['policy'] = {'version': VERSION, 'export_blocking': False, 'categories': dict(counts),
                        'editorial_state': 'not_evaluated' if review.get('review_incomplete') else
                                           'uncertainties' if counts.get('factual_uncertainty') else
                                           'recommendations' if counts else 'clear',
                        'notice': 'A revisão orienta melhorias internas; observações pendentes não impedem a entrega.'}
    return review


def finalize(job, cycle=None):
    """Finalize diagnostics after review completion or failure without editing text."""
    if job.get('review'):
        annotate_review(job, job['review'])
        if job.get('editorial', {}).get('local_corrections'):
            job['review']['local_corrections'] = deepcopy(job['editorial']['local_corrections'])
    return job.get('review')
