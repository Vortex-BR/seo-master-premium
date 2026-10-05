from copy import deepcopy
from datetime import datetime, timedelta, timezone

from . import db


AUTOMATIC_PROVIDERS = {'Legendas do YouTube', 'Legendas do YouTube via proxy', 'Supadata',
                       'Transcrição de áudio OpenAI'}
MAX_AGE = timedelta(hours=24)


def find_recent(video_id, prefix, exclude_job_id):
    """Reuse a recent automatic transcript from this workspace, retaining its provenance."""
    now = datetime.now(timezone.utc)
    candidates = []
    for job in db.list_jobs():
        if job['id'] == exclude_job_id:
            continue
        for source in job.get('sources', []):
            if (source.get('video_id') != video_id or source.get('status') != 'ok'
                    or source.get('provider') not in AUTOMATIC_PROVIDERS):
                continue
            # Old jobs predate extracted_at. Their creation time is a conservative fallback.
            extracted_at = source.get('extracted_at') or job.get('created_at', '')
            try:
                extracted = datetime.fromisoformat(extracted_at)
                if not extracted.tzinfo or not timedelta(0) <= now - extracted <= MAX_AGE:
                    continue
            except (TypeError, ValueError):
                continue
            segments = source.get('segments', [])
            if not segments or any(not isinstance(s.get('text'), str) or not s['text'].strip() for s in segments):
                continue
            if not 80 <= sum(len(s['text']) for s in segments) <= 120000:
                continue
            candidates.append((extracted, job['id'], source))
    if not candidates:
        return None
    extracted, origin, source = max(candidates, key=lambda item: item[0])
    source = deepcopy(source)
    source['id'] = prefix
    for index, segment in enumerate(source['segments'], 1):
        segment['id'] = f'{prefix}s{index}'
    source['extracted_at'] = extracted.isoformat()
    source['reused_at'] = db.now()
    source['reused_from_job_id'] = origin
    source.pop('error', None)
    return source
