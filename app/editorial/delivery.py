"""Technical delivery availability, independent from editorial opinions.

These dimensions are derived on read so existing jobs keep their saved status,
sources and history. A stale or incomplete review never invalidates an article.
"""
from pydantic import ValidationError

from ..schemas import Article


def ensure_exportable(job):
    """Return validated saved content or a public, technical error."""
    article = job.get('article')
    if not article:
        raise ValueError('Ainda não há artigo salvo para exportação.')
    try:
        valid = Article.model_validate(article).model_dump()
    except (ValidationError, TypeError):
        raise ValueError('O artigo salvo tem um formato inválido. Corrija os campos do artigo antes de exportar.') from None
    for field, name in [('title', 'título'), ('slug', 'slug'), ('markdown', 'conteúdo')]:
        if not valid[field].strip():
            raise ValueError(f'O artigo salvo está sem {name}. Salve um artigo válido antes de exportar.')
    # XML/WXR and WordPress cannot reliably carry these control characters.
    if any(ord(char) < 32 and char not in '\n\r\t'
           for value in valid.values()
           for text in (value if isinstance(value, list) else [value])
           if isinstance(text, str) for char in text):
        raise ValueError('O artigo contém caracteres de controle inválidos. Corrija o conteúdo antes de exportar.')
    return valid


def describe(job):
    """Expose processing, editorial and delivery dimensions without migrations."""
    status = job.get('status', 'new')
    error = None
    try:
        ensure_exportable(job)
        available = True
    except ValueError as exc:
        available, error = False, str(exc)
    partial = available and (job.get('draft_delivery') or {}).get('complete') is False
    delivery_state = ('partial_draft' if partial else 'article_available') if available else (
        'technical_unavailable' if job.get('article') else 'unavailable')
    role = (job.get('editorial') or {}).get('current_role')
    processing_state = {
        'queued': 'queued', 'extracting': 'processing_sources',
        'analyzing': 'planning' if role == 'planner' else 'processing_sources',
        'researching': 'planning', 'writing': 'writing', 'optimizing': 'optimizing',
        'reviewing': 'completed' if available else 'optimizing',
        'ready': 'completed', 'needs_review': 'completed' if available else 'awaiting_input',
        'error': 'technical_error', 'interrupted': 'technical_error',
        'sources_unavailable': 'technical_error', 'budget_exhausted': 'technical_error',
        'transcription_pending': 'processing_sources', 'needs_input': 'awaiting_input',
        'awaiting_key': 'awaiting_input', 'plan_ready': 'awaiting_input',
    }.get(status, 'completed' if available else 'idle')
    review = job.get('review') or {}
    if status == 'reviewing':
        editorial_state = 'analyzing'
    elif not review or review.get('stale'):
        editorial_state = 'not_evaluated'
    else:
        from ..generation import article_hash
        if review.get('article_hash') != article_hash(job.get('article')):
            editorial_state = 'not_evaluated'
        else:
            findings = [f for f in review.get('findings', []) if not (f.get('resolution') or {}).get('dismissed')]
            uncertain = review.get('review_incomplete') or any(
                f.get('category') == 'factual_uncertainty' or f.get('origin') in
                ('semantic_review', 'pending_issue', 'budget', 'review_service') for f in findings)
            editorial_state = 'uncertainties' if uncertain else 'recommendations' if findings else 'clear'
    return {'processing_state': processing_state, 'editorial_state': editorial_state,
            'delivery_state': delivery_state, 'export_available': available,
            'export_error': error}
