from copy import deepcopy

import pytest

from app import generation
from app.editorial import changes, engine, text_checks


def codes(job):
    return {finding['code'] for finding in text_checks.analyze(job)['findings']}


def test_tutorial_requires_visible_steps_but_other_genres_do_not(job):
    job['brief']['genre'] = 'tutorial'
    job['article']['markdown'] = '## Configuração\n\nUma explicação sem ações numeradas. [[v1s1]]'
    assert 'tutorial_sequence' in codes(job)
    for genre in ('explicação', 'comparação', 'análise', 'resenha'):
        job['brief']['genre'] = genre
        assert 'tutorial_sequence' not in codes(job)
    job['brief']['genre'] = 'tutorial'
    for body in ('1. Abra o editor.\n2. Confira o arquivo.', '## 1. Abra o editor\n\n## 2. Confira o arquivo',
                 '## Passo 1: Abra o editor\n\n## Passo 2: Confira o arquivo'):
        job['article']['markdown'] = body
        assert 'tutorial_sequence' not in codes(job)


def test_long_duplicate_paragraphs_and_overrun_are_observable_not_model_opinions(job):
    paragraph = ' '.join(f'A condição {n} precisa ser explicada dentro de seu contexto.' for n in range(25))
    job['article']['markdown'] = '\n\n'.join(['## Visão geral', paragraph, '## Introdução',
        paragraph.replace('A condição 0', 'Esta condição 0'), paragraph, paragraph, paragraph])
    result = text_checks.analyze(job)
    assert {'duplicate_paragraph', 'length_overrun', 'long_paragraph'} <= codes(job)
    blockers = {f.get('code') for f in generation.deterministic_findings(job)}
    assert 'duplicate_paragraph' in blockers
    assert 'length_overrun' not in blockers  # The target is a warning, not a new generation error.
    assert result['word_count'] > 800


def test_serialization_artifacts_and_undefined_footnotes_cannot_pass_as_reader_text(job):
    job['article']['markdown'] = r'## Orientação' + '\n\n' + r'Primeiro bloco.\n\nSegundo bloco.[^internal-id] [[v1s1]]'
    assert {'escaped_paragraphs', 'undefined_footnotes'} <= codes(job)
    job['article']['markdown'] = '## Exemplo\n\n```text\nA\\n\\nB[^example]\n```\n\nUse `\\n\\n` no exemplo de código.\n\nNota[^n].\n\n[^n]: Uma definição.'
    assert not {'escaped_paragraphs', 'undefined_footnotes'} & codes(job)


def test_list_numbering_is_not_a_factual_quantity_but_new_quantities_still_fail(job):
    after = deepcopy(job['article'])
    after['markdown'] = '## Orientação\n\n1. Abra o arquivo.\n2. Confira o conteúdo.\n3. Salve o projeto. [[v1s1]]'
    changes.validate_numbers(job, after)
    after['markdown'] += '\n\nO procedimento dura 987 minutos.'
    with pytest.raises(changes.EditConflict):
        changes.validate_numbers(job, after)
    after['markdown'] = '## Orientação\n\n```python\nretries = 987\n```'
    with pytest.raises(changes.EditConflict):
        changes.validate_numbers(job, after)


def test_editor_receives_local_defects_even_when_reader_praises_the_article(job, newsroom_ai):
    job['brief']['genre'] = 'tutorial'
    engine.start(job, 'review')
    reading = {'summary': 'A leitura está perfeita.', 'findings': []}
    engine.edit(job, 'voice_editor', {'reading_review': reading}, 'voice_editor:0')
    payload = newsroom_ai.call_args.args[4]
    assert payload['reading_review'] == reading
    assert 'tutorial_sequence' in {f['code'] for f in payload['local_editorial_review']['findings']}
