"""Topic-independent source roles and the reader's path through an article."""
from copy import deepcopy


POLICY = '''Use o briefing para definir a pergunta e os vídeos enviados como guia editorial do conteúdo.
Em equipe_editorial.source_guidance, os itens dos vídeos mantêm sua origem e ordem de localização;
essa ordem não prova dependência. Preserve as relações conferidas, os métodos e as ressalvas.
A pesquisa complementa lacunas, verifica e pode corrigir afirmações com evidência. Mais páginas ou
mais itens de pesquisa não lhes dão prioridade sobre o objetivo e o percurso explicativo dos vídeos.
Prioridade editorial não torna uma fala verdadeira: divergências exigem atribuição e conferência.
Escolha a estrutura pela tarefa real do leitor e pelas fontes, considerando também as instruções do
briefing quando o gênero selecionado for amplo. Para executar uma tarefa, organize etapas identificáveis
com dependências, ação, motivo, condições e critério de avanço, quando sustentados. Para compreender,
comparar ou avaliar, organize conceitos, critérios ou argumentos; não imponha passos a todo conteúdo.
O assunto, produtos, público e narrador vêm exclusivamente do briefing, perfil da marca e fontes deste
trabalho. Fale como a marca quando o perfil assim definir; políticas, garantias e experiências de fontes
externas não pertencem automaticamente à marca. Não invente razões, instruções ou promessas para preencher
um roteiro. Se uma demonstração depende de imagem não analisada, registre a lacuna na apuração.'''


def source_guide(job):
    """Derive provenance from actual evidence IDs, never from model-supplied labels."""
    items = (job.get('apuration') or {}).get('items', [])
    videos = []
    owned = set()
    relations = {v['id']: v.get('relations', []) for v in (job.get('apuration') or {}).get('videos', [])}
    for source in job.get('sources', []):
        positions = {s['id']: n for n, s in enumerate(source.get('segments', []))}
        contributions = [i for i in items if any(e['source_id'] in positions for e in i.get('evidence', []))]
        # Source position locates the explanation; relations record actual dependencies.
        contributions.sort(key=lambda i: min(positions[e['source_id']] for e in i['evidence']
                                              if e['source_id'] in positions))
        ids = {i['id'] for i in contributions}
        owned.update(ids)
        videos.append({'source_id': source['id'], 'title': source.get('title', ''),
            'ordered_item_ids': [i['id'] for i in contributions],
            'supported_item_ids': [i['id'] for i in contributions if i.get('check', {}).get('status') == 'supported'],
            'relations': [deepcopy(r) for r in relations.get(source['id'], [])
                          if r.get('check', {}).get('status') == 'supported' and set(r['item_ids']) <= ids]})
    research_sources = {s['id'] for s in job.get('research', {}).get('sources', [])}
    research = [i['id'] for i in items if i['id'] not in owned and
                any(e['source_id'] in research_sources for e in i.get('evidence', []))]
    return {'videos': videos, 'research_item_ids': research,
            'unclassified_item_ids': [i['id'] for i in items if i['id'] not in owned and i['id'] not in research],
            'basis': 'Conteúdo extraído das fontes; imagens e demonstrações visuais não são presumidas.',
            'priority': 'Os vídeos orientam o percurso; pesquisa complementa e verifica, sem votação por quantidade.'}


def video_item_ids(job):
    return {ident for video in source_guide(job)['videos'] for ident in video['supported_item_ids']}


def article_route(plan):
    """Keep the whole planned path available even when writing a single section."""
    return {'reader_journey': plan.get('reader_journey'), 'main_question': plan['main_question'],
            'opening': plan['opening'], 'closing': plan['closing'],
            'sections': [{key: section[key] for key in ('id', 'title', 'question', 'purpose',
                                                       'prerequisites', 'conditions', 'transition')}
                         for section in plan['sections']]}
