import re

from .. import generation


def analyze(job):
    article = job.get('article')
    if not article:
        return []
    text = article['markdown']
    keyword = job['brief'].get('keyword', '').strip().casefold()
    rows = []

    def add(rule, label, status, detail):
        rows.append({'rule_id': rule, 'label': label, 'status': status, 'ok': status == 'pass',
                     'detail': detail, 'engine': 'SEO MASTER — verificação local'})

    add('google.title', 'Título SEO preenchido', 'pass' if article['seo_title'].strip() else 'warning',
        f'{len(article["seo_title"])} caracteres. Sem limite universal do Google; confira clareza e apresentação.')
    add('google.snippet', 'Metadescrição preenchida', 'pass' if article['meta_description'].strip() else 'warning',
        f'{len(article["meta_description"])} caracteres. O buscador pode usar outro trecho.')
    add('yoast.structure', 'Organização em seções H2', 'pass' if re.search(r'^##\s+\S', text, re.M) else 'warning',
        'Títulos devem ajudar o leitor a acompanhar o assunto.')
    add('brand.evidence', 'Integridade das referências', 'pass' if not generation.deterministic_findings(job) else 'fail',
        'Confere IDs e estrutura; não comprova sozinho a veracidade das afirmações.')
    add('yoast.keyphrase', 'Termo principal no título SEO', 'not_applicable' if not keyword else
        ('pass' if keyword in article['seo_title'].casefold() else 'warning'),
        'Correspondência literal simples. Avalie variantes naturais e o sentido antes de alterar.')
    add('yoast.links', 'Links internos e uso anterior da palavra-chave', 'unknown',
        'Dependem do contexto do site; não são necessários para gerar ou revisar no aplicativo.')
    add('google.ai', 'Rastreamento, indexação e elegibilidade em buscas', 'unknown',
        'Não verificados no editor de conteúdo.')
    return rows
