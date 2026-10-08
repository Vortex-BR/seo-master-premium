from copy import deepcopy
from html.parser import HTMLParser

import pytest

from app import generation, publishing
from app.editorial import changes, text_checks


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def test_step_architecture_exports_as_native_wordpress_headings_paragraphs_and_useful_list(job):
    job['article']['markdown'] = '''## Configurar o projeto

### Etapa 1: Abra o editor

Abra o projeto para conferir os arquivos que serão usados. A conferência evita selecionar outro arquivo. [[v1s1]]

### Etapa 2: Confira antes de salvar

Leia a alteração e confira os itens necessários para manter o registro consistente. [[v1s1]]

- Arquivo correto.
- Nome identificado.

## Resultado

O registro fica disponível para a próxima conferência. [[v1s1]]'''
    tags = Tags()
    html = publishing.render(job, gutenberg=True)
    tags.feed(html)
    assert tags.tags.count('h2') == 2 and tags.tags.count('h3') == 2
    assert tags.tags.count('ul') == 1 and tags.tags.count('p') == 3
    assert '<!-- wp:heading {"level":3} -->' in html
    assert '<!-- wp:list -->' in html and '<!-- wp:paragraph -->' in html
    assert 'watch?v=abcdefghijk' in html and '[[v1s1]]' not in html
    assert not text_checks.analyze(job)['findings']


def test_numbered_h3_does_not_count_as_invented_factual_quantity(job):
    edited = deepcopy(job['article'])
    edited['markdown'] = '## Processo\n\n### Etapa 987: Conferir o arquivo\n\nConfira o registro. [[v1s1]]'
    changes.validate_numbers(job, edited)
    edited['markdown'] += '\n\n### Espere 654 minutos'
    with pytest.raises(changes.EditConflict):
        changes.validate_numbers(job, edited)


def test_list_dominance_is_observable_but_not_an_automatic_paid_rewrite_or_blocker(job):
    job['article']['markdown'] = '\n\n'.join('## Grupo '+str(n)+'\n\n'+ '\n'.join(
        '- ' + ' '.join(['Informação útil para conferir no registro.'] * 4) for _ in range(5)) for n in range(3))
    report = text_checks.analyze(job)
    finding = next(f for f in report['findings'] if f['code'] == 'list_dominance')
    assert report['list_items'] == 15 and report['list_word_ratio'] > .9
    assert finding['severity'] == 'warning' and finding['auto_repair'] is False
    assert not any(f.get('code') == 'list_dominance' for f in generation.deterministic_findings(job))


def test_real_reader_journey_drives_sequence_check_and_h3_satisfies_it(job):
    job['brief']['genre'] = 'explicação'
    job['plan'] = {'data': {'reader_journey': {'kind': 'sequencial'}}}
    assert any(f['code'] == 'tutorial_sequence' for f in text_checks.analyze(job)['findings'])
    job['article']['markdown'] = '## Configuração\n\n### Etapa 1: Abra\n\nConfira o projeto.\n\n### Etapa 2: Salve\n\nSalve o registro.'
    assert not any(f['code'] == 'tutorial_sequence' for f in text_checks.analyze(job)['findings'])


def test_orphan_heading_is_visible_diagnostic_not_a_new_delivery_blocker(job):
    job['article']['markdown'] = '### Subdivisão sem seção\n\nUma explicação que continua disponível. [[v1s1]]'
    report = text_checks.analyze(job)
    finding = next(f for f in report['findings'] if f['code'] == 'heading_hierarchy')
    assert finding['severity'] == 'warning' and finding['auto_repair'] is False
    assert 'Uma explicação' in publishing.render(job)


def test_readability_receives_architecture_and_real_list_diagnostic(job):
    from app.editorial import engine
    job['article']['markdown'] = '\n\n'.join('## Grupo '+str(n)+'\n\n'+ '\n'.join(
        '- ' + ' '.join(['Informação útil para conferir no registro.'] * 4) for _ in range(5)) for n in range(3))
    payload = engine.reading_payload(job)
    assert payload['article'] == job['article']
    assert payload['local_editorial_review']['bullet_lists'] == 3
    assert any(f['code'] == 'list_dominance' for f in payload['local_editorial_review']['findings'])
