"""Identify possible body development without certifying its meaning or support."""
import re

from markdown_it import MarkdownIt


def _development_spans(markdown):
    offsets = [0]
    for line in markdown.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt().enable('table').parse(markdown)
    result = []
    for index, token in enumerate(tokens):
        if (token.type != 'inline' or not index or
                tokens[index - 1].type not in ('paragraph_open', 'td_open') or not token.map):
            continue
        visible = ''.join(child.content for child in token.children or []
                          if child.type in ('text', 'code_inline'))
        visible = re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]|\[\d{1,2}:\d{2}(?::\d{2})?\]', '', visible)
        if re.search(r'\w', visible):
            result.append((offsets[token.map[0]], offsets[token.map[1]]))
    return result


def is_body_development(passage):
    """Identify possible prose/list/table development in an isolated block.

    This compatibility gate is only structural; an independent review still
    assesses meaning and source support. Full-article callers must also preserve
    Markdown context with body_development_passages(..., markdown=...).
    """
    return passage.get('field') == 'markdown' and bool(_development_spans(passage.get('text', '')))


def body_development_passages(passages, *, markdown=None):
    if markdown is None:
        return {part['id']: part for part in passages if is_body_development(part)}
    # Blank-line partitioning and length limits can split a code fence, a long
    # heading, or a table. The original Markdown determines which source ranges
    # are prose; parsing the detached fragments cannot recover that context.
    spans = _development_spans(markdown)
    result, cursor = {}, 0
    for part in passages:
        if part.get('field') != 'markdown':
            continue
        text = part.get('text', '')
        start = markdown.find(text, cursor)
        if not text or start < 0:
            raise ValueError('As passagens do corpo precisam corresponder à ordem do Markdown original.')
        end = start + len(text)
        cursor = end
        if is_body_development(part) and any(start < right and end > left for left, right in spans):
            result[part['id']] = part
    return result
