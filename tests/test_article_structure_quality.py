from copy import deepcopy

import pytest

from app.editorial import guidance, text_checks


def section(ident, title, mode='explanation', subheadings=(), item_ids=()):
    return {'id': ident, 'title': title, 'question': f'Como desenvolver {title.lower()}?',
            'purpose': 'Responder à tarefa com as explicações disponíveis na fonte.',
            'item_ids': list(item_ids), 'presentation': {
                'mode': mode, 'reason': 'Organizar o conteúdo conforme a tarefa do leitor.',
                'subheadings': list(subheadings)}}


def article(markdown, sections=(), genre='explicação', title='Como montar uma mesa'):
    # Synthetic, local fixtures: no account, provider, persistence or real jobs.
    return {'brief': {'genre': genre}, 'article': {'title': title, 'markdown': markdown},
            'plan': {'data': {'sections': list(sections)}}}


def findings(job):
    return text_checks.analyze(job)['findings']


def repair_finding(job, code):
    finding = next(finding for finding in findings(job) if finding['code'] == code)
    assert finding['auto_repair'] is True
    assert finding['export_blocking'] is False
    assert finding['recipient'] == 'writing'
    assert finding['suggestion'] and finding['passage']
    return finding


def test_source_supported_steps_allow_h4_and_material_lists_without_heading_quotas():
    job = article('''Separe as peças antes de iniciar a montagem.

## Peças necessárias

- Tampo.
- Pernas.
- Parafusos fornecidos.

## Montagem

### Etapa 1: Posicionar as pernas

Alinhe cada perna aos furos do tampo para encaixar os parafusos sem deslocar a peça. [[v1s1]]

### Etapa 2: Apertar os parafusos

O criador mantém a mesa apoiada enquanto aperta os parafusos, para conservar o alinhamento. [[v1s2]]

#### Conferir o apoio

Ele confere se as pernas tocam a superfície antes de retirar o apoio. [[v1s2]]

- Observe cada ponto de contato.
- Mantenha as peças alinhadas.''', [
        section('pecas', 'Peças necessárias', 'checklist'),
        section('montagem', 'Montagem', 'steps',
                ['Posicionar as pernas', 'Apertar os parafusos'])], genre='tutorial')
    report = text_checks.analyze(job)
    assert report['findings'] == []
    assert (report['h2_headings'], report['h3_headings'], report['h4_headings']) == (2, 2, 1)


@pytest.mark.parametrize('mode', ['checklist', 'steps'])
def test_a_short_planned_sequence_needs_no_artificial_paragraphs_or_subheadings(mode):
    job = article('''## Preparação

1. Lave as folhas.
2. Escorra a água.
3. Separe as folhas para a receita.''', [section('preparacao', 'Preparação', mode)],
        genre='tutorial', title='Como preparar as folhas para cozinhar')
    assert findings(job) == []


def test_an_explanation_without_subdivisions_does_not_need_h3_or_h4():
    job = article('''## Apoio da mesa

O apoio mantém as peças na posição durante a montagem. O criador observa o alinhamento antes de retirar o suporte.''',
                  [section('apoio', 'Apoio da mesa')])
    assert findings(job) == []


def test_a_planned_section_missing_from_the_body_is_actionable():
    job = article('## Separar as peças\n\nSepare as peças para conferir os pontos de encaixe.', [
        section('pecas', 'Separar as peças'), section('apoio', 'Conferir o apoio')])
    finding = repair_finding(job, 'planned_section_missing')
    assert finding['passage'] == 'Conferir o apoio'
    assert 'fontes' in finding['suggestion']


def test_renamed_and_reordered_sections_can_be_located_by_their_source_evidence():
    job = article('''## Temperatura da frigideira

O criador aquece a frigideira antes de colocar as folhas. [[v1s2]]

## Retirar a água das folhas

Ele escorre as folhas para evitar excesso de água na frigideira. [[v1s1]]''', [
        section('folhas', 'Preparação dos ingredientes', item_ids=['folhas']),
        section('frigideira', 'Preparação do utensílio', item_ids=['frigideira'])],
        title='Como preparar as folhas na frigideira')
    job['apuration'] = {'items': [
        {'id': 'folhas', 'evidence': [{'source_id': 'v1s1'}]},
        {'id': 'frigideira', 'evidence': [{'source_id': 'v1s2'}]}]}
    assert findings(job) == []


def test_reformulated_section_and_stage_names_are_not_exact_string_requirements():
    job = article('''## Preparar a altura do selim

### Etapa 1: Liberar a regulagem

Abra a trava para permitir que o canote se mova.

### Etapa 2: Fixar a posição escolhida

Feche a trava para manter a altura escolhida.''', [
        section('selim', 'Ajuste da bicicleta', 'steps', ['Soltar a trava', 'Fechar a trava'])],
        genre='tutorial', title='Como ajustar o selim da bicicleta')
    assert findings(job) == []


def test_planned_subdivisions_are_checked_inside_their_own_h2():
    job = article('''## Ajustar o selim

### Abrir a trava

Abra a trava para liberar o canote.

## Verificações

### Conferir a altura

O criador verifica a altura com a bicicleta apoiada.''', [
        section('selim', 'Ajustar o selim', 'steps', ['Abrir a trava', 'Fechar a trava']),
        section('verificacoes', 'Verificações', subheadings=['Conferir a altura'])],
        title='Como ajustar o selim da bicicleta')
    finding = repair_finding(job, 'planned_subheading_missing')
    assert finding['passage'] == 'Ajustar o selim'
    assert 'Fechar a trava' in finding['suggestion']


@pytest.mark.parametrize('mode', ['explanation', 'comparison'])
@pytest.mark.parametrize('marker', ['-', '1.'])
def test_list_only_development_is_visible_even_in_a_single_short_list(mode, marker):
    job = article(f'''## Ponto de apoio

{marker} A peça permanece alinhada sobre o apoio.
{marker} O criador observa os pontos de contato.''', [section('apoio', 'Ponto de apoio', mode)])
    report = text_checks.analyze(job)
    assert report['word_count'] < 300
    finding = repair_finding(job, 'list_only_development')
    assert 'Preserve listas úteis' in finding['suggestion']
    checklist = deepcopy(job)
    checklist['plan']['data']['sections'][0]['presentation']['mode'] = 'checklist'
    assert findings(checklist) == []


def test_a_section_opening_does_not_develop_each_step():
    job = article('''## Montagem

Mantenha o tampo apoiado durante a montagem.

### Etapa 1: Alinhar as pernas

- Posicione as pernas nos furos.
- Confira os pontos de encaixe.

### Etapa 2: Apertar os parafusos

Aperte os parafusos. [[v1s2]]''', [
        section('montagem', 'Montagem', 'steps', ['Alinhar as pernas', 'Apertar os parafusos'])],
        genre='tutorial')
    shallow = [finding for finding in findings(job) if finding['code'] == 'shallow_step']
    assert {finding['passage'] for finding in shallow} == {
        'Etapa 1: Alinhar as pernas', 'Etapa 2: Apertar os parafusos'}
    assert all(finding['auto_repair'] for finding in shallow)


def test_a_short_meaningful_step_explanation_is_not_rejected_by_word_count():
    job = article('''## Ajuste

### Etapa 1: Abrir a trava

A trava libera o canote.

### Etapa 2: Fechar a trava

O fechamento mantém a posição.''', [
        section('ajuste', 'Ajuste', 'steps', ['Abrir a trava', 'Fechar a trava'])],
        genre='tutorial', title='Como ajustar o selim da bicicleta')
    assert findings(job) == []


def test_empty_subdivision_and_reference_only_content_do_not_count_as_development():
    job = article('''## Apoio

### Posição da mesa

[[v1s1]]

### Conferência

Confira o contato das pernas para observar o alinhamento.''')
    finding = repair_finding(job, 'empty_heading')
    assert finding['passage'] == 'Posição da mesa'


@pytest.mark.parametrize('prefix, code', [('#', 'body_h1'), ('##', 'duplicate_article_title')])
def test_title_is_metadata_and_body_repetition_can_be_repaired(prefix, code):
    job = article(f'{prefix} Como montar uma mesa\n\nSepare as peças para iniciar a montagem.')
    finding = repair_finding(job, code)
    assert finding['passage'] == job['article']['title']
    if prefix == '#':
        hierarchy = next(finding for finding in findings(job) if finding['code'] == 'heading_hierarchy')
        assert hierarchy['auto_repair'] is False


def test_fenced_heading_examples_are_not_part_of_the_article_outline():
    job = article('''## Formato das instruções

O exemplo mostra o formato usado pelo criador.

```markdown
# Como montar uma mesa
### Etapa 1: Apertar os parafusos
```''')
    assert findings(job) == []


def test_a_legacy_plan_does_not_invent_new_presentation_requirements():
    job = article('## Apoio\n\n- Confira as pernas.\n- Confira o tampo.')
    job['plan']['data']['sections'] = [{'id': 'apoio', 'title': 'Verificar o apoio'}]
    assert findings(job) == []


def cooking_plan():
    planned = section('cozimento', 'Preparar as folhas', 'steps', ['Aquecer a frigideira', 'Adicionar as folhas'])
    planned['presentation']['nested_subheadings'] = [
        {'parent_title': 'Aquecer a frigideira', 'title': 'Controlar o calor'}]
    return planned


def test_source_supported_optional_h4_is_planned_and_developed_within_its_h3():
    job = article('''## Preparar as folhas

### Etapa 1: Aquecer a frigideira

Aqueça a frigideira antes de colocar as folhas para acompanhar o início do cozimento. [[v1s1]]

#### Controlar o calor

O criador reduz o calor quando as folhas começam a murchar, para acompanhar o ponto demonstrado. [[v1s1]]

### Etapa 2: Adicionar as folhas

Espalhe as folhas sobre a frigideira para observar o cozimento de cada parte. [[v1s2]]''',
        [cooking_plan()], genre='tutorial', title='Como preparar as folhas na frigideira')
    assert findings(job) == []


def test_h4_in_another_h3_does_not_satisfy_the_planned_parent():
    job = article('''## Preparar as folhas

### Etapa 1: Aquecer a frigideira

Aqueça a frigideira antes de colocar as folhas.

### Etapa 2: Adicionar as folhas

Espalhe as folhas para acompanhar o cozimento.

#### Controlar o calor

O criador reduz o calor quando as folhas começam a murchar.''',
        [cooking_plan()], genre='tutorial', title='Como preparar as folhas na frigideira')
    finding = repair_finding(job, 'planned_nested_subheading_missing')
    assert finding['passage'] == 'Etapa 1: Aquecer a frigideira'
    assert 'Controlar o calor' in finding['suggestion']


def test_a_reformulated_h4_retains_its_parent_without_an_exact_name_requirement():
    job = article('''## Preparar as folhas

### Etapa 1: Aquecer a frigideira

O criador aquece a frigideira antes de colocar as folhas.

#### Acompanhar a temperatura

Ele reduz o calor quando as folhas começam a murchar.

### Etapa 2: Adicionar as folhas

Espalhe as folhas para acompanhar o cozimento.''',
        [cooking_plan()], genre='tutorial', title='Como preparar as folhas na frigideira')
    assert findings(job) == []


def test_h2_opening_cannot_mask_list_only_development_in_a_planned_h3():
    job = article('''## Apoio da mesa

O criador explica como observa o apoio durante a montagem.

### Pontos de contato

- A perna dianteira permanece apoiada.
- A perna traseira permanece apoiada.''',
        [section('apoio', 'Apoio da mesa', subheadings=['Pontos de contato'])])
    finding = repair_finding(job, 'list_only_development')
    assert finding['passage'] == 'Pontos de contato'


def test_an_extra_useful_list_is_not_a_required_explanatory_subdivision():
    job = article('''## Apoio da mesa

### Materiais

- Tampo.
- Pernas.

### Pontos de contato

O criador mantém as pernas apoiadas para conferir o alinhamento do tampo.''',
        [section('apoio', 'Apoio da mesa', subheadings=['Pontos de contato'])])
    assert findings(job) == []


def test_planned_h4_and_metadata_title_remain_available_in_the_complete_article_route():
    plan = {'title': 'Como preparar as folhas na frigideira', 'main_question': 'Como preparar as folhas?',
            'opening': 'Situar a preparação.', 'closing': 'Encerrar com a observação do ponto.',
            'sections': [cooking_plan()]}
    route = guidance.article_route(plan)
    assert route['title'] == plan['title']
    assert route['sections'][0]['presentation']['nested_subheadings'] == [
        {'parent_title': 'Aquecer a frigideira', 'title': 'Controlar o calor'}]
