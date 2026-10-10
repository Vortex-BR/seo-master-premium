from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os

from . import db


AUTOMATIC_PROVIDERS = {'Legendas do YouTube', 'Legendas do YouTube via proxy', 'Supadata',
                       'Transcrição de áudio OpenAI', 'Whisper local · YouTube'}
MAX_AGE = timedelta(hours=24)


def _current_contract(source):
    from .transcript_integrity import NORMALIZATION_VERSION

    options = {'merge_adjacent': os.getenv('TRANSCRIPT_AGGREGATE_CUES', '1') != '0'}
    if source.get('normalization_version') != NORMALIZATION_VERSION or source.get('normalization_options') != options:
        return False
    cue_ids = []
    for segment in source.get('segments', []):
        cues = segment.get('original_cues')
        if segment.get('normalization_version') != NORMALIZATION_VERSION or not isinstance(cues, list) or not cues:
            return False
        if any(not isinstance(cue, dict) or not isinstance(cue.get('id'), str)
               or not isinstance(cue.get('original_text'), str) or not isinstance(cue.get('text'), str)
               for cue in cues):
            return False
        owned = [cue['id'] for cue in cues]
        if segment.get('cue_ids', owned) != owned:
            return False
        cue_ids.extend(owned)
    return len(cue_ids) == len(set(cue_ids))


def _remap_references(value, identifiers):
    """Rebase owned references, never provider IDs, text or origin history."""
    if isinstance(value, list):
        return [_remap_references(item, identifiers) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ('original_id', 'original_text', 'origin') or key.startswith(('original_', 'reused_from_')):
            result[key] = item
        elif key in ('source_id', 'segment_id', 'cue_id') and isinstance(item, str):
            result[key] = identifiers.get(item, item)
        elif key in ('source_ids', 'segment_ids', 'cue_ids') and isinstance(item, list):
            result[key] = [identifiers.get(ident, ident) if isinstance(ident, str) else ident for ident in item]
        else:
            result[key] = _remap_references(item, identifiers)
    return result


def find_recent(video_id, prefix, exclude_job_id, *, audio_only=False, require_current=False):
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
            # A saved legacy job remains readable and resumable. A new source
            # must not silently reuse normalization whose lost raw cues cannot
            # be repaired; lower provider/audio caches can still reuse raw rows.
            if require_current and not _current_contract(source):
                continue
            if audio_only and source.get('medium') != 'audio' and source.get('provider') not in ('Whisper local · YouTube', 'Transcrição de áudio OpenAI'):
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
    old_source_id = source.get('id')
    identifiers = {old_source_id: prefix} if old_source_id else {}
    cue_index = 0
    for index, segment in enumerate(source['segments'], 1):
        old_segment_id = segment.get('id')
        new_segment_id = f'{prefix}s{index}'
        if old_segment_id:
            identifiers[old_segment_id] = new_segment_id
            segment.setdefault('original_segment_id', old_segment_id)
            segment['reused_from_segment_id'] = old_segment_id
        segment['id'] = new_segment_id
        cues = segment.get('original_cues')
        for cue in cues if isinstance(cues, list) else []:
            if not isinstance(cue, dict):
                continue
            cue_index += 1
            old_cue_id = cue.get('id')
            new_cue_id = f'{prefix}c{cue_index}'
            if isinstance(old_cue_id, str):
                identifiers[old_cue_id] = new_cue_id
                cue.setdefault('original_cue_id', old_cue_id)
                cue['reused_from_cue_id'] = old_cue_id
            cue['id'] = new_cue_id
    source = _remap_references(source, identifiers)
    # Build ownership from final IDs after remapping. With a reused prefix and
    # skipped empty cues, a new ID can equal a different old ID; applying the
    # mapping twice would then bind the segment to the wrong cue.
    for segment in source['segments']:
        if isinstance(segment.get('original_cues'), list):
            segment['cue_ids'] = [cue['id'] for cue in segment['original_cues'] if isinstance(cue, dict)]
    if old_source_id:
        source.setdefault('original_source_id', old_source_id)
        source['reused_from_source_id'] = old_source_id
    source['id'] = prefix
    source['extracted_at'] = extracted.isoformat()
    source['reused_at'] = db.now()
    source['reused_from_job_id'] = origin
    source.pop('error', None)
    return source
