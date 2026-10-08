"""Expose each paid writing checkpoint without treating it as editorial approval."""
from copy import deepcopy
import re
import unicodedata

from .. import db, generation
from . import store


def checkpoint(job, article, *, complete, completed_parts=None, total_parts=None):
    if not article.get('markdown', '').strip():
        return
    previous = job.get('draft_delivery') or {}
    cycle = job['editorial']['cycle_id']
    if previous.get('cycle_id') != cycle:
        db.revision(job)
    job['article'] = deepcopy(article)
    job['review'] = None
    job['draft_delivery'] = {'cycle_id': cycle, 'complete': complete,
        'inputs_version': store.inputs_version(job), 'plan_version': (job.get('plan') or {}).get('version'),
        'completed_parts': completed_parts, 'total_parts': total_parts,
        'review_pending': True, 'updated_at': db.now()}
    job.update(generation_complete=complete, article_needs_generation=not complete,
               article_editorial_version=generation.EDITORIAL_VERSION, article_evidence_version=1)
    # draft_installed belongs to the coordinator. The saved draft is visible
    # while the writer is still producing parts or attempting an optional edit.
    db.save_job(job)


def partial(job, markdown, completed_parts, total_parts):
    title = job['plan']['data']['title']
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', title).encode(
        'ascii', 'ignore').decode().lower()).strip('-') or 'artigo-' + job['id'][:8]
    checkpoint(job, {'title': title, 'seo_title': title, 'slug': slug,
        'meta_description': '', 'excerpt': '', 'tags': [], 'markdown': markdown},
        complete=False, completed_parts=completed_parts, total_parts=total_parts)
