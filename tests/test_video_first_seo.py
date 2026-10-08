"""Video-first SEO validation is deterministic and never contacts providers."""
from copy import deepcopy

import pytest

from app import generation
from app.seo import checks, knowledge


@pytest.fixture
def local_job():
    return {
        'brief': {'keyword': 'folhas', 'topic': 'Observação das folhas', 'instructions': ''},
        'editorial': {'video_first': True},
        'sources': [{'id': 'v1', 'author': 'João Silva', 'title': 'Observação da horta',
                     'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                     'segments': [{'id': 'v1s1', 'text': 'Observe as folhas antes de regar.',
                                   'start': 225, 'end': 230}]}],
        'article': {'title': 'Como observar as folhas', 'seo_title': 'Como observar as folhas',
                    'meta_description': 'Observe as folhas antes de regar.', 'excerpt': 'Observação das folhas.',
                    'markdown': 'No vídeo, João Silva ensina a observar as folhas.\n\n'
                                '## Observe as folhas\n\n'
                                'Observe as folhas antes de regar. [03:45] [[v1s1]]'},
    }


def video_rows(job):
    return {row['rule_id']: row for row in checks.video_first_checks(job)}


def test_video_first_rules_pass_without_provider_calls(local_job, monkeypatch):
    monkeypatch.setattr(generation, 'client', lambda: pytest.fail('Local SEO opened a provider client'))
    assert all(row['status'] == 'pass' for row in video_rows(local_job).values())
    assert checks.blocking_findings(local_job) == []


@pytest.mark.parametrize('opening', [
    'O criador ensina a observar as folhas.',
    'No vídeo, Maria Silva ensina a observar as folhas.',
    '# João Silva\n\nObserve as folhas antes de regar.',
    '<!-- João Silva -->\n\nObserve as folhas antes de regar.',
    'Veja [a explicação](https://example.com/João-Silva).',
    'Veja [a explicação](https://example.com "João Silva").',
    'No vídeo, João Silvano ensina a observar as folhas.',
])
def test_generic_other_or_hidden_author_does_not_credit_source(local_job, opening):
    local_job['article']['markdown'] = opening + '\n\n## Método\n\nJoão Silva observa as folhas. [[v1s1]]'
    assert video_rows(local_job)['video_first.attribution']['status'] == 'fail'
    assert 'video_first.attribution' in {rule for finding in checks.blocking_findings(local_job)
                                         for rule in finding['rule_ids']}


def test_attribution_accepts_visible_markdown_and_natural_line_breaks(local_job):
    local_job['article']['markdown'] = 'No vídeo, **JOAO\nSILVA** explica a observação.\n\n## Método\n\n[[v1s1]]'
    assert video_rows(local_job)['video_first.attribution']['status'] == 'pass'


@pytest.mark.parametrize('author', ['', None])
def test_missing_author_metadata_warns_without_inventing_a_name(local_job, author):
    local_job['sources'][0]['author'] = author
    row = video_rows(local_job)['video_first.attribution']
    assert row['status'] == 'warning'
    assert 'não invente' in row['detail']
    assert not checks.blocking_findings(local_job)


def test_multiple_creators_are_credited_in_opening(local_job):
    other = deepcopy(local_job['sources'][0])
    other.update(id='v2', author='Ana Souza', segments=[{'id': 'v2s1', 'text': 'Observe a base da folha.'}])
    local_job['sources'].append(other)
    local_job['article']['markdown'] += '\n\nObserve a base da folha. [[v2s1]]'
    assert video_rows(local_job)['video_first.attribution']['status'] == 'fail'
    local_job['article']['markdown'] = 'João Silva e Ana Souza explicam a observação das folhas.\n\n## Método\n\n[[v1s1]] [[v2s1]]'
    assert video_rows(local_job)['video_first.attribution']['status'] == 'pass'


def test_unused_input_video_does_not_require_credit(local_job):
    other = deepcopy(local_job['sources'][0])
    other.update(id='v2', author='Ana Souza', segments=[{'id': 'v2s1', 'text': 'Outro tema.'}])
    local_job['sources'].append(other)
    assert video_rows(local_job)['video_first.attribution']['status'] == 'pass'


@pytest.mark.parametrize('phrase', checks.AI_JARGON)
def test_each_banned_phrase_fails_locally(local_job, phrase):
    local_job['article']['markdown'] += '\n\n' + phrase.upper() + ', observe as folhas.'
    assert video_rows(local_job)['video_first.no_ai_jargon']['status'] == 'fail'


def test_jargon_detects_accents_spacing_and_metadata_but_respects_word_boundaries(local_job):
    local_job['article']['meta_description'] = 'E\n   IMPRESCINDIVEL observar as folhas.'
    assert video_rows(local_job)['video_first.no_ai_jargon']['status'] == 'fail'
    local_job['article']['meta_description'] = 'A origem da planta fica em Sumatra.'
    assert video_rows(local_job)['video_first.no_ai_jargon']['status'] == 'pass'


@pytest.mark.parametrize('timestamp', ['[03:45]', '[1:03:45]', '[125:45]'])
def test_visible_timestamps_are_encouraged(local_job, timestamp):
    local_job['article']['markdown'] = local_job['article']['markdown'].replace('[03:45]', timestamp)
    assert video_rows(local_job)['video_first.timestamp_presence']['status'] == 'pass'


@pytest.mark.parametrize('timestamp', ['', '[03:99]', '[1:99:45]', '````\n[03:45]\n````'])
def test_absent_invalid_or_code_timestamps_warn_without_blocking(local_job, timestamp):
    local_job['article']['markdown'] = local_job['article']['markdown'].replace('[03:45]', '')
    local_job['article']['markdown'] += '\n\n' + timestamp
    assert video_rows(local_job)['video_first.timestamp_presence']['status'] == 'warning'
    assert not checks.blocking_findings(local_job)


@pytest.mark.parametrize('reference', ['rn1', 'wpage1s1', 'unknown'])
def test_external_and_unknown_references_block_strict_cycles(local_job, reference):
    local_job['research'] = {'sources': [{'id': 'rn1'}, {'id': 'wpage1s1'}]}
    local_job['article']['markdown'] += f'\n\nTexto externo. [[{reference}]]'
    assert video_rows(local_job)['video_first.video_evidence']['status'] == 'fail'
    assert any('video_first.video_evidence' in finding['rule_ids']
               for finding in checks.blocking_findings(local_job))


def test_strict_rules_leave_saved_legacy_cycles_accessible(local_job):
    local_job['editorial'].pop('video_first')
    local_job['article']['markdown'] = 'Em suma, o criador apresenta o tema. [[rn1]]'
    assert checks.video_first_checks(local_job) == []
    assert checks.blocking_findings(local_job) == []


def local_rows(job, monkeypatch):
    # Reference and factual integrity are tested at their own boundary; this isolates SEO signals.
    monkeypatch.setattr(generation, 'deterministic_findings', lambda current: [])
    return {row['rule_id']: row for row in checks.analyze(job)}


def test_keyphrase_frequency_counts_whole_words_and_excludes_reference_ids(local_job, monkeypatch):
    local_job['brief']['keyword'] = 'folha'
    local_job['article']['markdown'] = '## Horta\n\nA folha cresce. As folhas crescem. [[folha]]'
    row = local_rows(local_job, monkeypatch)['yoast.keyphrase_density']
    assert row['status'] == 'pass'
    assert '1 ocorrência(s) em 7 palavras' in row['detail']
    assert '14.29%' in row['detail']


def test_multiword_density_is_defined_and_no_density_target_is_imposed(local_job, monkeypatch):
    local_job['brief']['keyword'] = 'folha nova'
    local_job['article']['markdown'] = 'Folha nova. Folha nova.'
    row = local_rows(local_job, monkeypatch)['yoast.keyphrase_density']
    assert row['status'] == 'pass'
    assert '2 ocorrência(s) em 4 palavras' in row['detail']
    assert '100.00%' in row['detail']
    local_job['brief']['keyword'] = ''
    assert local_rows(local_job, monkeypatch)['yoast.keyphrase_density']['status'] == 'not_applicable'


def test_heading_checks_ignore_code_and_warn_for_h3_without_h2(local_job, monkeypatch):
    local_job['article']['markdown'] = '```\n## Código\n```\n\n### Observação\n\nObserve as folhas.'
    rows = local_rows(local_job, monkeypatch)
    assert rows['yoast.structure']['status'] == 'warning'
    assert rows['yoast.heading_hierarchy']['status'] == 'warning'
    local_job['article']['markdown'] = '## Observação\n\nObserve as folhas.'
    rows = local_rows(local_job, monkeypatch)
    assert rows['yoast.structure']['status'] == 'pass'
    assert rows['yoast.heading_hierarchy']['status'] == 'pass'


def test_title_size_is_diagnostic_unless_user_sets_an_editorial_limit(local_job, monkeypatch):
    local_job['article']['seo_title'] = 'Observação das folhas ' * 10
    assert local_rows(local_job, monkeypatch)['google.title']['status'] == 'pass'
    local_job['brief']['seo_title_max_chars'] = 80
    row = local_rows(local_job, monkeypatch)['google.title']
    assert row['status'] == 'warning'
    assert 'Preferência editorial configurada' in row['detail']


def test_versioned_rule_catalog_covers_local_rule_ids(local_job, monkeypatch):
    package = knowledge.package()
    ids = [rule['id'] for rule in package['rules']]
    assert len(ids) == len(set(ids))
    assert set(local_rows(local_job, monkeypatch)) <= set(ids)
    required = {'id', 'title', 'origin', 'publisher', 'url', 'sectors', 'guidance',
                'application', 'exceptions', 'example', 'evaluation'}
    assert all(required <= set(rule) for rule in package['rules'])
    assert all('seo' in rule['sectors'] for rule in package['rules']
               if rule['id'].startswith('video_first.'))
