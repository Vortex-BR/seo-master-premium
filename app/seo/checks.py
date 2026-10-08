"""Local editorial checks. No provider calls or imitation of plugin scores."""
import re
import unicodedata

from markdown_it import MarkdownIt

from .. import generation


AI_JARGON = (
    'é crucial ressaltar', 'no mundo contemporâneo', 'vale a pena destacar',
    'em suma', 'um divisor de águas', 'é imprescindível', 'vale ressaltar',
    'é importante ressaltar', 'é fundamental destacar',
)
_CITATION = re.compile(r'\[\[([\w-]+)\]\]')
_TIMESTAMP = re.compile(r'\[(?:\d+:[0-5]\d:[0-5]\d|\d+:[0-5]\d)\]')


def _fold(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    return ' '.join(''.join(char for char in text if not unicodedata.combining(char)).split())


def _row(rule, label, status, detail):
    return {'rule_id': rule, 'label': label, 'status': status, 'ok': status == 'pass',
            'detail': detail, 'engine': 'SEO MASTER — verificação local'}


def _tokens(text):
    return MarkdownIt().parse(text)


def _inline_text(token):
    # Work on displayed prose, never code blocks, link targets or HTML comments.
    # Inline code is retained: a writer cannot hide a cliché inside backticks.
    return ''.join(' ' if child.type in ('softbreak', 'hardbreak') else child.content
                   for child in token.children or []
                   if child.type in ('text', 'code_inline', 'softbreak', 'hardbreak'))


def _prose(tokens):
    return '\n'.join(_inline_text(token) for token in tokens if token.type == 'inline')


def _introduction(tokens):
    """Introductory paragraphs before the first section, excluding the H1 title."""
    paragraphs = []
    in_paragraph = False
    for token in tokens:
        if token.type == 'heading_open' and int(token.tag[1:]) >= 2:
            break
        if token.type == 'paragraph_open':
            in_paragraph = True
        elif token.type == 'paragraph_close':
            in_paragraph = False
        elif token.type == 'inline' and in_paragraph:
            paragraphs.append(_inline_text(token))
    return paragraphs


def _contains_name(text, name):
    return bool(re.search(r'(?<!\w)' + re.escape(_fold(name)) + r'(?!\w)', _fold(text)))


def video_first_checks(job):
    """Checks exclusive to strict cycles; warnings alone never block delivery."""
    article = job.get('article')
    if not article or not job.get('editorial', {}).get('video_first'):
        return []
    tokens = _tokens(article.get('markdown', ''))
    introduction = _introduction(tokens)
    sources = job.get('sources', [])
    refs = set(_CITATION.findall(article.get('markdown', '')))
    cited_sources = [source for source in sources
                     if any(segment['id'] in refs for segment in source.get('segments', []))]
    # An unused input video is not an author of the article. Without valid
    # citations, keep the conservative check while reference validation fails.
    attributed_sources = cited_sources or sources
    known = {(source.get('author') or '').strip() for source in attributed_sources
             if (source.get('author') or '').strip()}
    missing = sorted(author for author in known
                     if not any(_contains_name(paragraph, author) for paragraph in introduction))
    unknown = sum(not (source.get('author') or '').strip() for source in attributed_sources)
    if missing:
        attribution = _row('video_first.attribution', 'Crédito ao criador na introdução', 'fail',
                           'Credite na introdução os criadores das fontes: ' + '; '.join(missing) + '.')
    elif unknown or not sources:
        attribution = _row('video_first.attribution', 'Crédito ao criador na introdução', 'warning',
                           'A autoria de uma ou mais fontes não está disponível. Confira os metadados; '
                           'não invente o nome do criador.')
    else:
        attribution = _row('video_first.attribution', 'Crédito ao criador na introdução', 'pass',
                           'Os nomes dos criadores informados nas fontes aparecem nos parágrafos de abertura.')

    prose = _prose(tokens)
    visible_article = '\n'.join([prose, *(article.get(key, '') for key in
                                       ('title', 'seo_title', 'meta_description', 'excerpt'))])
    folded = _fold(visible_article)
    found = [phrase for phrase in AI_JARGON
             if re.search(r'(?<!\w)' + re.escape(_fold(phrase)) + r'(?!\w)', folded)]
    rows = [attribution,
            _row('video_first.no_ai_jargon', 'Linguagem direta sem clichês de IA',
                 'fail' if found else 'pass',
                 'Substitua as expressões vazias: ' + '; '.join(found) + '.' if found else
                 'Nenhuma das expressões proibidas foi encontrada no texto ou nos metadados.'),
            _row('video_first.timestamp_presence', 'Referência de tempo do vídeo',
                 'pass' if _TIMESTAMP.search(prose) else 'warning',
                 'Marcações de tempo ajudam a localizar a demonstração. A presença da marcação não '
                 'certifica sua exatidão; use apenas tempos existentes nas fontes.')]

    # Defense in depth: web materials are background knowledge and never article citations.
    video_ids = {segment['id'] for source in sources for segment in source.get('segments', [])}
    web_ids = {source['id'] for source in job.get('research', {}).get('sources', [])}
    forbidden = sorted(ref for ref in refs if ref in web_ids or ref not in video_ids)
    rows.append(_row('video_first.video_evidence', 'Referências exclusivas ao vídeo',
                     'fail' if forbidden else 'pass',
                     'Referências externas ou inexistentes: ' + ', '.join(forbidden) + '. '
                     'Use apenas IDs de segmentos do vídeo.' if forbidden else
                     'As referências citadas pertencem a segmentos de vídeo. '
                     'Esta checagem não prova fidelidade semântica.'))
    return rows


def blocking_findings(job):
    """Adapt only strict rule failures to the factual review's existing contract."""
    return [{'severity': 'blocking', 'passage': '', 'reason': row['detail'],
             'suggestion': 'Corrija a falha local e execute a revisão novamente.',
             'source_ids': [], 'origin': 'validation', 'rule_ids': [row['rule_id']]}
            for row in video_first_checks(job) if row['status'] == 'fail']


def analyze(job):
    article = job.get('article')
    if not article:
        return []
    text = article['markdown']
    keyword = job['brief'].get('keyword', '').strip().casefold()
    tokens = _tokens(text)
    headings = [int(token.tag[1:]) for token in tokens if token.type == 'heading_open']
    rows = []

    def add(rule, label, status, detail):
        rows.append(_row(rule, label, status, detail))

    title_limit = job['brief'].get('seo_title_max_chars')
    configured_limit = (isinstance(title_limit, int) and not isinstance(title_limit, bool) and title_limit > 0)
    long_title = configured_limit and len(article['seo_title']) > title_limit
    title_detail = f'{len(article["seo_title"])} caracteres. Sem limite universal do Google; confira clareza e apresentação.'
    if configured_limit:
        title_detail += f' Preferência editorial configurada: até {title_limit} caracteres.'
    add('google.title', 'Título SEO preenchido', 'pass' if article['seo_title'].strip() and not long_title else 'warning',
        title_detail)
    add('google.snippet', 'Metadescrição preenchida', 'pass' if article['meta_description'].strip() else 'warning',
        f'{len(article["meta_description"])} caracteres. O buscador pode usar outro trecho.')
    add('yoast.structure', 'Organização em seções H2', 'pass' if 2 in headings else 'warning',
        f'{headings.count(2)} seção(ões) H2. Títulos devem ajudar o leitor a acompanhar o assunto.')
    previous, bad_hierarchy = 1, False
    for level in headings:
        if level > previous + 1:
            bad_hierarchy = True
        previous = level
    add('yoast.heading_hierarchy', 'Hierarquia dos subtítulos', 'warning' if bad_hierarchy else 'pass',
        f'{headings.count(3)} subtítulo(s) H3. Use H3 quando houver uma subdivisão útil de H2; '
        'não crie H3 apenas para preencher uma quota.' +
        (' Há saltos de nível na hierarquia.' if bad_hierarchy else ''))
    add('brand.evidence', 'Integridade das referências', 'pass' if not generation.deterministic_findings(job) else 'fail',
        'Confere IDs e estrutura; não comprova sozinho a veracidade das afirmações.')
    add('yoast.keyphrase', 'Termo principal no título SEO', 'not_applicable' if not keyword else
        ('pass' if keyword in article['seo_title'].casefold() else 'warning'),
        'Correspondência literal simples. Avalie variantes naturais e o sentido antes de alterar.')
    words = re.findall(r'[^\W_]+', _fold(_CITATION.sub('', _prose(tokens))))
    key_words = re.findall(r'[^\W_]+', _fold(keyword))
    occurrences = sum(words[index:index + len(key_words)] == key_words
                      for index in range(len(words) - len(key_words) + 1)) if key_words else 0
    density = occurrences * len(key_words) / len(words) * 100 if words else 0
    add('yoast.keyphrase_density', 'Presença e frequência do termo principal',
        'not_applicable' if not key_words else 'pass' if occurrences else 'warning',
        f'{occurrences} ocorrência(s) em {len(words)} palavras; {density:.2f}% das palavras pertencem '
        'à frase-chave. Diagnóstico local, sem densidade ideal universal ou prova de spam.')
    add('yoast.links', 'Links internos e uso anterior da palavra-chave', 'unknown',
        'Dependem do contexto do site; não são necessários para gerar ou revisar no aplicativo.')
    add('google.ai', 'Rastreamento, indexação e elegibilidade em buscas', 'unknown',
        'Não verificados no editor de conteúdo.')
    rows.extend(video_first_checks(job))
    return rows
