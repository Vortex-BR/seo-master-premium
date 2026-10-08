"""Versioned editorial workflow: individual evidence, explicit decisions and coverage.

The coordinator controls IDs, dependencies, budgets and completeness. Models assess
meaning; their opinions never bypass structural checks or unresolved source issues.
"""
from collections import defaultdict
import json
import os
import re
import unicodedata
from pydantic import ValidationError

from .. import db, generation
from ..schemas import Article, Review
from . import agents, guidance, source_processing, store
from .contracts import (ArticleMetadata, ArticlePlan, BlockKnowledge, DraftSection,
                        KnowledgeAudit, PassageAudit, PlanStructure, TopicComparison, TopicPlan,
                        TopicRouting, VideoContext)

VERSION = 1


class BudgetExceeded(ValueError):
    pass


class NeedsInput(ValueError):
    pass


def enabled():
    return os.getenv('EDITORIAL_FLOW', 'evidence') != 'legacy'


def dependencies(job):
    state = job['editorial']
    return {'inputs': store.inputs_version(job), 'flow': VERSION, 'agents': agents.VERSION,
            'model': state['model'], 'profile': store.voice(state['profile'])['version'],
            'partition_chars': source_processing.block_limit(state['profile']['profile']),
            'knowledge': state['knowledge_version']}


def compatible(job):
    apuration = job.get('apuration') or {}
    return apuration.get('valid') and apuration.get('dependencies') == dependencies(job)


def remaining(job):
    return job['editorial']['profile']['profile']['max_calls'] - job['editorial']['calls']


def reserve(job, calls, stage, *, strict=False):
    # New cycles deliver the draft before optional downstream reviews. Actual
    # provider calls are still capped centrally in engine.invoke, after cache lookup.
    if job['editorial'].get('composition_version') == 1 and not strict:
        return
    if remaining(job) < calls:
        job['editorial']['budget_pending'] = {'stage': stage, 'needed': calls, 'remaining': remaining(job)}
        db.save_job(job)
        raise BudgetExceeded(f'Orçamento insuficiente para {stage}: precisa reservar {calls} chamada(s), '
                             f'mas restam {remaining(job)}. As entregas foram preservadas. Ajuste o perfil e retome.')


def exact_ids(values, expected, label):
    if len(values) != len(set(values)) or set(values) != set(expected):
        raise generation.GenerationResponseError('coverage_mismatch',
            'A IA não avaliou exatamente todos os itens da etapa. '
            'A entrega foi rejeitada e as etapas concluídas foram preservadas.', retryable=True)


def known_ids(values, expected, label):
    if set(values) - set(expected):
        raise generation.GenerationResponseError('unknown_reference',
            'A IA citou uma referência que não pertence à etapa. '
            'A entrega foi rejeitada e as etapas concluídas foram preservadas.', retryable=True)


def call(job, role, schema, instruction, payload, slot, validate=None):
    from . import engine
    payload = {'_local_context': True, **payload}
    def deliver(current):
        raw = generation.structured(current, schema, instruction, role, payload)
        try:
            result = schema.model_validate(raw).model_dump()
            if validate:
                validate(result)
        except generation.GenerationResponseError:
            raise
        except ValidationError as exc:
            raise generation.schema_failure(exc) from None
        except ValueError:
            # Durable one-time format recovery, including exact coverage failures.
            raise generation.GenerationResponseError('invalid_output', generation.INVALID_RESPONSE_MESSAGE,
                                                     retryable=True) from None
        return result
    return engine.invoke(job, role, payload, deliver, slot)[0]


def batches(items, char_limit, cost=None, max_items=30):
    cost = cost or (lambda item: len(json.dumps(item, ensure_ascii=False)))
    group, size = [], 0
    for item in items:
        length = cost(item)
        if length > char_limit:
            raise ValueError('Uma unidade de apuração excede o contexto configurado. Aumente o limite ou reduza a pauta.')
        if group and (size + length > char_limit or len(group) >= max_items):
            yield group
            group, size = [], 0
        group.append(item)
        size += length
    if group:
        yield group


def source_fragments(job, items, padding=450):
    """Literal windows around evidence; originals remain unchanged in storage."""
    mapping = generation.evidence_map(job)
    spans = defaultdict(list)
    for item in items:
        for evidence in item.get('evidence', []):
            source = mapping.get(evidence['source_id'])
            if not source:
                continue
            # Extraction requires exact quotes. For legacy casing differences keep the full segment.
            offset = source['text'].find(evidence['excerpt'])
            if offset < 0:
                spans[evidence['source_id']].append((0, len(source['text'])))
            else:
                spans[evidence['source_id']].append((max(0, offset - padding),
                                                    min(len(source['text']), offset + len(evidence['excerpt']) + padding)))
    result = {}
    for ident, intervals in spans.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        source = mapping[ident]
        result[ident] = {**source, 'text': '\n[... intervalo entre trechos ...]\n'.join(
            source['text'][start:end] for start, end in merged), 'original_spans': merged}
    return result


def item_index(job):
    return {item['id']: item for item in job.get('apuration', {}).get('items', [])}


def compact(item):
    return {key: item[key] for key in ('id', 'video_id', 'topic', 'statement', 'kind', 'information_type',
                                     'method', 'conditions', 'quantities', 'restrictions', 'limitations', 'check')}


def locator(item):
    """A discovery index, not a replacement for the complete checked item."""
    result = {'id': item['id'], 'video_id': item['video_id'], 'topic': item['topic'],
            'locator': item['statement'][:160], 'abbreviated': len(item['statement']) > 160,
            'information_type': item['information_type']}
    result.update({key: item[key] for key in ('method', 'conditions', 'quantities', 'restrictions') if item[key]})
    return result


def check_relations(job, rows, items, slot):
    """Load every referenced original, including counterparts discovered through indexes."""
    index = {i['id']: i for i in items}
    assessed = []
    def relation_cost(row):
        return len(json.dumps(row, ensure_ascii=False)) + sum(len(json.dumps(index[i], ensure_ascii=False)) * 2 for i in row['item_ids'])
    for n, group in enumerate(batches(rows, job['editorial']['profile']['profile']['context_chars'] // 2, relation_cost)):
        claims = []
        for k, row in enumerate(group):
            originals = [index[ident] for ident in row['item_ids']]
            claims.append({'id': f'r{k+1}', 'statement': row['explanation'],
                           'evidence': [e for i in originals for e in i['evidence']], 'items': originals})
        result = call(job, 'source_checker', KnowledgeAudit,
                      CHECK + '\nA formulação examinada é uma RELAÇÃO entre informações: confira se os originais '
                      'sustentam esta relação e se as condições são compatíveis. Um índice abreviado só aponta IDs; '
                      'aqui estão os itens completos e a evidência original. Não aprove inferências conciliatórias sem apoio.',
                      {'items': claims, '_context_sources': source_fragments(job, [i for c in claims for i in c['items']])},
                      f'{slot}:relations:{n}:{generation.article_hash(group)}',
                      lambda output: exact_ids([c['item_id'] for c in output['checks']], [c['id'] for c in claims], 'Conferência de relações'))
        for row, claim in zip(group, claims):
            check = next(c for c in result['checks'] if c['item_id'] == claim['id'])
            assessed.append({**row, 'check': check})
    return assessed


def related_items(job, identifiers):
    selected = set(identifiers)
    for video in job.get('apuration', {}).get('videos', []):
        for relation in video.get('relations', []):
            if selected.intersection(relation['item_ids']):
                selected.update(relation['item_ids'])
    for comparison in job.get('apuration', {}).get('comparisons', []):
        for row in comparison.get('rows', []):
            if selected.intersection(row['item_ids']):
                selected.update(row['item_ids'])
    return [item for item in job.get('apuration', {}).get('items', []) if item['id'] in selected]


def context_sources(job, payload):
    article = payload.get('article', job.get('article') or {})
    citations = set(re.findall(r'\[\[([\w-]+)\]\]', article.get('markdown', '')))
    items = [i for i in job['apuration']['items'] if any(e['source_id'] in citations for e in i['evidence'])]
    planned = [d['item_id'] for d in (job.get('plan') or {}).get('data', {}).get('dispositions', []) if d['status'] == 'used']
    relevant = related_items(job, [i['id'] for i in items] + planned)
    result = source_fragments(job, relevant)
    # Cited passages outside extracted knowledge still need review, never silently ignored.
    for ident in citations - result.keys():
        source = generation.evidence_map(job).get(ident)
        if source:
            result[ident] = source
    return result


def validate_evidence(items, mapping):
    for item in items:
        for evidence in item['evidence']:
            ref = mapping.get(evidence['source_id'])
            if not ref or not evidence['excerpt'].strip() or evidence['excerpt'] not in ref['text']:
                raise generation.GenerationResponseError('evidence_mismatch',
                    'A IA citou um trecho que não corresponde à fonte recebida. '
                    'A entrega foi rejeitada e as etapas concluídas foram preservadas.', retryable=True)


EXTRACT = '''Extraia TODO o conhecimento útil dos trechos owned deste bloco, incluindo detalhes finais,
conceitos, procedimentos, exemplos, valores com unidades, métodos, restrições e ressalvas. surroundings
serve apenas como contexto; não extraia novas informações somente dessa faixa. Não corrija termos ambíguos
por adivinhação. Nunca alegue ter visto imagens. Identifique dependência visual como limitação.
Cada informação tem topic específico e estável, statement, natureza, método, condições, quantities e
restrições. evidence.excerpt é literal, contínuo, suficientemente completo para apoiar o significado;
source_id é o ID original. Não transforme um caso particular em regra geral. Separe dados de métodos
diferentes. Não concilie divergências. Preserve informações mesmo quando a relevância à pauta for incerta.
Não reduza a fala a um resumo genérico. Se nenhuma informação é extraível, items vazio exige empty_reason.
Gaps registra ambiguidades, condições ausentes e dependências visuais, sem inventar informações.'''

EXTRACT += '''\nPreserve a contribuição concreta da fonte. Separe ações sucessivas quando elas têm
objetivos ou dependências diferentes; para cada ação conserve o que é feito, o motivo explicado,
condições, restrições e resultado observado quando presentes. Em conteúdo conceitual, comparativo
ou argumentativo, preserve definições, critérios e argumentos em vez de inventar procedimentos.
Uma menção genérica ao assunto não substitui a explicação dada pela fonte.'''

CHECK = '''Confira cada informação extraída contra a fala original e seu contexto. Copiar palavras existentes
não demonstra apoio semântico. Compare significado, atribuição, generalização, método, condições, números,
unidades e restrições. Uma fala pode ser uma experiência individual, não uma regra universal. supported
significa que a transcrição sustenta esta formulação, não que o conteúdo foi provado cientificamente.
unsupported quando a evidência não apoia o significado; uncertain quando é ambígua ou depende de imagem.
Entregue exatamente um check por item_id, incluindo o último. Não omita itens nem reescreva dados.'''


def extract(job):
    profile = job['editorial']['profile']['profile']
    inv = source_processing.inventory(job, profile)
    estimate = source_processing.estimate(job, profile)
    job['editorial']['estimate'] = estimate
    if job['editorial']['calls'] == 0 and not estimate['fits_minimum']:
        reserve(job, estimate['estimated_calls_min'], 'iniciar a apuração, a redação e a revisão', strict=True)
    previous = job.get('apuration') or {}
    if previous.get('dependencies') != dependencies(job):
        if job.get('plan'):
            job['plan']['valid'] = False
        job['apuration'] = {'valid': False, 'dependencies': dependencies(job), 'inventory': inv,
                            'items': [], 'videos': [], 'comparisons': [], 'pending': []}
    apuration = job['apuration']
    for source in job.get('sources', []):
        for source_id in source_processing.confidence_source_ids(source):
            store.issue(job, 'transcription', source_id,
                        'O reconhecimento do áudio sinalizou baixa confiança neste trecho. Confira nomes, números e termos no áudio original.',
                        source_ids=[source_id])
    db.save_job(job)
    completed = {block['id']: block for block in apuration['inventory']['blocks']}
    for block in inv['blocks']:
        saved = completed[block['id']]
        if saved.get('status') == 'checked' and saved['input_hash'] == block['input_hash']:
            continue
        reserve(job, 2 + 6, 'extrair e conferir o próximo bloco e revisar o artigo')
        mapping = generation.evidence_map(job)
        # Exactly the owned spans are evidence for extraction; surrounding text is explicitly separate.
        received = {}
        for part in block['owned']:
            ident = part['source_id']
            received.setdefault(ident, {**mapping[ident], 'text': ''})['text'] += part['text']
        payload = {'block': block, 'source_quality': next(s['quality'] for s in inv['sources'] if s['id'] == block['video_id']),
                   '_context_sources': received}
        def check_extract(result):
            if not result['items'] and not result['empty_reason'].strip():
                raise ValueError('Bloco sem informação precisa de justificativa.')
            validate_evidence(result['items'], received)
        extracted = call(job, 'extractor', BlockKnowledge, EXTRACT, payload,
                         f'extract:{block["id"]}:{block["input_hash"]}', check_extract)
        items = [dict(item, id=f'{block["id"]}k{i+1}', video_id=block['video_id'], block_id=block['id'])
                 for i, item in enumerate(extracted['items'])]
        audit = call(job, 'source_checker', KnowledgeAudit, CHECK,
                     {'items': items, 'block_context': block, 'source_quality': payload['source_quality'], '_context_sources': received},
                     f'check:{block["id"]}:{generation.article_hash(items)}',
                     lambda result: exact_ids([c['item_id'] for c in result['checks']], [i['id'] for i in items], 'Conferência'))
        checked = {c['item_id']: c for c in audit['checks']}
        for item in items:
            item['check'] = checked[item['id']]
            if item['check']['status'] != 'supported':
                store.issue(job, 'knowledge', item['id'], item['check']['reason'],
                            source_ids=[e['source_id'] for e in item['evidence']])
        for index, gap in enumerate(extracted['gaps']):
            store.issue(job, 'extraction', f'{block["id"]}:{index}', gap)
        apuration['items'] = [i for i in apuration['items'] if i['block_id'] != block['id']] + items
        saved.update(status='checked', extracted_items=len(items), summary=extracted['summary'],
                     gaps=extracted['gaps'], empty_reason=extracted['empty_reason'])
        snapshot = store.artifact(job, 'block', block['id'], {'block': saved, 'items': items}, dependencies(job))
        saved['version'] = snapshot['version']
        db.save_job(job)
    # A complete-video pass catches late caveats applying to earlier explanations.
    for source in inv['sources']:
        items = [i for i in apuration['items'] if i['video_id'] == source['id']]
        fingerprint = generation.article_hash(items)
        if any(v['id'] == source['id'] and v['input_hash'] == fingerprint for v in apuration['videos']):
            continue
        relations, summaries, gaps = [], [], []
        # Every batch sees the complete video index, so distant qualifications remain discoverable.
        index = [locator(i) for i in items]
        for n, group in enumerate(batches(items, profile['context_chars'] // 4, max_items=8)):
            reserve(job, 7, 'conferir relações do vídeo e reservar a revisão')
            def check_video(result):
                for relation in result['relations']:
                    known_ids(relation['item_ids'], [i['id'] for i in items], 'Relação entre trechos')
            result = call(job, 'source_checker', VideoContext,
                          '''Examine as relações de todo este vídeo, com foco nos itens do lote. O índice completo
permite encontrar ressalvas distantes, mudanças de método e restrições apresentadas no final. Registre
relações por IDs; não apague nem substitua informações. Nunca acrescente causas ou relações inventadas.
Identifique sequence quando a fala sustenta uma dependência ou ordem de execução entre ações;
a mera ordem dos timestamps não basta. Preserve ações intermediárias e seus motivos quando explicados.
Se a informação necessária está apenas no índice e a relação for incerta, registre gap para conferência.
Relacionar itens não declara a fala verdadeira. Não use repetição do mesmo autor como confirmação independente.''',
                          {'video_index': index, 'items': group, '_context_sources': source_fragments(job, group)},
                          f'video:{source["id"]}:{fingerprint}:{n}', check_video)
            relations.extend(result['relations']); summaries.append(result['summary']); gaps.extend(result['gaps'])
        audited_relations = check_relations(job, relations, items, f'video:{source["id"]}:{fingerprint}')
        for relation in audited_relations:
            if relation['check']['status'] != 'supported':
                gaps.append('Relação entre trechos não confirmada: ' + relation['check']['reason'])
        relations = [r for r in audited_relations if r['check']['status'] == 'supported']
        video = {'id': source['id'], 'input_hash': fingerprint, 'summary': '\n'.join(summaries),
                 'relations': relations, 'gaps': gaps}
        apuration['videos'] = [v for v in apuration['videos'] if v['id'] != source['id']] + [video]
        store.artifact(job, 'video', source['id'], video, dependencies(job))
        for n, gap in enumerate(gaps):
            store.issue(job, 'video', f'{source["id"]}:{n}', gap)
        db.save_job(job)
    apuration['valid'] = True
    apuration['pending'] = [i for i in store.issues(job) if i['status'] == 'open']
    snapshot = store.artifact(job, 'knowledge', 'all', apuration, dependencies(job))
    apuration['version'] = snapshot['version']
    db.save_job(job)


def route_topics(job):
    profile = job['editorial']['profile']['profile']
    candidates = [i for i in job['apuration']['items'] if i['check']['status'] != 'unsupported']
    if not any(i['check']['status'] == 'supported' for i in candidates):
        raise NeedsInput('Nenhuma informação foi sustentada pela conferência. Revise as fontes antes de planejar.')
    registry = defaultdict(list)
    catalog = []
    all_topics = list(dict.fromkeys(i['topic'] for i in candidates))
    for n, group in enumerate(batches(candidates, profile['context_chars'] // 3,
                                    lambda i: len(json.dumps(compact(i), ensure_ascii=False)))):
        reserve(job, 7, 'organizar assuntos e reservar a revisão')
        def valid(result):
            exact_ids([ident for topic in result['topics'] for ident in topic['item_ids']], [i['id'] for i in group], 'Organização de assuntos')
        result = call(job, 'planner', TopicRouting,
                      '''Agrupe TODOS os itens em famílias editoriais amplas que respondam à pergunta do leitor.
Use all_topics para conhecer a variedade do conjunto antes de definir as famílias. Uma família reúne
vários detalhes, passos, condições e métodos para comparação; não crie uma categoria para cada informação.
Use o menor número de famílias que mantenha comparações úteis; uma única família pode bastar.
Não crie categorias só para preencher o catálogo. O limite é oito famílias. null marca
posições não utilizadas. Inclua uma família para assuntos externos à pauta quando necessária; nenhuma
informação é descartada pela classificação. Os detalhes originais permanecem disponíveis para comparar
e planejar subseções. Se catalog já foi recebido, reutilize suas famílias para manter todos os lotes coesos.
Cada ID aparece uma vez. Reutilize nomes
do catálogo quando forem o mesmo assunto; diferenças de método ou números pertencem ao mesmo assunto
para permitir comparação. Crie um nome específico quando necessário. Não elimine itens nem decida verdade.''',
                      {'items': [compact(i) for i in group], 'existing_topics': list(registry),
                       'catalog': catalog, 'all_topics': all_topics, '_context_sources': {}},
                      f'topics:{n}:{generation.article_hash(group)}', valid)
        catalog = result['catalog']
        for topic in result['topics']:
            key = generation.normalize(topic['topic'])
            registry[key].extend(topic['item_ids'])
    return [{'topic': key, 'item_ids': ids} for key, ids in registry.items()]


def compare(job, topics):
    apuration = job['apuration']
    index = item_index(job)
    comparisons = []
    profile = job['editorial']['profile']['profile']
    for topic in topics:
        items = [index[ident] for ident in topic['item_ids']]
        rows, questions, summaries = [], [], []
        # Every item is considered, including tail batches; the compact index preserves cross-batch counterparts.
        for n, group in enumerate(batches(items, profile['context_chars'] // 4, max_items=8)):
            reserve(job, 7, 'comparar fontes e reservar a revisão')
            def valid(result):
                ids = [ident for row in result['rows'] for ident in row['item_ids']]
                known_ids(ids, topic['item_ids'], 'Comparação')
                if set(i['id'] for i in group) - set(ids):
                    raise ValueError('Comparação omitiu itens do lote.')
            result = call(job, 'source_checker', TopicComparison,
                          '''Compare os itens deste assunto e classifique relações: complementação, repetição,
concordância sob mesmas condições, métodos diferentes, divergência ou informação insuficiente. Cubra todos
os IDs do lote. O índice completo permite apontar contrapontos distantes. Confira natureza, autor, método,
unidade e condições antes de combinar. Repetir a mesma origem não é confirmação independente.
Nunca invente uma relação ideal/máximo. Preserve alternativas atribuídas ou marque pesquisa/pendência.
essential só quando a lacuna impede responder à pergunta central. treatment registra decisão justificável;
não afirme que uma pesquisa resolveu uma questão sem evidência original verificada.''',
                          {'topic': topic['topic'], 'items': group, 'topic_index': [locator(i) for i in items],
                           'video_relations': [v for v in apuration['videos'] if any(i['video_id'] == v['id'] for i in group)],
                           'source_authors': [{k: s[k] for k in ('id', 'author', 'title')} for s in apuration['inventory']['sources']],
                           '_context_sources': source_fragments(job, group)},
                          f'compare:{generation.article_hash(topic)}:{n}', valid)
            rows.extend(result['rows']); questions.extend(result['research_questions']); summaries.append(result['summary'])
        rows = check_relations(job, rows, items, f'compare:{generation.article_hash(topic)}')
        for row in rows:
            if row['check']['status'] != 'supported':
                row.update(relation='insufficient', treatment='pending',
                           explanation='Comparação não confirmada: ' + row['check']['reason'])
        comparison = {'topic': topic['topic'], 'item_ids': topic['item_ids'], 'rows': rows,
                      'summary': '\n'.join(row['explanation'] for row in rows), 'research_questions': list(dict.fromkeys(questions))}
        comparisons.append(comparison)
        for n, row in enumerate(rows):
            if row['treatment'] in ('pending', 'research'):
                store.issue(job, 'comparison', f'{generation.article_hash(topic)}:{n}', row['explanation'],
                            essential=row['essential'], source_ids=list(dict.fromkeys(
                                e['source_id'] for ident in row['item_ids'] for e in index[ident]['evidence'])))
    apuration['comparisons'] = comparisons
    apuration['pending'] = [i for i in store.issues(job) if i['status'] == 'open']
    store.artifact(job, 'comparison', 'all', comparisons, dependencies(job))
    db.save_job(job)
    return comparisons


def validate_plan(job, plan):
    plan = ArticlePlan.model_validate(plan).model_dump()
    items = item_index(job)
    priorities = plan['issue_priorities']
    priority_ids = [p['issue_id'] for p in priorities]
    known_ids(priority_ids, [i['id'] for i in job['apuration'].get('pending', [])
                            if i['status'] == 'open' and i['origin'] == 'comparison'], 'Prioridade do plano')
    if len(priority_ids) != len(set(priority_ids)):
        raise ValueError('Uma pendência precisa de uma única avaliação de prioridade.')
    assessed_priorities = {p['issue_id']: p['essential'] for p in priorities}
    if any(i['status'] == 'open' and assessed_priorities.get(i['id'], i['essential'])
           for i in job['apuration'].get('pending', [])):
        plan['ready_to_write'] = False
    exact_ids([d['item_id'] for d in plan['dispositions']], items, 'Cobertura do plano')
    section_ids = [s['id'] for s in plan['sections']]
    if len(section_ids) != len(set(section_ids)):
        raise ValueError('Cada seção precisa de um ID único.')
    used = set()
    for section in plan['sections']:
        known_ids(section['item_ids'], items, 'Seção do plano')
        used.update(section['item_ids'])
        if any(items[ident]['check']['status'] != 'supported' for ident in section['item_ids']):
            raise ValueError('Uma seção não pode utilizar informação pendente ou não sustentada.')
    for disposition in plan['dispositions']:
        if (disposition['status'] == 'used') != (disposition['item_id'] in used):
            raise ValueError('A situação de uso precisa corresponder às seções do plano.')
        if disposition['status'] == 'unsupported' and items[disposition['item_id']]['check']['status'] == 'supported':
            raise ValueError('Uma informação conferida não pode ser excluída como não sustentada sem nova apuração.')
    journey = plan.get('reader_journey')
    if journey:
        basis = journey['video_item_ids']
        known_ids(basis, guidance.video_item_ids(job), 'Guia dos vídeos')
        if len(basis) != len(set(basis)) or set(basis) - used:
            raise ValueError('O percurso precisa apontar itens únicos dos vídeos destinados às seções.')
        if job.get('sources') and not basis:
            plan['ready_to_write'] = False
    if plan['ready_to_write'] and not used:
        raise ValueError('Um plano pronto para redação precisa de conhecimento sustentado e destinado ao artigo.')
    return plan


def save_plan(job, data, *, manual=False):
    data = validate_plan(job, data)
    snapshot = store.artifact(job, 'plan', 'all', data, {
        **dependencies(job), 'knowledge': job['apuration']['version'],
        'comparison': generation.article_hash(job['apuration']['comparisons']), 'manual': manual})
    job['plan'] = {**snapshot, 'valid': True, 'input_version': store.inputs_version(job), 'manual': manual}
    db.save_job(job)
    return job['plan']


def essential_issue(job, issue):
    """Scope comparison priority to this valid plan without resolving the gap."""
    plan = job.get('plan') or {}
    if issue['origin'] == 'comparison' and plan.get('valid') and plan.get('input_version') == store.inputs_version(job):
        for priority in plan['data'].get('issue_priorities', []):
            if priority['issue_id'] == issue['id']:
                return priority['essential']
    return issue['essential']


def plan(job, *, allow_research=True):
    if job['editorial'].get('planning_version') == 1:
        from . import planning
        result = planning.plan(job, allow_research=allow_research)
        if result is not None:
            return result
    apuration = job['apuration']
    topics = route_topics(job)
    comparisons = compare(job, topics)
    research_questions = list(dict.fromkeys(q for c in comparisons for q in c['research_questions']))
    if allow_research and job['brief'].get('research') and research_questions:
        from . import research as research_flow
        if research_flow.run(job, research_questions):
            # Only new evidence or resolved issues justify revisiting paid work.
            topics = route_topics(job)
            comparisons = compare(job, topics)
    elif not job.get('research'):
        job['research'] = {'text': '', 'sources': [], 'notice': 'Pesquisa desativada ou sem pedidos específicos de apuração.'}
    index = item_index(job)
    source_guidance = guidance.source_guide(job)
    store.artifact(job, 'source_guidance', 'all', source_guidance, dependencies(job))
    topic_plans = []
    for comparison in comparisons:
        items = [index[ident] for ident in comparison['item_ids']]
        for n, group in enumerate(batches(items, job['editorial']['profile']['profile']['context_chars'] // 4)):
            reserve(job, 7, 'planejar seções e reservar a revisão')
            def valid(result):
                exact_ids([d['item_id'] for d in result['dispositions']], [i['id'] for i in group], 'Plano de assunto')
                for section in result['sections']:
                    known_ids(section['item_ids'], [i['id'] for i in group], 'Seção')
                    if any(index[ident]['check']['status'] != 'supported' for ident in section['item_ids']):
                        raise ValueError('Informação incerta não pode entrar no artigo.')
            output = call(job, 'planner', TopicPlan,
                          '''Planeje a contribuição deste assunto à pauta. Cada seção responde uma pergunta, tem
finalidade, pré-requisitos, condições, transição e IDs sustentados. Dê uma situação e justificativa para
TODOS os itens: used, duplicate, out_of_scope, unsupported ou pending. Não use informação uncertain ou
unsupported. Preserve exemplos, ressalvas distantes e métodos diferentes. Consulte as relações e
contrapontos. Se uma fonte não contribui, explique a exclusão; não há quota de citações por vídeo.
Use os vídeos como guia do percurso. Preserve dependências reais e explicações indispensáveis, mesmo
quando atravessam famílias de assuntos. Reorganize digressões da fala para a utilidade e gênero da pauta,
sem embaralhar ações. Pesquisa entra para responder lacunas ou conferir pontos específicos: material
complementar, repetido ou externo à pergunta não precisa ser usado só por ter sido extraído. Justifique
duplicate e out_of_scope sem apagar o inventário. A meta de palavras pertence ao ARTIGO INTEIRO.
Selecione o que ajuda a responder à pergunta dentro dessa extensão: ações indispensáveis, explicações,
condições e exemplos úteis. Digressões, detalhes de fases posteriores e informações de outros objetivos
podem ser out_of_scope mesmo quando verdadeiras. Não transforme todo item extraído em obrigação de
parágrafo. Um mesmo parágrafo pode explicar vários itens relacionados. Não escreva o artigo.''',
                          {'items': group, 'comparison': comparison, 'source_review': {'summary': comparison['summary']},
                           'video_relations': apuration['videos'], 'pending': apuration['pending'],
                           '_context_sources': source_fragments(job, group)},
                          f'topicplan:{generation.article_hash(comparison)}:{n}', valid)
            # Stable namespaces prevent equal section IDs generated for different topics.
            for section in output['sections']:
                section['id'] = f't{len(topic_plans)+1}_{section["id"]}'
            topic_plans.append(output)
    dispositions = [d for p in topic_plans for d in p['dispositions']]
    handled = {d['item_id'] for d in dispositions}
    dispositions.extend({'item_id': i['id'], 'status': 'unsupported', 'reason': i['check']['reason']}
                        for i in apuration['items'] if i['id'] not in handled)
    reserve(job, 7, 'consolidar o plano e reservar a revisão')
    output = call(job, 'planner', PlanStructure,
                  guidance.FORMAT_POLICY + '''\nConsolide o plano dos assuntos num artigo com início, desenvolvimento e fim. Respeite
pergunta, público, intenção, gênero e exclusões. Reordene ou reúna seções para formar raciocínio contínuo.
Não apague condições ou detalhes essenciais. Preserve os IDs e situações do inventário de dispositions;
o aplicativo conserva suas justificativas. sections usa todos os IDs com status used. A abertura situa a
pergunta; o fechamento responde ou explicita limites sem inventar uma conclusão. Registre pendências,
especialmente questões essenciais ainda abertas. ready_to_write é false se falta informação indispensável
para responder à pergunta central. available_items contém o inventário conferido completo; confira nele
se uma informação realmente falta antes de considerá-la indispensável. As observações dos colegas são
hipóteses editoriais e não substituem esse inventário. Considere o escopo da pauta: diferenças de contexto
podem ser explicadas separadamente, e detalhes de fases posteriores não são automaticamente essenciais.
ready_to_write precisa ser false se alguma prioridade avaliada permanecer essencial e aberta.
reader_journey explica a tarefa real do leitor em goal, escolhe kind e justifica em reason a estrutura
com base no briefing e nos vídeos. video_item_ids identifica os itens dos vídeos usados como guia.
Considere main_question e instructions, não apenas o rótulo amplo de genre. Em um percurso sequencial,
cada etapa identificável ocupa o lugar de sua dependência, com ação, finalidade e critério de avanço
quando sustentados. Nos demais percursos, organize conceitos, critérios ou argumentos pertinentes.
Detalhes de pesquisa devem se encaixar nesse percurso. Agrupamentos temáticos são instrumentos de
apuração, não a ordem obrigatória do artigo. Não crie uma enciclopédia paralela nem uma seção para cada
fonte. Uma sequência ensinada não deve desaparecer na consolidação entre assuntos.
A abertura e o fechamento já têm campos próprios. Não crie outra introdução ou conclusão nas seções.
Destine cada informação ao ponto em que ela será desenvolvida; uma retomada em outra seção é contexto,
não obrigação de repetir a explicação inteira. Seções de síntese precisam acrescentar uma relação útil.
Não escreva o artigo nem concilie divergências sem apoio.''',
                  {'topic_plans': [{'sections': [{k: s[k] for k in ('title', 'question', 'item_ids')}
                                               for s in p['sections'] if s['item_ids']]} for p in topic_plans],
                   'available_items': [compact(item) for item in index.values()],
                   'source_guidance': source_guidance,
                   'dispositions': [{'item_id': d['item_id'], 'status': d['status']} for d in dispositions],
                   'pending': [i for i in apuration['pending'] if i.get('essential')],
                   '_context_sources': {}}, f'plan:{generation.article_hash(topic_plans)}',
                  lambda result: validate_plan(job, {**result, 'dispositions': dispositions}))
    save_plan(job, {**output, 'dispositions': dispositions})
    return job['plan']


def dossier(job, identifiers=None):
    wanted = set(identifiers) if identifiers is not None else {
        d['item_id'] for d in job['plan']['data']['dispositions'] if d['status'] == 'used'}
    items = [i for i in job['apuration']['items'] if i['id'] in wanted]
    return {'main_question': job['plan']['data']['main_question'], 'summary': job['plan']['data']['opening'],
            'claims': [{k: i[k] for k in ('statement', 'kind', 'evidence')} for i in items],
            'examples': [i['statement'] for i in items if i['information_type'] == 'exemplo'],
            'conflicts': [c['summary'] for c in job['apuration']['comparisons']
                          if any(r['relation'] == 'divergence' for r in c['rows'])],
            'gaps': [i['reason'] for i in store.issues(job) if i['status'] == 'open'],
            'outline': [s['title'] for s in job['plan']['data']['sections']]}


WRITE_SECTION = guidance.FORMAT_POLICY + '\n\n' + '''Escreva somente a parte solicitada em Markdown, sem H1. Respeite a pergunta e o gênero
do artigo e a função desta seção. Cada parágrafo desenvolve uma ideia com contexto e ligação real com o
anterior. Os leitores não assistiram aos vídeos. Preserve métodos, unidades, condições, atribuições,
exemplos e ressalvas. article_route apresenta o percurso completo; escreva esta seção no seu lugar,
sem antecipar etapas posteriores nem reiniciar o caminho já desenvolvido. Se o percurso é sequencial,
explique a ação, motivo, condições e critério de avanço que as fontes sustentam. Em outros percursos,
desenvolva conceitos, critérios ou argumentos conforme o plano. Não transforme pesquisa complementar
em outro artigo. Para seções de desenvolvimento, use exatamente section.title no H2 de abertura.
Use word_budget como orientação de extensão desta parte. Se section.id é opening, escreva apenas uma
abertura curta que situe a dúvida e o percurso da explicação, sem desenvolver os procedimentos ou
antecipar o artigo inteiro. Se é closing, encerre o raciocínio sem repetir os procedimentos nem acrescentar
informações novas. Nas seções de desenvolvimento, responda somente a pergunta desta seção;
os contrapontos servem para conferir condições e alternativas, não para escrever todos os assuntos.
Não repita explicações das partes anteriores; use-as para manter continuidade.
Nunca complete lacunas de memória nem invente relações conciliatórias. Use apenas informações
sustentadas indicadas no plano desta parte. Toda afirmação factual relevante recebe [[source_id]].
items e counterpoints_and_conditions conservam os fatos e suas condições, com source_ids para
localizar as evidências literais em fontes_para_conferencia; não repita nem invente uma atribuição.
Retorne used_item_ids para TODAS as informações efetivamente desenvolvidas; não marque uso por ter
apenas lido o item. Não inclua instruções ao editor, relatório de limitações ou metadados no corpo.'''

SECTION_COVERAGE = '''
required_item_ids identifica o que ainda precisa ser desenvolvido neste artigo. already_developed_item_ids
identifica informações já desenvolvidas nas partes anteriores: podem ser retomadas quando a pergunta desta
seção exigir, mas não repita a explicação inteira para preencher usage. Um item apenas contextual mantém
usage false. A revisão factual posterior conferirá a cobertura do artigo inteiro independentemente.
Se section_repair estiver presente, a primeira redação já está salva. Reescreva SOMENTE esta seção,
integrando as informações de missing_item_ids com suas fontes, condições e ressalvas. Preserve o conteúdo
válido do rascunho e sua continuidade; não acrescente um apêndice desconectado nem reinicie o artigo.
O texto substitui o rascunho inteiro: usage precisa descrever o conteúdo desta versão, não da anterior.
Se não conseguir desenvolver algo com apoio, mantenha usage false, sem inventar uma explicação.'''


def missing_section_items(section, output, developed):
    """Coverage is cumulative; a planned reprise is not a second mandatory explanation."""
    return sorted(set(section['item_ids']) - set(developed) - set(output['used_item_ids']))


def write_section(job, section, payload, slot, developed, remaining_parts, on_draft=None):
    from . import engine
    allowed = set(payload['_context_sources'])
    def validate(result):
        known_ids(result['used_item_ids'], section['item_ids'], 'Uso na redação')
        known_ids(re.findall(r'\[\[([\w-]+)\]\]', result['markdown']), allowed, 'Citação da seção')
    # Completed v1 deliveries have already passed the stricter per-section check.
    # Require the exact original cache identity; never reuse just by section name.
    original = {**payload, '_local_context': True}
    cached = engine.cached_invocation(job, 'writer', original, slot=slot)
    if cached:
        output = DraftSection.model_validate(cached['output']).model_dump()
        validate(output)
        if on_draft:
            on_draft(output)
        return output, missing_section_items(section, output, developed)

    payload = {**payload, '_draft_contract': 2,
               'required_item_ids': sorted(set(section['item_ids']) - set(developed)),
               'already_developed_item_ids': sorted(set(section['item_ids']) & set(developed))}
    current_slot = slot + ':v2'

    def request(material, request_slot):
        if not engine.cached_invocation(job, 'writer', {**material, '_local_context': True}, slot=request_slot):
            # Reserve only pending writing work plus metadata and final review.
            reserve(job, remaining_parts + 7, 'concluir as partes restantes e reservar a revisão')
        budget_instruction = ('''\nword_budget é a parcela desta parte dentro da meta total do artigo.
Responda de forma direta, podendo terminar antes. Não amplie com introduções, elogios ou recapitulações.
Use etapas numeradas quando esta parte desenvolver ações de um tutorial; mantenha parágrafos curtos
que expliquem as ações e condições apoiadas pelas fontes. Não force listas em outros gêneros.
''' if material.get('_composition_contract') else '')
        return call(job, 'writer', DraftSection, WRITE_SECTION + SECTION_COVERAGE + budget_instruction,
                    material, request_slot, validate)
    output = request(payload, current_slot)
    if on_draft:
        on_draft(output)
    missing = missing_section_items(section, output, developed)
    if missing:
        deps = {**dependencies(job), 'plan': job['plan']['version'],
                'prior_text': generation.article_hash(payload['prior_text'])}
        # Keep the paid, structurally valid response before attempting an editorial repair.
        store.artifact(job, 'draft_section_partial', section['id'],
                       {'draft': output, 'missing_item_ids': missing}, deps)
        repair = {'draft': output, 'missing_item_ids': missing}
        repaired = request({**payload, 'section_repair': repair},
                           current_slot + ':repair:' + generation.article_hash(repair))
        repaired_missing = missing_section_items(section, repaired, developed)
        # A replacement cannot borrow coverage from text that it discarded.
        if len(repaired_missing) <= len(missing):
            output, missing = repaired, repaired_missing
        store.artifact(job, 'draft_section_repair', section['id'],
                       {'draft': output, 'missing_item_ids': missing,
                        'attempted_missing_item_ids': repaired_missing}, deps)
    # Self-reported omissions remain explicit; the exhaustive factual review is
    # the approval gate. They do not trigger another blind full-section retry.
    return output, missing


def write(job):
    saved_plan = job.get('plan') or {}
    if not saved_plan.get('valid') or saved_plan.get('input_version') != store.inputs_version(job):
        raise ValueError('O plano está ausente ou desatualizado. Planeje novamente antes de redigir.')
    plan_data = validate_plan(job, saved_plan['data'])
    essential = [i for i in store.issues(job) if i['status'] == 'open' and essential_issue(job, i)]
    if essential or not plan_data['ready_to_write']:
        job['editorial']['decision'] = {'decision': 'needs_input',
                                      'summary': 'A apuração tem informação indispensável pendente.', 'findings': []}
        db.save_job(job)
        return None
    profile = job['editorial']['profile']['profile']
    used = [i for i in job['apuration']['items'] if any(
        d['item_id'] == i['id'] and d['status'] == 'used' for d in plan_data['dispositions'])]
    job['dossier'] = dossier(job)
    if job['editorial'].get('composition_version') == 1:
        from . import composition
        article = composition.write(job, plan_data, used)
        if article is not None:
            return article
    shared = {'plan': plan_data, 'items': used, 'source_relations': job['apuration']['videos'],
              '_context_sources': source_fragments(job, related_items(job, [i['id'] for i in used]))}
    # Leave room for the common instructions, profile and output. Large drafts use section calls.
    if not job['editorial'].get('composition_version') and len(json.dumps(shared, ensure_ascii=False)) < profile['context_chars'] // 2:
        reserve(job, 7, 'redigir e reservar a revisão')
        def deliver(current):
            token = generation.agent_scope.set({**generation.agent_scope.get(), 'context_sources': shared['_context_sources']})
            try:
                return generation.write_article(current)
            finally:
                generation.agent_scope.reset(token)
        from . import engine
        return engine.invoke(job, 'writer', shared, deliver, f'writer:{saved_plan["version"]}')[0]
    sections = plan_data['sections']
    parts, applied, coverage = [], [], []
    index = item_index(job)
    segments = [{'id': 'opening', 'title': '', 'purpose': plan_data['opening'],
                 'item_ids': [], 'context_item_ids': sections[0]['item_ids']}, *sections,
                {'id': 'closing', 'title': '', 'purpose': plan_data['closing'],
                 'item_ids': [], 'context_item_ids': sections[-1]['item_ids']}]
    assigned = sum(len(s['item_ids']) for s in sections) or 1
    budgets = None
    if job['editorial'].get('composition_version') == 1:
        from .composition import section_budgets
        budgets = section_budgets(segments, job['brief']['target_words'])
    for number, section in enumerate(segments):
        items = related_items(job, section.get('context_item_ids', section['item_ids']))
        payload = {'section': section, 'items': [writing_item(index[i]) for i in section['item_ids']],
                   'article_route': guidance.article_route(plan_data),
                   'word_budget': 120 if section['id'] in ('opening','closing') else max(120,
                       round(job['brief']['target_words'] * .8 * len(section['item_ids']) / assigned)),
                   'counterpoints_and_conditions': [writing_item(i) for i in items if i['id'] not in section['item_ids']],
                   'used_before': applied,
                   'prior_text': '\n\n'.join(parts), '_context_sources': source_fragments(job, items)}
        if budgets is not None:
            payload.update(word_budget=budgets[number], target_words_total=job['brief']['target_words'],
                           _composition_contract=1)
        from . import drafts
        def expose(output):
            text = output['markdown']
            if section.get('title') and not text.lstrip().startswith('#'):
                text = '## ' + section['title'] + '\n\n' + text
            drafts.partial(job, '\n\n'.join([*parts, text]), number + 1, len(segments))
        output, missing = write_section(job, section, payload,
                          f'write:{saved_plan["version"]}:{section["id"]}', applied, len(segments) - number, expose)
        coverage.append({'section_id': section['id'], 'used_item_ids': output['used_item_ids'],
                         'already_developed_item_ids': sorted(set(section['item_ids']) & set(applied)),
                         'missing_item_ids': missing})
        text = output['markdown']
        if section.get('title') and not text.lstrip().startswith('#'):
            text = '## ' + section['title'] + '\n\n' + text
        parts.append(text); applied.extend(output['used_item_ids'])
        store.artifact(job, 'draft_section', section['id'], output,
                       {**dependencies(job), 'plan': saved_plan['version']})
        drafts.partial(job, '\n\n'.join(parts), number + 1, len(segments))
    markdown = '\n\n'.join(parts)
    store.artifact(job, 'draft_coverage', 'all', {'sections': coverage,
                   'missing_item_ids': sorted({i['id'] for i in used} - set(applied)),
                   'notice': 'Declaração da redação; a revisão factual ainda precisa conferir o artigo completo.'},
                   {**dependencies(job), 'plan': saved_plan['version'], 'markdown': generation.article_hash(markdown)})
    metadata = call(job, 'writer', ArticleMetadata,
                    'Produza metadados para o artigo recebido. Preserve o tema e a promessa sustentada pelo texto. '
                    'Não acrescente parâmetros, benefícios ou certeza ausentes. Não reescreva o corpo.',
                    {'markdown': markdown, 'plan': plan_data, '_context_sources': {}},
                    f'metadata:{generation.article_hash(markdown)}')
    metadata['slug'] = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', metadata['slug']).encode(
        'ascii', 'ignore').decode().lower()).strip('-') or 'artigo-' + job['id'][:8]
    article = Article.model_validate({**metadata, 'markdown': markdown}).model_dump()
    drafts.checkpoint(job, article, complete=True)
    return article


def writing_item(item):
    """Keep complete checked facts and source links; source text is supplied once."""
    return {**compact(item), 'source_ids': list(dict.fromkeys(e['source_id'] for e in item['evidence']))}


def passages(article):
    """Exhaustive partition, including headings, title and every metadata field."""
    result = []
    for field in ('title', 'seo_title', 'slug', 'meta_description', 'excerpt', 'tags', 'markdown'):
        value = article[field]
        values = value if isinstance(value, list) else re.split(r'\n\s*\n', value) if field == 'markdown' else [value]
        for value in values:
            for _, _, text in source_processing.parts(value, 2600):
                if text.strip():
                    result.append({'id': f'p{len(result)+1}', 'field': field, 'text': text})
    return result


def assessment_items(job, group):
    items = item_index(job)
    sources = {ident for part in group for ident in re.findall(r'\[\[([\w-]+)\]\]', part['text'])}
    selected = {i['id'] for i in items.values() if any(e['source_id'] in sources for e in i['evidence'])}
    # Metadata and uncited claims are assessed against the entire planned factual basis.
    if any(not re.search(r'\[\[[\w-]+\]\]', part['text']) for part in group):
        selected.update(d['item_id'] for d in (job.get('plan') or {}).get('data', {}).get('dispositions', [])
                        if d['status'] == 'used')
    return related_items(job, selected)


def factual_review(job, round_index):
    from . import engine
    profile = job['editorial']['profile']['profile']
    all_passages = passages(job['article'])
    article_version = generation.article_hash(job['article'])
    assessments = []
    groups = list(batches(all_passages, min(6500, profile['context_chars'] // 8)))
    requests = []
    for n, group in enumerate(groups):
        items = assessment_items(job, group)
        sources = source_fragments(job, items)
        # Include original cited segments even if extraction did not keep a matching item.
        for part in group:
            for ident in re.findall(r'\[\[([\w-]+)\]\]', part['text']):
                if ident not in sources and ident in generation.evidence_map(job):
                    sources[ident] = generation.evidence_map(job)[ident]
        payload = {'passages': group, 'items': [writing_item(i) for i in items],
                   'article_title': job['article']['title'], '_context_sources': sources, '_local_context': True}
        if job['editorial'].get('composition_version') == 1:
            from .composition import qualifications
            payload['required_qualifications'] = qualifications(items)
            # Prior approvals are not evidence. Let the reviewer judge the
            # original passages rather than echo another agent's verdict.
            payload['items'] = [{k: v for k, v in item.items() if k != 'check'} for item in payload['items']]
        requests.append((group, sources, payload, f'semantic:{article_version}:{n}'))
    # Only exact, validated cache hits reduce the reserve. A matching slot name
    # alone is insufficient after source, plan, profile or article changes.
    pending_calls = sum(not engine.cached_invocation(job, 'fact_reviewer', payload, slot=slot)
                        for _, _, payload, slot in requests)
    pending_calls += not engine.cached_invocation(job, 'fact_reviewer',
        {'article': job['article'], '_context_sources': {}}, slot=f'fact_reviewer:{round_index}:{article_version}')
    pending_calls += not engine.cached_invocation(job, 'readability_reviewer',
        engine.reading_payload(job), slot=f'readability_reviewer:{round_index}')
    reserve(job, pending_calls + 1, 'conferir todos os trechos e concluir a revisão')
    for n, (group, sources, payload, slot) in enumerate(requests):
        def valid(result):
            exact_ids([a['passage_id'] for a in result['assessments']], [p['id'] for p in group], 'Revisão dos trechos')
            for position, assessment in enumerate(result['assessments']):
                known_ids(assessment['used_item_ids'], item_index(job), 'Cobertura do artigo')
                validate_evidence([assessment], sources)
                validate_evidence([assessment], generation.evidence_map(job))
                if assessment['status'] == 'supported' and not assessment['evidence']:
                    raise generation.GenerationResponseError('assessment_mismatch',
                        'A conferência declarou apoio factual sem indicar evidências. O trecho não foi aprovado.',
                        retryable=True, diagnostics=[{'type': 'supported_without_evidence', 'position': [position]}])
                if assessment['status'] != 'supported' and assessment['used_item_ids']:
                    raise generation.GenerationResponseError('assessment_mismatch',
                        'A conferência contou informação sem apoio como utilizada. O trecho não foi aprovado.',
                        retryable=True, diagnostics=[{'type': 'unconfirmed_coverage', 'position': [position]}])
        output = call(job, 'fact_reviewer', PassageAudit,
                      '''Audite TODOS os trechos do lote, um assessment por passage_id, inclusive títulos e
metadados. Confira TODAS as afirmações de cada trecho: significado, atribuição, condições, quantidades,
unidades, causalidade e generalizações. Um trecho existente pode não sustentar a conclusão. Se uma
afirmação material não tem apoio, todo o trecho é unsupported/uncertain; não aprove apenas sua parte fácil.
supported exige evidência literal das fontes fornecidas. not_factual apenas quando o trecho não contém
afirmação verificável (por exemplo um subtítulo neutro). Quantifique used_item_ids somente para informações
efetivamente desenvolvidas com fidelidade no trecho. Confira ressalvas distantes e contrapontos do plano.
Não use aprovação anterior como prova, nem fontes excluídas como verdade. Texto depende de demonstração
visual ausente é uncertain. O artigo pode preservar alternativas atribuídas e divergências reais.
Quando required_qualifications estiver presente, confira cada condição, restrição e limitação que
delimita a afirmação. Não conte um item em used_item_ids se a redação omitiu uma ressalva material,
transformou uma opção em obrigação ou atribuiu certeza a uma verificação limitada. A presença da
citação não comprova fidelidade. Avalie o texto do artigo, não a intenção declarada pelo redator.''',
                      payload, slot, valid)
        assessments.extend(output['assessments'])
        store.artifact(job, 'semantic_review', f'{article_version}:{n}', output,
                       {**dependencies(job), 'article': article_version, 'plan': (job.get('plan') or {}).get('version')})
    exact_ids([a['passage_id'] for a in assessments], [p['id'] for p in all_passages], 'Cobertura da revisão')
    lookup = {p['id']: p for p in all_passages}
    findings, supported = [], []
    used = set()
    for assessment in assessments:
        passage = lookup[assessment['passage_id']]['text']
        if assessment['status'] in ('unsupported', 'uncertain'):
            findings.append({'severity': 'blocking', 'passage': passage, 'reason': assessment['reason'],
                             'suggestion': 'Confira as fontes e corrija, atribua ou remova a informação sem apoio.',
                             'source_ids': [e['source_id'] for e in assessment['evidence']],
                             'recipient': 'apuration', 'origin': 'semantic_review'})
        if assessment['status'] == 'supported':
            used.update(assessment['used_item_ids'])
            supported.append({'statement': passage, 'kind': 'fato', 'evidence': assessment['evidence']})
    coverage = []
    for disposition in (job.get('plan') or {}).get('data', {}).get('dispositions', []):
        status = disposition['status']
        actual = 'used' if disposition['item_id'] in used else status if status != 'used' else 'pending'
        coverage.append({**disposition, 'planned_status': status, 'status': actual})
        if status == 'used' and actual != 'used':
            item = item_index(job)[disposition['item_id']]
            findings.append({'severity': 'blocking', 'passage': '',
                             'reason': 'Informação prevista não foi desenvolvida com apoio: ' + item['statement'],
                             'suggestion': 'Complete a explicação ou ajuste o plano com uma exclusão justificada.',
                             'source_ids': [e['source_id'] for e in item['evidence']], 'recipient': 'writing', 'origin': 'coverage'})
    cited_sources = {e['source_id'] for assessment in assessments for e in assessment['evidence']}
    for issue in store.issues(job):
        audio_uncertain = issue['origin'] == 'transcription' and bool(set(issue['source_ids']) & cited_sources)
        if issue['status'] == 'open' and (essential_issue(job, issue) or audio_uncertain):
            findings.append({'severity': 'blocking', 'passage': '', 'reason': issue['reason'],
                             'suggestion': 'Resolva explicitamente a pendência na apuração.', 'source_ids': issue['source_ids'],
                             'recipient': 'apuration', 'origin': 'pending_issue',
                             'issue_id': issue['id'], 'issue_origin': issue['origin']})
    # Global review uses the complete article and the exhaustive semantic reports.
    def global_review(current):
        output = generation.structured(current, Review,
            '''Revise a direção editorial, a continuidade GLOBAL e o contexto dos parágrafos do artigo completo.
Confira início, desenvolvimento e fechamento, título e metadados. Os lotes semânticos já avaliaram todas
as afirmações; não dispense seus bloqueios nem invente evidências. Avalie costuras entre seções e
contradições internas, distinguindo alternativas atribuídas de erros. Não reescreva o texto. Copie o
título e passagens literalmente. supported_claims pode ficar vazio; a cobertura factual é consolidada
pelo aplicativo. Preferência estilística é warning, quebra material de compreensão é blocking.''',
            'fact_reviewer', {'article': current['article'], 'semantic_findings': findings,
                              'coverage': coverage,
                              'article_route': guidance.article_route(current['plan']['data']) if current.get('plan') else None,
                              '_context_sources': {}, '_local_context': True})
        output['findings'].extend(generation.review_integrity_findings(current, output))
        if not output['editorial_alignment']['matches_brief']:
            output['findings'].append({'severity': 'blocking', 'passage': output['editorial_alignment']['passage'],
                'reason': output['editorial_alignment']['reason'], 'suggestion': 'Ajuste o artigo à direção editorial.',
                'source_ids': [], 'origin': 'editorial_alignment'})
        return output
    result = engine.invoke(job, 'fact_reviewer', {'article': job['article'], '_context_sources': {}},
                           global_review, f'fact_reviewer:{round_index}:{article_version}')[0]
    result['findings'].extend(findings)
    result['findings'].extend(generation.deterministic_findings(job))
    result['supported_claims'] = supported
    result.update(article_hash=article_version, reviewed_at=db.now(),
                  semantic_coverage={'passages': len(all_passages), 'assessed': len(assessments),
                                     'batches': len(groups), 'assessments': assessments},
                  coverage=coverage, flow_version=VERSION)
    job['coverage'] = {'article_hash': article_version, 'items': coverage, 'blocks': job['apuration']['inventory']['blocks']}
    store.artifact(job, 'coverage', article_version, job['coverage'], dependencies(job))
    db.save_job(job)
    return result
