"""Deterministic knowledge sidecars; never an independent source or paid stage.

Only project existing structure and observable literal cues. A stored opinion
remains an opinion; a Video-First synthetic ``kind=fato`` carries no semantic
classification. Original source records live once in a separate immutable
artifact and are recovered through the same source resolver as factual review.
"""
import math
import os
import re
from collections import Counter
from copy import deepcopy

from .. import generation
from . import store
from .human_knowledge_contracts import HumanKnowledgeDossier, HumanKnowledgeSources


PROJECTION_VERSION = 1
SCHEMA_VERSION = 'human_knowledge.v1'
SOURCE_SCHEMA_VERSION = 'human_knowledge_sources.v1'

# These locate words in the original, not their meaning or scientific truth.
# A question inside a quotation, for example, is still only a candidate.
_CUES = (
    ('opinion', r'\b(?:eu acho|na minha opinião|na minha opiniao|eu acredito)\b'),
    ('experience', r'\b(?:eu testei|eu experimentei|no meu caso|na minha experiência)\b'),
    ('observation', r'\b(?:eu observei|eu percebi|eu notei)\b'),
    ('example', r'\b(?:por exemplo)\b'),
    ('analogy', r'\b(?:é como se|e como se|funciona como)\b'),
    ('proposed_cause', r'\b(?:porque|por causa de)\b'),
    ('condition', r'\b(?:somente se|apenas se|desde que|caso)\b'),
    ('exception', r'\b(?:exceto|a menos que|salvo se)\b'),
    ('risk', r'\b(?:cuidado|risco de|pode causar)\b'),
    ('doubt', r'\b(?:não sei|nao sei|talvez|pode ser que)\b'),
    ('result', r'\b(?:o resultado foi|obtive|consegui)\b'),
)
_CUES = tuple((kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _CUES)


def mode():
    """Unknown flags fail closed to off, never select an unreviewed active mode."""
    value = os.getenv('HUMAN_KNOWLEDGE_MODE', 'shadow').strip().lower()
    return value if value in ('off', 'shadow') else 'off'


def _hash(value):
    return generation.article_hash(value)


def _source_snapshot(job):
    return HumanKnowledgeSources(sources=deepcopy(job.get('sources', []))).model_dump()


def _snapshot_dependencies(snapshot):
    return {'sources': _hash(snapshot['sources']), 'schema_version': SOURCE_SCHEMA_VERSION}


def _snapshot_version(snapshot):
    return _hash({'kind': 'human_knowledge_sources', 'scope': 'all', 'data': snapshot,
                  'dependencies': _snapshot_dependencies(snapshot)})


def _apuration(job):
    saved = job.get('apuration')
    if not isinstance(saved, dict):
        return {}, None
    content = saved.get('data') if isinstance(saved.get('data'), dict) else saved
    # Flattened apurations take precedence over their previous artifact data.
    content = {**content, **{key: value for key, value in saved.items()
                            if key not in ('data', 'created_at', 'kind', 'scope', 'dependencies')}}
    return content, saved


def _apuration_status(job, content, saved):
    if saved is None:
        return 'missing'
    if saved.get('valid') is False:
        return 'stale'
    recorded_inputs = (saved.get('dependencies') or {}).get('inputs')
    if recorded_inputs and 'brief' in job and recorded_inputs != store.inputs_version(job):
        return 'stale'
    blocks = (content.get('inventory') or {}).get('blocks', [])
    if blocks and any(block.get('status') not in ('checked', 'extracted') for block in blocks):
        return 'partial'
    if not content.get('items'):
        return 'partial'
    return 'current' if saved.get('valid') is True and recorded_inputs and 'brief' in job else 'legacy_unverified'


class _Reader:
    """Index refs once; the existing resolver still validates every record.

    A unique ref is resolved in its owning source/segment. Duplicate usable IDs
    are rejected before narrowing, so indexing never hides an ambiguity.
    """
    def __init__(self, job):
        self.refs, self.internal, self.records, self.hashes = {}, set(), {}, {}
        for source in job.get('sources', []):
            for segment in source.get('segments', []):
                cues = segment.get('original_cues')
                records = [segment] + ([cue for cue in cues if isinstance(cue, dict)] if isinstance(cues, list) else [])
                for record in records:
                    reference_id = record.get('id')
                    if not isinstance(reference_id, str) or not reference_id:
                        continue
                    if (source.get('internal_context_only') or segment.get('internal_context_only')
                            or record.get('internal_context_only')):
                        self.internal.add(reference_id)
                    else:
                        self.refs.setdefault(reference_id, []).append((source, segment))
        self.source_ids = Counter(source.get('id') for source in job.get('sources', []))

    def status(self, reference_id):
        if not isinstance(reference_id, str) or not reference_id:
            return 'invalid_reference'
        refs = self.refs.get(reference_id, [])
        return 'ambiguous' if len(refs) > 1 else 'internal_only' if reference_id in self.internal and not refs else 'missing'

    def resolve(self, reference_id):
        if not isinstance(reference_id, str) or not reference_id:
            return None
        if reference_id not in self.records:
            refs = self.refs.get(reference_id, [])
            if len(refs) != 1:
                self.records[reference_id] = None
            else:
                source, segment = refs[0]
                self.records[reference_id] = generation.resolve_evidence(
                    {'sources': [{**source, 'segments': [segment]}]}, reference_id)
        return self.records[reference_id]

    def source_hash(self, reference_id):
        source = self.refs[reference_id][0][0]
        if self.source_ids[source.get('id')] != 1:
            return None
        key = id(source)
        if key not in self.hashes:
            self.hashes[key] = _hash(source)
        return self.hashes[key]


def _timing(record):
    def valid(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value >= 0)
    start = record.get('start') if valid(record.get('start')) else None
    end = record.get('end') if valid(record.get('end')) else None
    if start is not None and end is not None and end < start:
        end = None
    availability = ('point' if start == end else 'interval') if start is not None and end is not None else (
        'partial' if start is not None or end is not None else 'unavailable')
    return {'start': start, 'end': end, 'availability': availability}


def _anchor(job, evidence, reader):
    reference_id = evidence.get('source_id') if isinstance(evidence, dict) else evidence
    record = reader.resolve(reference_id)
    if record is None:
        return {'reference_id': reference_id if isinstance(reference_id, str) else None,
                'status': reader.status(reference_id)}
    speaker = record.get('speaker')
    label = speaker if isinstance(speaker, str) else speaker.get('label') if isinstance(speaker, dict) else None
    label = label if isinstance(label, str) and label else None
    intervals = [{'reference_id': cue.get('id'), **_timing(cue)}
                 for cue in record['original_cues'] if not cue.get('internal_context_only')]
    if not intervals:
        raw_intervals = record.get('intervals')
        intervals = [{'reference_id': reference_id, **_timing(interval)}
                     for interval in raw_intervals if isinstance(interval, dict)] if isinstance(raw_intervals, list) else []
    if not intervals:
        intervals = [{'reference_id': reference_id, **record['timing']}]
    result = {'reference_id': reference_id, 'status': 'resolved', 'source_id': record['source_id'],
              'segment_id': record['segment_id'], 'cue_ids': record['cue_ids'],
              'source_hash': reader.source_hash(reference_id),
              'record_hash': _hash(record), 'timing': record['timing'], 'intervals': intervals,
              'speaker_label': label, 'speaker_metadata': deepcopy(speaker)}
    if isinstance(evidence, dict) and 'excerpt' in evidence:
        excerpt = evidence['excerpt']
        result['excerpt'] = excerpt if isinstance(excerpt, str) else None
        text = record['text']
        start, end = evidence.get('offset_start'), evidence.get('offset_end')
        if start is not None or end is not None:
            valid = (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                     and text[start:end] == excerpt)
            if valid:
                result.update(offset_start=start, offset_end=end)
            else:
                result['status'] = 'invalid_excerpt'
        elif not isinstance(excerpt, str) or not excerpt or excerpt not in text:
            result['status'] = 'invalid_excerpt'
        elif text.find(excerpt) != text.rfind(excerpt):
            result['status'] = 'ambiguous_excerpt'
        else:
            start = text.index(excerpt)
            result.update(offset_start=start, offset_end=start + len(excerpt))
    if result['source_hash'] is None:
        result.setdefault('warnings', []).append('source_id_not_unique')
    return result


def _candidates(reader, anchors):
    candidates = []
    for anchor in anchors:
        if anchor['status'] != 'resolved':
            continue
        original = reader.resolve(anchor['reference_id'])
        for kind, pattern in _CUES:
            for match in pattern.finditer(original['text']):
                candidates.append({'type': kind, 'reference_id': anchor['reference_id'],
                                   'excerpt': match.group(), 'offset_start': match.start(), 'offset_end': match.end()})
    return candidates


def _classification(item, synthetic):
    if not synthetic:
        kind = {'fato': 'assertion', 'opinião': 'opinion', 'experiência': 'experience'}.get(item.get('kind'))
        if kind:
            return kind, {'basis': 'legacy_kind', 'status': 'declared', 'source_field': 'kind'}
        kind = {'procedimento': 'method', 'exemplo': 'example'}.get(item.get('information_type'))
        if kind:
            return kind, {'basis': 'legacy_information_type', 'status': 'declared', 'source_field': 'information_type'}
    return 'unknown', {'basis': 'unknown', 'status': 'unknown', 'source_field': None}


def _projected_items(content):
    """Recover the richer spoken contract when only its older projection exists."""
    items = deepcopy(content.get('items', []))
    for n, video in enumerate((content.get('spoken_extraction') or {}).get('videos', [])):
        for k, insight in enumerate(video.get('insights', [])):
            matches = [item for item in items if item.get('video_id') == video['video_id']
                       and item.get('statement') == insight.get('spoken_explanation')
                       and [value.get('source_id') for value in item.get('evidence', [])]
                       == insight.get('source_segment_ids')]
            if len(matches) == 1:
                matches[0].setdefault('source_spoken_insight', deepcopy(insight))
            elif not any(item.get('source_spoken_insight') == insight for item in items):
                items.append({'video_id': video['video_id'], 'topic': insight.get('topic', ''),
                              'statement': insight.get('spoken_explanation', ''),
                              'source_spoken_insight': deepcopy(insight),
                              'adapter_source_path': f'spoken_extraction.videos[{n}].insights[{k}]'})
    return items


def _unit(job, *, reader, ident, item_id, field, text, topic, kind, classification, evidence,
          preserved=None, parent=None, stale=False):
    anchors = [_anchor(job, value, reader) for value in evidence]
    if stale:
        for anchor in anchors:
            if anchor['status'] == 'resolved':
                anchor.update(status='stale', warnings=['apuration_source_dependencies_changed'])
    return {'id': ident, 'legacy_item_id': item_id, 'parent_unit_id': parent, 'type': kind,
            'classification': classification, 'candidates': _candidates(reader, anchors),
            'origin': 'video', 'origin_status': 'anchored' if anchors and all(
                anchor['status'] == 'resolved' for anchor in anchors) else 'unresolved',
            'text': text, 'topic': topic, 'source_field': field, 'preserved': deepcopy(preserved or {}),
            'anchors': anchors, 'speaker_labels': list(dict.fromkeys(
                anchor['speaker_label'] for anchor in anchors if anchor.get('speaker_label')))}


def _item_units(job, item, index, synthetic, reader, stale):
    raw_id = item.get('id') if isinstance(item.get('id'), str) else None
    ident = 'hku-' + _hash({'item': item, 'position': index})[:24]
    spoken = item.get('source_spoken_insight')
    spoken = spoken if isinstance(spoken, dict) else None
    evidence = item.get('evidence') or [{'source_id': key} for key in (spoken or {}).get('source_segment_ids', [])]
    kind, classification = _classification(item, synthetic or spoken is not None)
    text = (spoken or {}).get('spoken_explanation', item.get('statement', ''))
    field = 'source_spoken_insight.spoken_explanation' if spoken is not None else 'statement'
    # Preserve every existing nuance without interpreting it as an original quote.
    preserved = {key: deepcopy(value) for key, value in item.items() if key not in ('statement', 'evidence')}
    units = [_unit(job, reader=reader, ident=ident, item_id=raw_id, field=field, text=text,
                   topic=item.get('topic', ''), kind=kind, classification=classification,
                   evidence=evidence, preserved=preserved, stale=stale)]
    relations = []
    fields = [('method', 'method', item.get('method'), 'legacy_field'),
              ('conditions', 'condition', item.get('conditions'), 'legacy_field'),
              ('restrictions', 'unknown', item.get('restrictions'), 'legacy_field'),
              ('limitations', 'unknown', item.get('limitations'), 'legacy_field')]
    if spoken is not None:
        fields.extend(('source_spoken_insight.' + key, kind, spoken.get(key), 'spoken_field')
                      for key, kind in (('practical_tips', 'method'), ('analogies', 'analogy'), ('warnings', 'risk')))
    for field, kind, values, basis in fields:
        for number, value in enumerate([values] if isinstance(values, str) else values or []):
            if not isinstance(value, str) or not value:
                continue
            child_id = 'hku-' + _hash({'parent': ident, 'field': field, 'position': number, 'text': value})[:24]
            units.append(_unit(job, reader=reader, ident=child_id, item_id=raw_id, parent=ident, field=field,
                               text=value, topic=item.get('topic', ''), kind=kind,
                               classification={'basis': basis, 'status': 'declared', 'source_field': field},
                               evidence=evidence, stale=stale))
            relations.append({'id': 'hkr-' + _hash([ident, child_id, field])[:24],
                              'relation': 'field_of', 'unit_ids': [ident, child_id],
                              'legacy_item_ids': [raw_id] if raw_id else [], 'explanation': '',
                              'basis': 'existing_field', 'status': 'declared', 'source_field': field})
    return units, relations


def _relations(content, units):
    primary = {}
    for unit in units:
        if not unit.get('parent_unit_id') and unit['legacy_item_id']:
            primary.setdefault(unit['legacy_item_id'], []).append(unit['id'])
    rows = []
    for n, video in enumerate(content.get('videos', [])):
        rows.extend((f'videos[{n}].relations[{k}]', value) for k, value in enumerate(video.get('relations', [])))
    for n, comparison in enumerate(content.get('comparisons', [])):
        rows.extend((f'comparisons[{n}].rows[{k}]', value) for k, value in enumerate(comparison.get('rows', [])))
    result = []
    for path, relation in rows:
        item_ids = relation.get('item_ids', [])
        known = bool(item_ids) and all(len(primary.get(key, [])) == 1 for key in item_ids)
        result.append({'id': 'hkr-' + _hash({'path': path, 'data': relation})[:24],
                       'relation': relation.get('relation', 'unknown'),
                       'unit_ids': [primary[key][0] for key in item_ids] if known else [],
                       'legacy_item_ids': deepcopy(item_ids), 'explanation': relation.get('explanation', ''),
                       'basis': 'existing_relation', 'status': 'declared' if known else 'unresolved',
                       'source_field': path, 'check': deepcopy(relation.get('check'))})
    return result


def _coverage(units, sources):
    anchors = [anchor for unit in units for anchor in unit['anchors']]
    return {'units': len(units), 'by_type': dict(Counter(unit['type'] for unit in units)),
            'by_classification_basis': dict(Counter(unit['classification']['basis'] for unit in units)),
            'candidate_cues_by_type': dict(Counter(cue['type'] for unit in units for cue in unit['candidates'])),
            'units_missing_provenance': sum(unit['origin_status'] != 'anchored' for unit in units),
            'anchor_status': dict(Counter(anchor['status'] for anchor in anchors)),
            'timing': dict(Counter((anchor.get('timing') or {}).get('availability', 'unavailable') for anchor in anchors)),
            'units_unknown_speaker': sum(not unit['speaker_labels'] for unit in units),
            'anchors_unknown_speaker': sum(not anchor.get('speaker_label') for anchor in anchors),
            'source_segments': sum(len(source.get('segments', [])) for source in sources
                                   if not source.get('internal_context_only')),
            'semantic_verification': 'not_performed', 'transcript_completeness': 'unverified'}


def project(job):
    """Pure versioned adapter: no providers, storage, job mutation or article edits."""
    snapshot = _source_snapshot(job)
    content, saved = _apuration(job)
    apuration_status = _apuration_status(job, content, saved)
    dependencies = {'projection_version': PROJECTION_VERSION, 'source_schema_version': SOURCE_SCHEMA_VERSION,
                    'sources': _hash(snapshot['sources']), 'apuration_content': _hash(content),
                    'apuration_valid': saved.get('valid') if saved else None,
                    'apuration_version': saved.get('version') if saved else None}
    units, relations = [], []
    reader = _Reader(job)
    items = _projected_items(content)
    for n, item in enumerate(items):
        projected, attached = _item_units(job, item, n, content.get('video_first') is True, reader,
                                         apuration_status == 'stale')
        units.extend(projected)
        relations.extend(attached)
    # A source with no structured extraction remains recoverable, not empty or
    # "complete". Uncovered segments are kept as refs even after partial jobs.
    covered = {anchor['segment_id'] for unit in units for anchor in unit['anchors']
               if anchor.get('segment_id') and anchor['status'] == 'resolved'}
    for source in job.get('sources', []):
        if source.get('internal_context_only'):
            continue
        for n, segment in enumerate(source.get('segments', [])):
            if segment.get('internal_context_only') or segment.get('id') in covered:
                continue
            ident = 'hku-' + _hash({'source': source.get('id'), 'segment': segment, 'position': n})[:24]
            units.append(_unit(job, reader=reader, ident=ident, item_id=None, field='source_segment', text='',
                               topic=source.get('title', ''), kind='unknown',
                               classification={'basis': 'original_reference', 'status': 'unknown',
                                               'source_field': 'sources.segments'},
                               evidence=[{'source_id': segment.get('id')}]))
    relations.extend(_relations(content, units))
    gaps = [str(gap) for video in content.get('videos', []) for gap in video.get('gaps', [])]
    gaps.extend(str(gap) for video in (content.get('spoken_extraction') or {}).get('videos', [])
                for gap in video.get('gaps', []))
    gaps.extend(str(gap) for gap in content.get('gaps', []))
    if apuration_status != 'current':
        gaps.append('structured_extraction_' + apuration_status)
    if any(unit['type'] == 'unknown' for unit in units):
        gaps.append('semantic_classification_incomplete')
    if any(unit['origin_status'] != 'anchored' for unit in units):
        gaps.append('provenance_incomplete')
    sources = [{'source_id': source.get('id'), 'video_id': source.get('video_id'),
                'title': source.get('title'), 'url': source.get('url'),
                'video_author': {'label': source.get('author') or None, 'basis': 'source_metadata',
                                 'identity_status': 'unverified'},
                'source_hash': _hash(source)} for source in job.get('sources', [])
               if not source.get('internal_context_only')]
    dossier = HumanKnowledgeDossier(
        projection_version=PROJECTION_VERSION, job_id=job['id'], source_snapshot_hash=_hash(snapshot),
        source_snapshot_version=_snapshot_version(snapshot), dependencies=dependencies,
        apuration_status=apuration_status, sources=sources, units=units, relations=relations,
        coverage=_coverage(units, snapshot['sources']), gaps=list(dict.fromkeys(gaps)))
    return dossier.model_dump()


def persist_shadow(job):
    """Append immutable sidecars only; off does no projection or storage work."""
    if mode() == 'off':
        return None
    dossier = project(job)
    snapshot = _source_snapshot(job)
    store.artifact(job, 'human_knowledge_sources', 'all', snapshot, _snapshot_dependencies(snapshot))
    return store.artifact(job, 'human_knowledge', 'all', dossier, dossier['dependencies'])


def _dossier(value):
    data = value.get('data') if isinstance(value, dict) and value.get('kind') == 'human_knowledge' else value
    if (not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION
            or type(data.get('projection_version')) is not int
            or not 1 <= data['projection_version'] <= PROJECTION_VERSION):
        raise ValueError('Versão da camada de conhecimento não suportada.')
    return HumanKnowledgeDossier.model_validate(data).model_dump()


def resolve_unit(job, dossier, unit_id, source_snapshot=None):
    """Recover verified original records without I/O, optionally from old snapshot.

    Current sources that differ from the dossier are stale. Old source artifacts
    must be explicitly supplied; this function never silently reads a new
    source under an old reference or fetches metadata from the network.
    """
    data = _dossier(dossier)
    matches = [unit for unit in data['units'] if unit['id'] == unit_id]
    result = {'unit_id': unit_id, 'status': 'unresolved', 'records': [], 'unresolved': []}
    if data['job_id'] != job.get('id'):
        result['unresolved'].append({'reason': 'job_mismatch'})
        return result
    if len(matches) != 1:
        result['unresolved'].append({'reason': 'unit_missing_or_ambiguous'})
        return result
    snapshot = source_snapshot
    if isinstance(snapshot, dict) and snapshot.get('kind') == 'human_knowledge_sources':
        snapshot = snapshot.get('data')
    if snapshot is None:
        snapshot = _source_snapshot(job)
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != SOURCE_SCHEMA_VERSION:
        result['unresolved'].append({'reason': 'source_snapshot_version_unsupported'})
        return result
    if _hash(snapshot) != data['source_snapshot_hash'] or _snapshot_version(snapshot) != data['source_snapshot_version']:
        result.update(status='stale', unresolved=[{'reason': 'source_snapshot_changed'}])
        return result
    original_job = {'sources': snapshot.get('sources', [])}
    for anchor in matches[0]['anchors']:
        reference_id = anchor['reference_id']
        original = generation.resolve_evidence(original_job, reference_id)
        if anchor['status'] != 'resolved' or original is None:
            result['unresolved'].append({'reference_id': reference_id, 'reason': anchor['status']})
            continue
        if _hash(original) != anchor['record_hash']:
            result['unresolved'].append({'reference_id': reference_id, 'reason': 'record_changed'})
            continue
        if anchor['excerpt'] is not None:
            start, end = anchor['offset_start'], anchor['offset_end']
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(original['text'])
                    or original['text'][start:end] != anchor['excerpt']):
                result['unresolved'].append({'reference_id': reference_id, 'reason': 'excerpt_changed'})
                continue
        result['records'].append(original)
    result['status'] = 'partial' if result['records'] and result['unresolved'] else (
        'resolved' if result['records'] else 'unresolved')
    return result


def compact(dossier, item_ids=None):
    """Future transport by refs only; never attached to current paid payloads."""
    data = _dossier(dossier)
    selected = set(item_ids) if item_ids is not None else None
    units = [unit for unit in data['units'] if selected is None or unit['id'] in selected
             or unit['legacy_item_id'] in selected]
    ids = {unit['id'] for unit in units}
    return {'schema_version': SCHEMA_VERSION, 'projection_version': data['projection_version'],
            'source_snapshot_version': data['source_snapshot_version'],
            'source_snapshot_hash': data['source_snapshot_hash'], 'independent_source': False,
            'units': [{'id': unit['id'], 'legacy_item_id': unit['legacy_item_id'], 'type': unit['type'],
                       'classification': unit['classification'], 'origin': unit['origin'],
                       'anchors': [{key: anchor[key] for key in ('reference_id', 'status', 'source_id',
                           'segment_id', 'cue_ids', 'record_hash', 'timing', 'intervals', 'offset_start', 'offset_end')}
                                   for anchor in unit['anchors']]} for unit in units],
            'relations': [{key: relation[key] for key in ('id', 'relation', 'unit_ids', 'status', 'basis')}
                          for relation in data['relations'] if selected is None or (
                              relation['unit_ids'] and set(relation['unit_ids']).issubset(ids))]}
