"""Observable delivery defects; these checks do not certify factual or prose quality."""
from difflib import SequenceMatcher
import re

from markdown_it import MarkdownIt


def analyze(job):
    article = job.get('article') or {}
    markdown = article.get('markdown', '')
    tokens = MarkdownIt().parse(markdown)
    paragraphs, visible, list_text = [], [], []
    list_depth = 0
    for index, token in enumerate(tokens):
        if token.type in ('bullet_list_open', 'ordered_list_open'):
            list_depth += 1
        elif token.type in ('bullet_list_close', 'ordered_list_close'):
            list_depth -= 1
        if token.type != 'inline':
            continue
        # Keep visible prose, excluding inline code examples from format checks.
        text = ''.join(child.content if child.type == 'text' else '\n'
                       for child in (token.children or []) if child.type in ('text', 'softbreak', 'hardbreak'))
        visible.append(text)
        if list_depth:
            list_text.append(text)
        if index and tokens[index - 1].type == 'paragraph_open':
            paragraphs.append(text)

    def words(text):
        return re.findall(r'\b\w+\b', re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]', '', text))

    count = sum(len(words(text)) for text in visible)
    target = job.get('brief', {}).get('target_words', 0)
    lists = sum(token.type == 'ordered_list_open' for token in tokens)
    bullets = sum(token.type == 'bullet_list_open' for token in tokens)
    list_words = sum(len(words(text)) for text in list_text)
    list_ratio = list_words / max(1, count)
    headings = [tokens[n + 1].content for n, token in enumerate(tokens) if token.type == 'heading_open']
    numbered_headings = sum(bool(re.match(r'^(?:Passo\s+|Etapa\s+)?\d+[.):\s-]+\S', heading, re.I))
                            for heading in headings)
    findings = []

    def add(code, severity, reason, suggestion, passage=''):
        findings.append({'code': code, 'severity': severity, 'passage': passage,
                         'reason': reason, 'suggestion': suggestion, 'source_ids': [],
                         'recipient': 'writing', 'origin': 'editorial_delivery',
                         'category': 'objective' if code in ('escaped_paragraphs', 'undefined_footnotes')
                                     else 'recommendation',
                         'export_blocking': False, 'auto_repair': False})

    if target and count > max(target * 1.35, target + 200):
        add('length_overrun', 'warning', f'O texto tem {count} palavras para uma meta de {target}.',
            'Reduza repetições e detalhes fora do foco; preserve a resposta, as evidências e as ressalvas essenciais.')
    journey = ((job.get('plan') or {}).get('data') or {}).get('reader_journey') or {}
    sequential = job.get('brief', {}).get('genre') == 'tutorial' or journey.get('kind') == 'sequencial'
    if sequential and not lists and numbered_headings < 2:
        add('tutorial_sequence', 'blocking', 'A pauta solicita um tutorial, mas o artigo não apresenta etapas numeradas.',
            'Organize as ações sustentadas pelas fontes em uma sequência numerada legível. A numeração, sozinha, não comprova que o texto ensina a tarefa.')
    if count >= 300 and bullets >= 3 and list_ratio > .65:
        add('list_dominance', 'warning',
            f'{list_ratio:.0%} das palavras estão em listas, distribuídas em {bullets} listas com marcadores.',
            'Confira se a pauta pede um checklist. Se precisa ensinar ou explicar, desenvolva as ideias em '
            'parágrafos e use H3 para etapas que exigem explicação. Preserve listas úteis de materiais ou '
            'verificações; não substitua marcadores por um parágrafo gigante nem acrescente texto de preenchimento.')
        findings[-1]['auto_repair'] = False  # A legitimate checklist needs human/model judgment.
    prior_level = 1
    for index, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        level = int(token.tag[1:])
        if level == 1 or level > prior_level + 1:
            add('heading_hierarchy', 'warning', 'A hierarquia de títulos do corpo precisa de conferência.',
                'O título do artigo já é H1. Use H2 para seções e H3 para subdivisões da seção anterior; '
                'não escolha a tag apenas pelo tamanho visual.', tokens[index + 1].content)
            findings[-1]['auto_repair'] = False
            break
        prior_level = level
    long_paragraph = next((text for text in paragraphs if len(words(text)) > 180), None)
    if long_paragraph:
        add('long_paragraph', 'warning', 'Há um parágrafo com mais de 180 palavras.',
            'Confira se ele acumula assuntos ou ações; organize a leitura preservando a ligação entre as ideias.',
            long_paragraph[:150])
    comparable = []
    for text in paragraphs:
        normalized = [word.casefold() for word in words(text)]
        if len(normalized) < 80:
            continue
        for previous in comparable:
            if abs(len(normalized) - len(previous)) > max(len(previous), len(normalized)) * .05:
                continue
            if SequenceMatcher(None, previous, normalized, autojunk=False).ratio() >= .95:
                add('duplicate_paragraph', 'blocking', 'Dois parágrafos extensos repetem praticamente a mesma redação.',
                    'Mantenha uma única explicação e preserve as informações exclusivas de cada trecho.', text[:150])
                break
        else:
            comparable.append(normalized)
            continue
        break
    prose = '\n'.join(visible)
    if re.search(r'\\n\s*\\n', prose):
        add('escaped_paragraphs', 'blocking', 'O corpo contém quebras de parágrafo escapadas como texto.',
            'Entregue parágrafos Markdown reais, sem códigos de serialização no texto para o leitor.')
    footnotes = set(re.findall(r'\[\^([^\]]+)\]', prose))
    defined = set(re.findall(r'^\s*\[\^([^\]]+)\]:', markdown, re.M))
    if footnotes - defined:
        add('undefined_footnotes', 'blocking', 'O corpo contém referências de rodapé sem definição.',
            'Confira a procedência das citações e use o formato rastreável do artigo; IDs internos não devem aparecer como notas quebradas.')
    return {'word_count': count, 'target_words': target, 'ordered_lists': lists,
            'bullet_lists': bullets, 'list_items': sum(t.type == 'list_item_open' for t in tokens),
            'list_word_ratio': round(list_ratio, 4),
            'h3_headings': sum(t.type == 'heading_open' and t.tag == 'h3' for t in tokens),
            'numbered_headings': numbered_headings, 'findings': findings,
            'notice': 'Diagnóstico local e contextual da entrega; não autoriza nem bloqueia a exportação. '
                      'Comprimento, numeração e semelhança de redação não comprovam inadequação do artigo.'}
