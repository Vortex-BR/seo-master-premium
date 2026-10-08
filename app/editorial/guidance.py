"""Topic-independent source roles and the reader's path through an article."""
from copy import deepcopy


POLICY = '''O vídeo é a fonte EXCLUSIVA de conteúdo, didática e narrativa do artigo.
Toda explicação, raciocínio, analogia, dica, exemplo e ressalva deve vir diretamente da fala do criador.
Preserve sua naturalidade, vocabulário e estilo explicativo humano; apenas adapte a linguagem oral
para leitura em tela, com clareza e organização. Não invente experiências, detalhes ou novos temas.
A pesquisa web tem internal_context_only=True e serve unicamente para compreender termos técnicos,
jargões, marcas e nomes já mencionados no vídeo. Receba-a apenas em agent_background_knowledge.
Nenhuma informação, parágrafo ou seção pode ser criada a partir da pesquisa. Nunca cite notas rn,
trechos de páginas web ou agent_background_knowledge como evidência; somente os vídeos sustentam o artigo.
Em equipe_editorial.source_guidance, os itens dos vídeos mantêm sua origem e ordem de localização;
essa ordem não prova dependência. Preserve métodos, ressalvas e alternativas atribuídas a cada criador.
A fidelidade à fala não transforma uma opinião em verdade universal. Atribua opiniões e experiências;
divergências ou lacunas do vídeo permanecem explícitas e a pesquisa não pode resolvê-las factual ou editorialmente.
Escolha a estrutura pela tarefa real do leitor e pelas fontes, considerando também as instruções do
briefing quando o gênero selecionado for amplo. Para executar uma tarefa, organize etapas identificáveis
com dependências, ação, motivo, condições e critério de avanço, quando sustentados. Para compreender,
comparar ou avaliar, organize conceitos, critérios ou argumentos; não imponha passos a todo conteúdo.
O assunto, produtos, público e narrador vêm exclusivamente do briefing, perfil da marca e fontes deste
trabalho. Fale como a marca quando o perfil assim definir; políticas, garantias e experiências de fontes
externas não pertencem automaticamente à marca. Não invente razões, instruções ou promessas para preencher
um roteiro. Se uma demonstração depende de imagem não analisada, registre a lacuna na apuração.'''

FORMAT_POLICY = '''Escolha a forma de cada trecho pela necessidade de leitura, não por facilidade de listar.
Parágrafos desenvolvem explicações, motivos, condições e relações entre ideias. Listas ajudam a conferir
materiais, requisitos, opções ou ações muito curtas; não substituem o desenvolvimento do artigo.
Quando uma sequência exige explicar cada etapa, organize o processo sob H2 e as etapas em H3
identificáveis (por exemplo, "### Etapa 1: ..."), seguidas de texto que explique a ação e seu motivo
quando as fontes sustentarem. Use uma lista dentro da etapa apenas se houver itens realmente enumeráveis.
Uma sequência curta pode ser uma lista numerada; não imponha H3 a toda ação nem passos a outros gêneros.
Em comparações, organize critérios equivalentes em seções e interprete diferenças em texto.
Checklists podem ser listas quando esse for o produto solicitado. Não converta uma lista inteira
em um parágrafo gigante. Retire frases genéricas de abertura e fechamento das listas; a explicação deve
acrescentar compreensão, sem elogios ao método, promessas nem repetição das instruções.
Na revisão, confronte a estrutura e os requisitos com o corpo realmente entregue. Não declare presente
uma seção que só consta no briefing ou no plano. Um H3 ou marcador isolado não prova desenvolvimento.'''


def source_guide(job):
    """Derive provenance from actual evidence IDs, never from model-supplied labels."""
    items = (job.get('apuration') or {}).get('items', [])
    videos = []
    owned = set()
    video_sources = {segment['id'] for source in job.get('sources', [])
                     if not source.get('internal_context_only')
                     for segment in source.get('segments', []) if not segment.get('internal_context_only')}
    relations = {v['id']: v.get('relations', []) for v in (job.get('apuration') or {}).get('videos', [])}
    for source in job.get('sources', []):
        if source.get('internal_context_only'):
            continue
        positions = {s['id']: n for n, s in enumerate(source.get('segments', []))}
        contributions = [i for i in items if not i.get('internal_context_only') and
                         i.get('evidence') and
                         all(e['source_id'] in video_sources for e in i['evidence']) and
                         any(e['source_id'] in positions for e in i['evidence'])]
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
    excluded = [i['id'] for i in items if i['id'] not in owned and (i.get('internal_context_only') or
                any(e['source_id'] in research_sources for e in i.get('evidence', [])))]
    return {'videos': videos, 'research_item_ids': [], 'excluded_internal_context_item_ids': excluded,
            'unclassified_item_ids': [i['id'] for i in items if i['id'] not in owned and i['id'] not in excluded],
            'basis': 'Conteúdo exclusivo dos vídeos; imagens e demonstrações visuais não são presumidas.',
            'priority': 'Os vídeos definem conteúdo e percurso; pesquisa é apenas entendimento interno e não cria tópicos.'}


def video_item_ids(job):
    return {ident for video in source_guide(job)['videos'] for ident in video['supported_item_ids']}


def article_route(plan):
    """Keep the whole planned path available even when writing a single section."""
    return {'reader_journey': plan.get('reader_journey'), 'main_question': plan['main_question'],
            'opening': plan['opening'], 'closing': plan['closing'],
            'sections': [{key: section.get(key) for key in ('id', 'title', 'question', 'purpose',
                                                       'prerequisites', 'conditions', 'transition', 'presentation')}
                         for section in plan['sections']]}
