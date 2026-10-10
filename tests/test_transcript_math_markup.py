"""HTML tag names must not erase an ambiguous mathematical comparison."""
import pytest

from app import youtube


EXPLANATION = 'A explicação preserva as condições técnicas e as ressalvas da fala original. '


@pytest.mark.parametrize('condition', ['2 <b e b>1', '2 <b && b>1',
                                     '2 <b and b>1', '2 <b e b > 1'])
def test_unspaced_math_using_an_html_tag_name_remains_literal(condition):
    text = EXPLANATION + 'A condição é ' + condition + '.'
    segment = youtube.segment_rows([{'text': text, 'start': 0, 'duration': 5}], 'v1')[0]
    assert segment['text'] == text
    assert segment['original_cues'][0]['original_text'] == text


@pytest.mark.parametrize('marked,expected', [
    ('<b>O procedimento</b> mantém a condição.', 'O procedimento mantém a condição.'),
    ('<b class="example" title="passo">O procedimento</b> mantém a condição.',
     'O procedimento mantém a condição.'),
    ('<p>Primeira etapa.</p><p>Segunda etapa.</p>', 'Primeira etapa. Segunda etapa.'),
    ('<img src="x" onerror="alert(1)">A condição continua disponível.',
     'A condição continua disponível.'),
    ('<input disabled>A condição continua disponível.', 'A condição continua disponível.'),
])
def test_standard_html_markup_remains_discriminated_and_does_not_join_words(marked, expected):
    segment = youtube.segment_rows([{'text': EXPLANATION + marked, 'start': 0, 'duration': 5}], 'v1')[0]
    assert segment['text'] == EXPLANATION + expected


def test_ambiguous_known_tag_with_invalid_attributes_keeps_its_literal_closing():
    text = EXPLANATION + 'Código técnico: <b strange>valor</b>.'
    segment = youtube.segment_rows([{'text': text, 'start': 0, 'duration': 5}], 'v1')[0]
    assert segment['text'] == text
