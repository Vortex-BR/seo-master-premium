"""Deterministic, lossless transcript ingestion; no network or model inference."""
from copy import deepcopy
from html.parser import HTMLParser
import math
import re


NORMALIZATION_VERSION = 'source-integrity-v1'
MAX_CONTIGUOUS_GAP = .05  # Provider rounding, never a material silence.
SEGMENT_CHARS = 900
_HTML_TAGS = frozenset(('a abbr address area article aside audio b base bdi bdo blockquote '
    'body br button canvas caption center cite code col colgroup data datalist dd del '
    'details dfn dialog div dl dt em embed fieldset figcaption figure footer form '
    'h1 h2 h3 h4 h5 h6 head header hr html i iframe img input ins kbd label legend '
    'li link main map mark menu meta meter nav noscript object ol optgroup option '
    'output p picture pre progress q rp rt ruby s samp script search section select '
    'slot small source span strong style sub summary sup table tbody td template '
    'textarea tfoot th thead time title tr track u ul var video wbr v lang c').split())
_INLINE_TIME = re.compile(r'<(?:\d+:)?\d{2}:\d{2}[.,]\d{3}>')
_BLOCK_TAGS = frozenset('address article aside blockquote div dl dt dd figure figcaption '
                      'footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre '
                      'section table tbody td tfoot th thead tr ul br'.split())
_BOOLEAN_ATTRIBUTES = frozenset(('allowfullscreen async autofocus autoplay checked controls '
    'default defer disabled download formnovalidate hidden inert ismap itemscope loop '
    'multiple muted nomodule novalidate open playsinline readonly required reversed selected').split())
_STAMP = r'(?:\d+:)?\d{2}:\d{2}[.,]\d{1,3}'
_TIMING = re.compile(r'^\s*(' + _STAMP + r')\s*-->\s*(' + _STAMP + r')(?:\s+(.*))?\s*$')


class IntegrityError(ValueError):
    """An explicit ingestion failure, converted to SourceError by public adapters."""
    def __init__(self, message, *, code='invalid_response'):
        super().__init__(message)
        self.code = code


class _TranscriptText(HTMLParser):
    """Remove actual known tags while leaving technical angle-bracket text inert."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.original = text
        self.line_offsets = [0] + [match.end() for match in re.finditer('\n', text)]
        self.literal_tags = {}

    @staticmethod
    def _known_tag(tag):
        return tag in _HTML_TAGS or tag.split('.', 1)[0] in ('c', 'v', 'lang', 'ruby', 'rt', 'b', 'i', 'u')

    def _known_markup(self, tag, attrs):
        if not self._known_tag(tag):
            return False
        # Voice/language annotations are valid WebVTT tag syntax, not HTML
        # boolean attributes. Other bare words ('e', 'b', '&&') often belong to
        # mathematical comparisons; ambiguous markup stays as inert text.
        if tag.split('.', 1)[0] in ('v', 'lang'):
            return True
        return all(re.fullmatch(r'[a-z_:][a-z0-9_.:-]*', name)
                   and (value is not None or name in _BOOLEAN_ATTRIBUTES) for name, value in attrs)

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if not self._known_markup(tag, attrs):
            self.parts.append(self.get_starttag_text())
            self.literal_tags[tag] = self.literal_tags.get(tag, 0) + 1
        elif tag in _BLOCK_TAGS and self.parts and not self.parts[-1][-1:].isspace():
            self.parts.append(' ')

    def handle_startendtag(self, tag, attrs):
        literal_count = self.literal_tags.get(tag, 0)
        self.handle_starttag(tag, attrs)
        self.literal_tags[tag] = literal_count

    def handle_endtag(self, tag):
        literal = self.literal_tags.get(tag, 0)
        if literal:
            self.literal_tags[tag] = literal - 1
        if not self._known_tag(tag) or literal:
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            end = self.original.find('>', start)
            self.parts.append(self.original[start:end + 1])
        elif tag in _BLOCK_TAGS and self.parts and not self.parts[-1][-1:].isspace():
            self.parts.append(' ')

    def handle_entityref(self, name):
        # HTMLParser leaves entities in script/style raw-text nodes. They are
        # source data here and never emitted as executable HTML.
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')


def clean_transcript_text(text):
    """Decode entities once; output is plain text, still escaped by renderers."""
    marked = _INLINE_TIME.sub('', text)
    parser = _TranscriptText(marked)
    parser.feed(marked)
    parser.close()
    # HTMLParser buffers an unclosed script/style raw-text node even on close().
    # Retain that speech as inert text instead of silently dropping the tail.
    return (''.join(parser.parts) + parser.rawdata).strip()


def _seconds(value):
    whole, fraction = re.split(r'[.,]', value)
    fields = whole.split(':')
    try:
        hours, minutes, seconds = ([0] + [int(part) for part in fields]
                                   if len(fields) == 2 else [int(part) for part in fields])
        result = hours * 3600 + minutes * 60 + seconds + int(fraction) / 10 ** len(fraction)
    except (ValueError, OverflowError):
        raise IntegrityError('A transcrição contém um timestamp inválido.') from None
    if minutes >= 60 or seconds >= 60:
        raise IntegrityError('A transcrição contém um timestamp inválido.')
    return _time(result)


def _warning(code, reason, **metadata):
    return {'code': code, 'reason': reason, **metadata}


def _voice_attribution(content):
    """A voice annotation proves only the speech it actually covers."""
    labels, active, previous = set(), None, 0
    for marker in re.finditer(r'<v(?:\.[^\s>]*)?\s+([^>]+)>|</v\s*>', content, re.IGNORECASE):
        if clean_transcript_text(content[previous:marker.start()]):
            labels.add(active)
        active = marker[1].strip() if marker[1] else None
        previous = marker.end()
    if clean_transcript_text(content[previous:]):
        labels.add(active)
    voices = labels - {None}
    if len(voices) == 1 and None not in labels:
        return next(iter(voices)), []
    if len(voices) > 1:
        return None, [_warning('multiple_speakers',
                              'A cue contém mais de uma voz; não foi atribuída a um único locutor.')]
    if voices:
        return None, [_warning('partial_speaker_attribution',
                              'A anotação de voz não cobre toda a cue; o locutor permaneceu desconhecido.')]
    return None, []


def parse_manual_rows(text):
    """Keep SRT/VTT ends, identifiers, unknown timing and subtitle diagnostics.

    A malformed arrow is an explicit error, rather than a silent fallback that
    confuses time codes with speech. Orphan speech in mixed documents is retained
    with unknown timing. WEBVTT metadata blocks contain no speech and are skipped.
    """
    if not isinstance(text, str):
        raise IntegrityError('Informe uma transcrição em texto.')
    normalized = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    webvtt = bool(re.match(r'^WEBVTT(?:\s|$)', normalized))
    lines_all = normalized.splitlines()
    structured = webvtt or any('-->' in line and (
        re.match(r'^\s*\d+:', line) or re.search(r'-->\s*\d+:', line)
        or index and lines_all[index - 1].strip().isdigit())
        for index, line in enumerate(lines_all))
    if not structured:
        return [{'text': text, 'start': None, 'duration': None}]
    rows = []
    for block in re.split(r'\n[ \t]*\n', normalized):
        lines = block.splitlines()
        if not lines or not block.strip():
            continue
        if re.match(r'^WEBVTT(?:\s|$)', lines[0]):
            lines = lines[1:]
            # Header metadata is not cue speech. Tolerate a missing blank line
            # only when an actual arrow starts a cue in the same block.
            arrow = next((i for i, line in enumerate(lines) if '-->' in line), None)
            if arrow is None:
                continue
            lines = lines[max(0, arrow - 1):] if arrow else lines
        if webvtt and lines and re.match(r'^(?:NOTE(?:\s|$)|STYLE\s*$|REGION\s*$)', lines[0]):
            continue
        arrows = [i for i, line in enumerate(lines) if '-->' in line]
        if len(arrows) > 1:
            raise IntegrityError('Separe as cues de legenda com uma linha em branco.')
        if not arrows:
            identifier = lines.pop(0) if len(lines) > 1 and lines[0].strip().isdigit() else None
            content = '\n'.join(lines)
            if content.strip():
                rows.append({'text': content, 'start': None, 'duration': None,
                             'original_id': identifier, 'normalization_warnings': [_warning(
                                 'missing_timestamps', 'Cue sem intervalo; o texto foi preservado sem inventar tempos.')]})
            continue
        index = arrows[0]
        match = _TIMING.fullmatch(lines[index])
        if not match:
            raise IntegrityError('A transcrição contém um intervalo de legenda inválido ou incompleto.')
        start, end = _seconds(match[1]), _seconds(match[2])
        if end < start:
            raise IntegrityError('O fim de uma cue é anterior ao início.')
        content = '\n'.join(lines[index + 1:])
        warnings = []
        if not content.strip():
            raise IntegrityError('Uma cue de legenda está sem texto.')
        if end == start:
            warnings.append(_warning('zero_duration', 'Cue com duração zero; o intervalo original foi preservado.'))
        if rows and isinstance(rows[-1].get('start'), (int, float)):
            previous = rows[-1]
            if start < previous['start']:
                warnings.append(_warning('out_of_order', 'Cues fora da ordem temporal; a ordem original foi preservada.'))
            if previous.get('end') is not None and start < previous['end'] and end > previous['start']:
                warnings.append(_warning('overlapping_cues', 'Cues sobrepostas; os intervalos originais foram preservados.'))
        speaker, voice_warnings = _voice_attribution(content)
        warnings.extend(voice_warnings)
        rows.append({'text': content, 'start': start, 'end': end, 'duration': end - start,
                     'original_id': '\n'.join(lines[:index]).strip() or None,
                     'settings': match[3].strip() if match[3] else None,
                     'speaker': speaker, 'normalization_warnings': warnings})
    if not rows:
        raise IntegrityError('A legenda não contém cues de fala.')
    return rows


def _time(value):
    if value is None:
        return None
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise IntegrityError('O provedor retornou timestamps inválidos.')
    return value


def _cue(row, index, prefix):
    if not isinstance(row, dict) or not isinstance(row.get('text'), str):
        raise IntegrityError('O provedor retornou trechos inválidos.')
    text = clean_transcript_text(row['text'])
    start, duration = _time(row.get('start')), _time(row.get('duration'))
    end = _time(row.get('end'))
    if end is None and start is not None and duration is not None:
        end = _time(start + duration)
    if start is not None and end is not None:
        if end < start or duration is not None and not math.isclose(end - start, duration, rel_tol=1e-7, abs_tol=.001):
            raise IntegrityError('O provedor retornou intervalos inconsistentes.')
        duration = end - start
    elif duration is None or start is None:
        # A duration without a known origin is retained in the cue only; it
        # cannot establish a located interval in the video.
        duration = row.get('duration') if row.get('duration') is not None else None
    original_id = row.get('original_id', row.get('cue_id', row.get('id')))
    cue = {'id': f'{prefix}c{index}', 'original_id': deepcopy(original_id),
           'original_text': row['text'], 'text': text,
           'start': start, 'end': end, 'duration': duration,
           'speaker': deepcopy(row.get('speaker', row.get('speaker_id'))),
           'confidence': deepcopy(row.get('confidence'))}
    for key in ('settings', 'origin', 'language', 'quality', 'speaker_id', 'normalization_warnings'):
        if key in row:
            cue[key] = deepcopy(row[key])
    cue.setdefault('normalization_warnings', [])
    if not isinstance(cue['normalization_warnings'], list) or any(not isinstance(item, dict)
                                                               for item in cue['normalization_warnings']):
        raise IntegrityError('O provedor retornou avisos de normalização inválidos.')
    return cue


def _joinable(left, right):
    if left['start'] is None or left['end'] is None or right['start'] is None or right['end'] is None:
        return False
    if left['end'] <= left['start'] or right['end'] <= right['start']:
        return False
    gap = right['start'] - left['end']
    if gap < 0 or gap > MAX_CONTIGUOUS_GAP + 1e-9:
        return False
    return all(left.get(key) == right.get(key) for key in
               ('speaker', 'speaker_id', 'confidence', 'origin', 'language', 'quality', 'normalization_warnings'))


def normalize_rows(rows, prefix, *, merge_adjacent=True):
    """Retain raw cues locally; group only compatible continuous intervals."""
    try:
        rows = iter(rows)
    except TypeError:
        raise IntegrityError('O provedor retornou trechos inválidos.') from None
    segments, current, characters, previous, current_chars = [], [], 0, None, 0

    def flush():
        nonlocal current, current_chars
        if not current:
            return
        start, end = current[0]['start'], current[-1]['end']
        warnings = [deepcopy(item) for cue in current for item in cue.get('normalization_warnings', [])]
        intervals = [{'start': cue['start'], 'end': cue['end']} for cue in current
                     if cue['start'] is not None and cue['end'] is not None]
        segments.append({'id': f'{prefix}s{len(segments) + 1}', 'text': ' '.join(c['text'] for c in current),
            'start': start, 'end': end, 'duration': end - start if start is not None and end is not None else None,
            'speaker': deepcopy(current[0]['speaker']), 'confidence': deepcopy(current[0]['confidence']),
            'normalization_version': NORMALIZATION_VERSION, 'cue_ids': [c['id'] for c in current],
            'intervals': intervals, 'original_cues': current, 'normalization_warnings': warnings})
        current = []
        current_chars = 0

    for index, row in enumerate(rows, 1):
        cue = _cue(row, index, prefix)
        if not cue['text']:
            continue
        diagnostics = []
        if cue['start'] is not None and cue['end'] is not None and cue['start'] == cue['end']:
            diagnostics.append(_warning('zero_duration', 'Cue com duração zero; o intervalo original foi preservado.'))
        if previous is not None and previous['start'] is not None and cue['start'] is not None:
            if cue['start'] < previous['start']:
                diagnostics.append(_warning('out_of_order', 'Cues fora da ordem temporal; a ordem original foi preservada.'))
            if (previous['end'] is not None and cue['end'] is not None
                    and cue['start'] < previous['end'] and cue['end'] > previous['start']):
                diagnostics.append(_warning('overlapping_cues', 'Cues sobrepostas; os intervalos originais foram preservados.'))
        codes = {warning.get('code') for warning in cue['normalization_warnings']}
        cue['normalization_warnings'].extend(warning for warning in diagnostics if warning['code'] not in codes)
        previous = cue
        characters += len(cue['text'])
        if characters > 120000:
            raise IntegrityError('Este vídeo excede o limite de 120 mil caracteres. Use um vídeo mais curto.', code='source_limit')
        if current and (not merge_adjacent or not _joinable(current[-1], cue)
                        or current_chars >= SEGMENT_CHARS):
            flush()
        current_chars += len(cue['text']) + bool(current)
        current.append(cue)
    flush()
    if sum(len(segment['text']) for segment in segments) < 80:
        raise IntegrityError('O vídeo não contém fala suficiente para fundamentar um artigo.', code='insufficient_speech')
    if sum(len(segment['text']) for segment in segments) > 120000:
        raise IntegrityError('Este vídeo excede o limite de 120 mil caracteres. Use um vídeo mais curto.', code='source_limit')
    return segments
