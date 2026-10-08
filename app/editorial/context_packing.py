"""Lossless wire representations; stored articles and citation indexes stay literal."""
import json
import re


def _pack_text(value, choices, reference):
    if not choices or not value:
        return value
    pattern = '|'.join(re.escape(text) for text in sorted(choices, key=len, reverse=True))
    parts, offset = [], 0
    for match in re.finditer(pattern, value):
        if match.start() > offset:
            parts.append(value[offset:match.start()])
        parts.append({reference: choices[match.group()]})
        offset = match.end()
    if offset < len(value):
        parts.append(value[offset:])
    replacement = {'content_parts': parts}
    return (replacement if len(json.dumps(replacement, ensure_ascii=False)) <
            len(json.dumps(value, ensure_ascii=False)) else value)


def pack_article(article, blocks, passages=None):
    """Reference already supplied edit blocks without sending the article twice.

    Whitespace and text outside editable blocks remain inline, including text
    beyond the edit index's size limit. No summarization or truncation is used.
    """
    packed = dict(article)
    for field, value in article.items():
        if not isinstance(value, str) or not value:
            continue
        choices = {block['text']: ident for ident, block in blocks.items()
                   if block['field'] == field and block['text']}
        packed[field] = _pack_text(value, choices, 'edit_block')
        if isinstance(packed[field], str) and passages:
            packed[field] = _pack_text(value, {text: ident for ident, text in passages.items() if text},
                                       'article_passage')
    return packed


def pack_findings(value, blocks, passages):
    """Reuse literal article spans in reports while retaining every finding."""
    if isinstance(value, list):
        return [pack_findings(item, blocks, passages) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'passage' and isinstance(item, str):
            packed = _pack_text(item, {b['text']: ident for ident, b in blocks.items() if b['text']}, 'edit_block')
            if isinstance(packed, str):
                packed = _pack_text(item, {text: ident for ident, text in passages.items() if text}, 'article_passage')
            result[key] = packed
        else:
            result[key] = pack_findings(item, blocks, passages)
    return result


ARTICLE_REFERENCES = '''No artigo e nos pareceres, um campo com content_parts representa o texto completo
em ordem: strings são texto literal; {"edit_block":"bN"} contém exatamente o texto do bloco bN em
equipe_editorial.edit_blocks; {"article_passage":"pN"} contém o texto pN de equipe_editorial.article_passage_refs.
Leia todas as partes nessa ordem como um único artigo. As referências
evitam cópias repetidas; não são conteúdo a escrever. before continua selecionando o ID do bloco original.'''
