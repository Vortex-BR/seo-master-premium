"""Lossless source partitioning and observable transcription limitations."""
import math
import re
from collections import Counter

from .. import generation


def confidence_source_ids(source):
    result = set()
    for warning in source.get('transcription_warnings', []):
        start = warning.get('start')
        if not isinstance(start, (int, float)):
            continue
        for segment in source.get('segments', []):
            if isinstance(segment.get('start'), (int, float)) and isinstance(segment.get('end'), (int, float)):
                if segment['start'] <= start <= segment['end']:
                    result.add(segment['id'])
    return sorted(result)


def quality(source):
    segments = source.get('segments', [])
    warnings = []
    confidence = source.get('transcription_warnings', [])
    if confidence:
        warnings.append(f'{len(confidence)} trecho(s) de áudio com baixa confiança; nomes, números e termos precisam de conferência no áudio original.')
    if source.get('input_origin') == 'uploaded_audio':
        warnings.append('Áudio enviado pelo usuário; o vínculo com o vídeo de referência não foi verificado.')
    texts = [generation.normalize(s.get('text', '')) for s in segments]
    counts = Counter(text for text in texts if text)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    if duplicates:
        warnings.append(f'{duplicates} trecho(s) repetido(s); preservados para conferência e deduplicação editorial.')
    if any(not text for text in texts):
        warnings.append('Há trechos vazios na transcrição.')
    times = [s['start'] for s in segments if isinstance(s.get('start'), (int, float))]
    if any(b < a for a, b in zip(times, times[1:])) or any(t < 0 or not math.isfinite(t) for t in times):
        warnings.append('Timestamps inválidos ou fora de ordem; confira a sequência da fala.')
    for left, right in zip(segments, segments[1:]):
        if isinstance(left.get('end'), (int, float)) and isinstance(right.get('start'), (int, float)):
            if right['start'] - left['end'] > 90:
                warnings.append('Intervalo de mais de 90 segundos sem fala transcrita; completude não confirmada.')
                break
    if any('\ufffd' in s.get('text', '') or re.search(r'\?{3,}', s.get('text', '')) for s in segments):
        warnings.append('Caracteres possivelmente corrompidos ou fala ambígua; não corrigir por suposição.')
    return {'language': source.get('language') or 'não informado', 'provider': source.get('provider') or 'não informado',
            'generated_captions': source.get('generated_captions'), 'extracted_at': source.get('extracted_at'),
            'medium': source.get('medium', 'text'), 'transcription_model': source.get('transcription_model'),
            'low_confidence': confidence,
            'timestamps': 'all' if segments and len(times) == len(segments) else 'partial' if times else 'unavailable',
            'completeness': 'unverified', 'warnings': warnings,
            'notice': 'Análise textual. Demonstrações, gráficos e dados exibidos apenas na tela não foram analisados.'}


def parts(text, limit):
    """Return exact, contiguous source spans, favoring sentence boundaries."""
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            choices = list(re.finditer(r'(?<=[.!?])\s+|\n+', text[start:end]))
            boundary = next((m.end() for m in reversed(choices) if m.end() >= limit // 2), None)
            if boundary is None:
                boundary = text.rfind(' ', start + limit // 2, end) - start + 1
            if boundary and boundary > 0:
                end = start + boundary
        yield start, end, text[start:end]
        start = end


def blocks(source, limit=7000, overlap=700):
    units = []
    for segment in source.get('segments', []):
        for start, end, text in parts(segment['text'], limit):
            units.append({'source_id': segment['id'], 'offset_start': start, 'offset_end': end, 'text': text})
    groups, current, size = [], [], 0
    for unit in units:
        if current and (size + len(unit['text']) > limit or len(current) >= 30):
            groups.append(current)
            current, size = [], 0
        current.append(unit)
        size += len(unit['text'])
    if current:
        groups.append(current)
    result = []
    for index, owned in enumerate(groups):
        # Adjacent source spans are context only, never new extraction ownership.
        surroundings = []
        if index:
            prior = groups[index - 1][-1]
            surroundings.append({**prior, 'text': prior['text'][-overlap:]})
        if index + 1 < len(groups):
            following = groups[index + 1][0]
            surroundings.append({**following, 'text': following['text'][:overlap]})
        result.append({'id': f'{source["id"]}b{index + 1}', 'video_id': source['id'], 'index': index + 1,
                       'owned': owned, 'surroundings': surroundings, 'status': 'pending',
                       'input_hash': generation.article_hash({'owned': owned, 'surroundings': surroundings})})
    return result


def inventory(job, profile):
    all_blocks, sources = [], []
    identifiers = []
    total = 0
    for source in job.get('sources', []):
        count = sum(len(s['text']) for s in source.get('segments', []))
        if source.get('status') != 'ok' or not count:
            raise ValueError('Todas as fontes precisam ter transcrição disponível antes da apuração.')
        if count > 120000:
            raise ValueError('Um vídeo excede 120 mil caracteres. Divida o material antes da apuração.')
        total += count
        identifiers.extend(s['id'] for s in source['segments'])
        grouped = blocks(source, block_limit(profile))
        all_blocks.extend(grouped)
        sources.append({'id': source['id'], 'title': source.get('title', ''), 'author': source.get('author', ''),
                        'url': source['url'], 'characters': count, 'blocks': len(grouped), 'quality': quality(source)})
    if not 1 <= len(sources) <= 5 or total > 180000:
        raise ValueError('Use até cinco vídeos, com no máximo 180 mil caracteres no conjunto.')
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Há IDs de evidência duplicados entre os vídeos. Refaça a extração.')
    return {'sources': sources, 'blocks': all_blocks, 'characters': total}


def block_limit(profile):
    # Keep room for scope, instructions, response and the duplicated literal evidence.
    return min(profile['block_chars'], max(1000, (profile['context_chars'] - 24000) // 3))


def estimate(job, profile):
    inv = inventory(job, profile)
    nblocks, nvideos = len(inv['blocks']), len(inv['sources'])
    # Topic count is only known after extraction. Show a range, never a cost promise.
    minimum = nblocks * 2 + nvideos + 1 + 2 + 1 + 1 + 10
    return {'blocks': nblocks, 'videos': nvideos, 'characters': inv['characters'],
            'estimated_calls_min': minimum, 'estimated_calls_max': minimum + nblocks * 4 + 12,
            'max_calls': profile['max_calls'], 'review_reserve': 6,
            'fits_minimum': minimum <= profile['max_calls'],
            'notice': 'Estimativa de chamadas; assuntos, pesquisa, lotes de revisão e correções podem ampliar o consumo. Sem preço monetário fixo.'}
