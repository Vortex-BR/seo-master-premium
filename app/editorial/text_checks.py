"""Observable delivery defects; these checks do not certify factual or prose quality."""
from difflib import SequenceMatcher
import re

from markdown_it import MarkdownIt


def analyze(job):
    article = job.get('article') or {}
    markdown = article.get('markdown', '')
    tokens = MarkdownIt().parse(markdown)
    paragraphs, visible = [], []
    for index, token in enumerate(tokens):
        if token.type != 'inline':
            continue
        # Keep visible prose, excluding inline code examples from format checks.
        text = ''.join(child.content if child.type == 'text' else '\n'
                       for child in (token.children or []) if child.type in ('text', 'softbreak', 'hardbreak'))
        visible.append(text)
        if index and tokens[index - 1].type == 'paragraph_open':
            paragraphs.append(text)

    def words(text):
        return re.findall(r'\b\w+\b', re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]', '', text))

    count = sum(len(words(text)) for text in visible)
    target = job.get('brief', {}).get('target_words', 0)
    lists = sum(token.type == 'ordered_list_open' for token in tokens)
    headings = [tokens[n + 1].content for n, token in enumerate(tokens) if token.type == 'heading_open']
    numbered_headings = sum(bool(re.match(r'^(?:Passo\s+|Etapa\s+)?\d+[.):\s-]+\S', heading, re.I))
                            for heading in headings)
    findings = []

    def add(code, severity, reason, suggestion, passage=''):
        findings.append({'code': code, 'severity': severity, 'passage': passage,
                         'reason': reason, 'suggestion': suggestion, 'source_ids': [],
                         'recipient': 'writing', 'origin': 'editorial_delivery'})

    if target and count > max(target * 1.35, target + 200):
        add('length_overrun', 'warning', f'O texto tem {count} palavras para uma meta de {target}.',
            'Reduza repetições e detalhes fora do foco; preserve a resposta, as evidências e as ressalvas essenciais.')
    if job.get('brief', {}).get('genre') == 'tutorial' and not lists and numbered_headings < 2:
        add('tutorial_sequence', 'blocking', 'A pauta solicita um tutorial, mas o artigo não apresenta etapas numeradas.',
            'Organize as ações sustentadas pelas fontes em uma sequência numerada legível. A numeração, sozinha, não comprova que o texto ensina a tarefa.')
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
            'numbered_headings': numbered_headings, 'findings': findings,
            'notice': 'Diagnóstico local da entrega; não substitui a conferência das fontes nem a avaliação do significado.'}
