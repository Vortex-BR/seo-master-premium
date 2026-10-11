"""Observable delivery defects; these checks do not certify factual or prose quality."""
from difflib import SequenceMatcher
import re
import unicodedata

from markdown_it import MarkdownIt


def _heading_key(text):
    """Compare visible headings without requiring the plan's exact wording."""
    text = re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]', '', text)
    text = re.sub(r'^\s*(?:#{1,6}\s*)?(?:(?:passo|etapa|step)\s+)?\d+[.):\s–—-]+', '', text, flags=re.I)
    plain = ''.join(char for char in unicodedata.normalize('NFKD', text.casefold())
                    if not unicodedata.combining(char))
    return re.findall(r'\b\w+\b', plain)


def _headings_match(planned, delivered):
    expected, actual = _heading_key(planned), _heading_key(delivered)
    if expected == actual:
        return True
    stopwords = {'a', 'o', 'as', 'os', 'de', 'da', 'do', 'das', 'dos', 'e', 'em',
                 'um', 'uma', 'para', 'por', 'com', 'no', 'na', 'nos', 'nas'}
    expected = [word for word in expected if word not in stopwords]
    actual = [word for word in actual if word not in stopwords]
    if not expected or not actual:
        return False
    shorter, longer = sorted((expected, actual), key=len)
    return all(any(word == candidate or (min(len(word), len(candidate)) >= 5 and
                   SequenceMatcher(None, word, candidate).ratio() >= .84)
                   for candidate in longer) for word in shorter)


def _repeats_step(text, title):
    actual, expected = _heading_key(text), _heading_key(title)
    if actual == expected:
        return True
    if not actual or len(actual) != len(expected) or actual[1:] != expected[1:]:
        return False
    # A plain infinitive/imperative change ("apertar" / "aperte") adds no
    # development. Do not compare longer explanations or judge their semantics.
    root = lambda word: re.sub(r'(?:ar|er|ir|a|e)$', '', word)
    return len(root(actual[0])) >= 3 and root(actual[0]) == root(expected[0])


def _locate_subheadings(expected, delivered):
    found, available = {}, list(range(len(delivered)))
    for number, title in enumerate(expected):
        match = next((index for index in available if _headings_match(title, delivered[index]['title'])), None)
        if match is not None:
            found[number] = delivered[match]
            available.remove(match)
    for number in range(len(expected)):
        if number not in found and available:
            found[number] = delivered[available.pop(0)]
    return found


def _planned_sections(plan, headings, markdown, items):
    """Locate planned sections; renamed headings may retain their order or evidence.

    An unmatched title alone is not a delivery defect. The independent editorial
    review determines whether a reformulated section still answers its question.
    """
    sections = [section for section in plan.get('sections', []) if isinstance(section, dict)]
    h2 = [heading for heading in headings if heading['level'] == 2]
    found, available = {}, list(range(len(h2)))
    for number, section in enumerate(sections):
        match = next((index for index in available if
                      _headings_match(section.get('title', ''), h2[index]['title'])), None)
        if match is not None:
            found[number] = h2[match]
            available.remove(match)
    item_sources = {item.get('id'): {e.get('source_id') for e in item.get('evidence', [])}
                    for item in items if isinstance(item, dict)}
    section_sources = [{source for ident in section.get('item_ids', [])
                        for source in item_sources.get(ident, set()) if source}
                       for section in sections]
    for number, section in enumerate(sections):
        if number in found:
            continue
        exclusive = section_sources[number] - set().union(
            *(sources for index, sources in enumerate(section_sources) if index != number))
        match = next((index for index in available if exclusive & set(re.findall(
            r'\[\[([\w-]+)\]\]', markdown[h2[index]['source_start']:h2[index]['source_end']]))), None)
        if match is not None:
            found[number] = h2[match]
            available.remove(match)
    # When wording and citation placement change, use the remaining section order
    # rather than inventing a semantic failure from lexical differences.
    for number in range(len(sections)):
        if number not in found and available:
            found[number] = h2[available.pop(0)]
    return sections, found


def analyze(job):
    article = job.get('article') or {}
    markdown = article.get('markdown', '')
    tokens = MarkdownIt().parse(markdown)
    paragraphs, visible, list_text, prose_blocks, list_blocks, content_blocks, headings = [], [], [], [], [], [], []
    source_lines = markdown.splitlines(keepends=True)
    source_offsets = [0]
    for line in source_lines:
        source_offsets.append(source_offsets[-1] + len(line))
    list_depth = 0
    for index, token in enumerate(tokens):
        if token.type in ('bullet_list_open', 'ordered_list_open'):
            list_depth += 1
        elif token.type in ('bullet_list_close', 'ordered_list_close'):
            list_depth -= 1
        if token.type == 'heading_open':
            headings.append({'level': int(token.tag[1:]), 'title': tokens[index + 1].content,
                             'start': index, 'end': len(tokens),
                             'source_start': source_offsets[token.map[0]], 'source_end': len(markdown)})
        if token.type in ('fence', 'code_block', 'html_block') and token.content.strip():
            content_blocks.append((index, token.content))
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
            meaningful_text = re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]', '', text).strip()
            if meaningful_text or any(child.type == 'image' for child in (token.children or [])):
                content_blocks.append((index, text))
            if list_depth and meaningful_text:
                list_blocks.append((index, text))
            elif meaningful_text:
                prose_blocks.append((index, text))
    for number, heading in enumerate(headings):
        following = next((next_heading for next_heading in headings[number + 1:]
                          if next_heading['level'] <= heading['level']), None)
        if following:
            heading['end'], heading['source_end'] = following['start'], following['source_start']
        heading['prose'] = [text for index, text in prose_blocks if heading['start'] < index < heading['end']]
        heading['list_prose'] = [text for index, text in list_blocks if heading['start'] < index < heading['end']]
        heading['content'] = [text for index, text in content_blocks if heading['start'] < index < heading['end']]

    def words(text):
        return re.findall(r'\b\w+\b', re.sub(r'\[\[[\w-]+\]\]|\[\^[^\]]+\]', '', text))

    count = sum(len(words(text)) for text in visible)
    target = job.get('brief', {}).get('target_words', 0)
    lists = sum(token.type == 'ordered_list_open' for token in tokens)
    bullets = sum(token.type == 'bullet_list_open' for token in tokens)
    list_words = sum(len(words(text)) for text in list_text)
    list_ratio = list_words / max(1, count)
    numbered_headings = sum(bool(re.match(r'^(?:Passo\s+|Etapa\s+)?\d+[.):\s-]+\S', heading, re.I))
                            for heading in (entry['title'] for entry in headings))
    findings = []

    def add(code, severity, reason, suggestion, passage='', auto_repair=False):
        findings.append({'code': code, 'severity': severity, 'passage': passage,
                         'reason': reason, 'suggestion': suggestion, 'source_ids': [],
                         'recipient': 'writing', 'origin': 'editorial_delivery',
                         'category': 'objective' if code in ('escaped_paragraphs', 'undefined_footnotes')
                                     else 'recommendation',
                         'export_blocking': False, 'auto_repair': auto_repair})

    if target and count > max(target * 1.35, target + 200):
        add('length_overrun', 'warning', f'O texto tem {count} palavras para uma meta de {target}.',
            'Reduza repetições e detalhes fora do foco; preserve a resposta, as evidências e as ressalvas essenciais.')
    plan = ((job.get('plan') or {}).get('data') or {})
    journey = plan.get('reader_journey') or {}
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
    body_h1 = next((heading for heading in headings if heading['level'] == 1), None)
    if body_h1:
        add('body_h1', 'warning', 'O corpo repete o nível H1 reservado ao título do artigo.',
            'Mantenha o título no campo title. Retire sua repetição do corpo; se este título delimita '
            'uma seção distinta, ajuste-o para H2 e preserve o conteúdo sustentado pelas fontes.',
            body_h1['title'], auto_repair=True)
    elif article.get('title') and headings and _heading_key(headings[0]['title']) == _heading_key(article['title']):
        add('duplicate_article_title', 'warning', 'O primeiro subtítulo repete o título já renderizado do artigo.',
            'Retire a repetição do título ou dê à seção um subtítulo que descreva sua função própria; '
            'preserve os parágrafos e as evidências existentes.', headings[0]['title'], auto_repair=True)

    sections, located = _planned_sections(plan, headings, markdown,
                                          (job.get('apuration') or {}).get('items', []))
    developed_steps = set()
    for number, section in enumerate(sections):
        presentation = section.get('presentation')
        if not isinstance(presentation, dict):
            continue  # A legacy plan has not made an explicit format decision.
        heading = located.get(number)
        if not heading:
            add('planned_section_missing', 'warning',
                'O corpo não tem uma seção H2 para uma parte prevista no plano.',
                f"Desenvolva a função de '{section.get('title', '')}' com os itens selecionados no plano, "
                'permitindo reformular o título. Use apenas conteúdo das fontes; se a seção deixou de ser '
                'necessária, ajuste o plano de modo explícito.', section.get('title', ''), auto_repair=True)
            continue
        children = [child for child in headings if child['level'] == 3 and
                    heading['start'] < child['start'] < heading['end']]
        expected = presentation.get('subheadings') or []
        located_children = _locate_subheadings(expected, children)
        if len(children) < len(expected):
            missing = [title for title in expected if not any(
                _headings_match(title, child['title']) for child in children)]
            add('planned_subheading_missing', 'warning',
                'A seção não entrega todas as subdivisões com desenvolvimento próprio previstas no plano.',
                'Recupere as funções das subdivisões previstas nesta seção: ' + '; '.join(missing or expected) +
                '. Os títulos podem ser reformulados. Desenvolva somente o que as fontes sustentam; '
                'não acrescente etapas nem H3 para cumprir uma quantidade.', heading['title'], auto_repair=True)
        mode = presentation.get('mode')
        if mode == 'steps':
            developed_steps.update(child['start'] for child in children)
        needs_prose = mode in ('explanation', 'comparison') or (mode == 'steps' and bool(expected or children))
        if needs_prose and heading['list_prose'] and not heading['prose']:
            add('list_only_development', 'warning',
                'A seção planejada para desenvolvimento próprio não tem explicação em parágrafos fora das listas.',
                'Desenvolva a explicação, as relações ou os motivos já presentes nas fontes em parágrafos '
                'desta seção. Preserve listas úteis e exemplos; não apenas retire marcadores nem acrescente '
                'frases de preenchimento. Uma sequência curta pode continuar como lista quando o plano assim definir.',
                heading['title'], auto_repair=True)
        nested_by_parent = {}
        for nested in presentation.get('nested_subheadings') or []:
            if isinstance(nested, dict) and nested.get('parent_title') and nested.get('title'):
                nested_by_parent.setdefault(nested['parent_title'], []).append(nested['title'])
        developed_subdivisions = list(located_children.values())
        for parent_title, titles in nested_by_parent.items():
            parent_number = next((index for index, title in enumerate(expected) if title == parent_title), None)
            parent = located_children.get(parent_number)
            if not parent:
                continue  # The missing H3 has already been reported above.
            grandchildren = [child for child in headings if child['level'] == 4 and
                             parent['start'] < child['start'] < parent['end']]
            if len(grandchildren) < len(titles):
                missing = [title for title in titles if not any(
                    _headings_match(title, child['title']) for child in grandchildren)]
                add('planned_nested_subheading_missing', 'warning',
                    'O H3 não entrega as subdivisões H4 previstas para seu próprio desenvolvimento.',
                    f"Desenvolva sob '{parent['title']}' as funções previstas em " + '; '.join(missing or titles) +
                    '. Um H4 em outro H3 não atende a esta subdivisão. Os títulos podem ser reformulados; '
                    'use apenas os aspectos que a fonte sustenta e não crie H4 para cumprir uma quantidade.',
                    parent['title'], auto_repair=True)
            developed_subdivisions.extend(_locate_subheadings(titles, grandchildren).values())
        if mode in ('explanation', 'comparison') and heading['prose']:
            for child in developed_subdivisions:
                if child['list_prose'] and not child['prose']:
                    add('list_only_development', 'warning',
                        'Uma subdivisão planejada para desenvolvimento próprio contém somente listas.',
                        'Desenvolva nesta subdivisão as explicações ou relações sustentadas pela fonte. '
                        'A abertura da seção não substitui seu conteúdo próprio. Preserve listas úteis '
                        'e não acrescente frases de preenchimento.', child['title'], auto_repair=True)

    for heading in headings:
        if not heading['content']:
            # An H2 with developed H3/H4 children already has content in its scope.
            add('empty_heading', 'warning', 'Há um subtítulo sem conteúdo próprio nem desenvolvimento em suas subdivisões.',
                'Desenvolva sua função com as fontes disponíveis ou remova o subtítulo vazio. '
                'Não invente explicações para preencher a estrutura.', heading['title'], auto_repair=True)
            continue
        explicit_step = bool(re.match(r'^(?:Passo|Etapa|Step)\s+\d+[.):\s–—-]+\S', heading['title'], re.I))
        if heading['start'] not in developed_steps and not explicit_step:
            continue
        if not heading['prose'] or all(_repeats_step(text, heading['title'])
                                      for text in heading['prose']):
            add('shallow_step', 'warning', 'O título da etapa não é acompanhado de desenvolvimento próprio em parágrafos.',
                'Explique a ação com as razões, condições ou critérios que a fonte oferece, preservando '
                'listas realmente úteis. Repetir o título ou adicionar uma frase genérica não desenvolve a etapa. '
                'Se a ação é curta e não exige explicação, use a sequência curta prevista no plano.',
                heading['title'], auto_repair=True)
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
            'h2_headings': sum(t.type == 'heading_open' and t.tag == 'h2' for t in tokens),
            'h3_headings': sum(t.type == 'heading_open' and t.tag == 'h3' for t in tokens),
            'h4_headings': sum(t.type == 'heading_open' and t.tag == 'h4' for t in tokens),
            'numbered_headings': numbered_headings, 'findings': findings,
            'notice': 'Diagnóstico local e contextual da entrega; não autoriza nem bloqueia a exportação. '
                      'Comprimento, numeração e semelhança de redação não comprovam inadequação do artigo. '
                      'A presença de subtítulos não comprova que a seção responde à pergunta ou preserva '
                      'as explicações da fonte; isso exige conferência editorial independente.'}
