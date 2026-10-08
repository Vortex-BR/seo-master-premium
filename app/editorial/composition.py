"""Compose one reader-facing article before editing; keep paid drafts recoverable."""
import re
import math
import unicodedata
from openai import APIConnectionError

from .. import generation
from ..schemas import Article
from . import drafts, engine, guidance, store, text_checks
from .contracts import DraftArticle

VERSION = 1


def section_budgets(segments, total):
    """Allocate one total budget; no per-section minimum that inflates the article."""
    weights, seen = [], set()
    for section in segments:
        fresh = set(section['item_ids']) - seen
        weights.append(.6 if section['id'] == 'opening' else .4 if section['id'] == 'closing' else max(.5, len(fresh)))
        seen.update(fresh)
    if not weights:
        return []
    exact = [total * weight / sum(weights) for weight in weights]
    values = [int(value) for value in exact]
    for index in sorted(range(len(values)), key=lambda i: exact[i] - values[i], reverse=True)[:total - sum(values)]:
        values[index] += 1
    return values


INSTRUCTION = guidance.FORMAT_POLICY + '\n\n' + '''Você é um editor que transforma a explicação falada de um criador de conteúdo em um
artigo de blog claro, envolvente e escaneável, em português brasileiro. Escreva o artigo completo em
uma única passada coesa e entregue os metadados nos campos próprios.

A fonte EXCLUSIVA de conteúdo, didática e narrativa é o que o criador explicou nos vídeos.
Preserve a espontaneidade, o vocabulário acessível e o raciocínio humano da fala. MANTENHA as
metáforas práticas, analogias, experiências, dicas e alertas em source_spoken_insight. Adapte a
linguagem oral para leitura em tela, retirando pedidos de like, inscrição, vinhetas e vícios de fala.
Não invente explicações, tópicos, seções, dados, experiências da marca ou benefícios.

agent_background_knowledge contém apenas contexto interno para compreender termos citados no vídeo.
Esse material NUNCA pode fornecer fatos, exemplos, recomendações ou seções ao artigo, e NUNCA pode
ser citado como evidência. Não use citações de pesquisa como [[rn1]]. Se uma explicação técnica não
está sustentada pela fala, omita a ampliação; entender o termo não autoriza acrescentar conteúdo.

PROIBIDO usar linguagem artificial como 'é crucial ressaltar', 'no mundo contemporâneo',
'vale a pena destacar', 'vale ressaltar', 'é imprescindível', 'em suma' ou 'um divisor de águas'.
Use frases diretas, parágrafos curtos, H2 e H3 objetivos. Listas organizam um passo a passo que o
criador ensinou; desenvolva explicações em parágrafos. A extensão é uma meta para o artigo inteiro:
termine quando a resposta estiver completa, sem criar conteúdo para preencher palavras.

Abra com uma resposta direta e credite naturalmente o autor/criador na introdução, conforme
creator_voice. Atribua experiências, opiniões e dicas ao especialista, por exemplo 'O criador
destaca que...' ou 'Como explicado no vídeo...'. Não alegue ter assistido a imagens não analisadas.
Preserve a voz explicativa sem repetir a atribuição a cada frase. Use as referências de tempo
fornecidas como [03:45] junto à explicação correspondente quando houver timestamp disponível;
nunca invente nomes, credenciais ou marcações de tempo.

O plano orienta cobertura e ordem. presentation e subheadings indicam o formato das seções;
integre uma informação repetida uma vez, preservando relações e condições. counterpoints mantém
alternativas e ressalvas dos vídeos, sem exigir novos assuntos. Quando criadores usam métodos
diferentes, apresente as alternativas com atribuição; não invente uma conciliação. Respeite
required_qualifications no mesmo trecho da afirmação. Não transforme opção em obrigação, checagem
em garantia ou experiência individual em regra universal. Escreva para quem não assistiu ao vídeo.

Entregue paragraphs em ordem de leitura, incluindo subtítulos Markdown e listas quando adequados.
Cada bloco declara os IDs dos trechos de vídeo que sustentam suas afirmações. Use quebras de linha
reais nas listas. usage registra apenas itens desenvolvidos com fidelidade; ler um item não é usá-lo.
A conferência factual será independente. Entregue a versão completa, sem notas ao editor.'''


def _article(output, job):
    article = Article.model_validate({k: v for k, v in output.items() if k != 'used_item_ids'}).model_dump()
    article['slug'] = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', article['slug']).encode(
        'ascii', 'ignore').decode().lower()).strip('-') or 'artigo-' + job['id'][:8]
    return article


def write(job, plan, used):
    from . import workflow
    video_ids = {segment['id'] for source in job.get('sources', []) for segment in source.get('segments', [])}
    if any(not item.get('evidence') or any(e['source_id'] not in video_ids for e in item['evidence']) for item in used):
        raise ValueError('A redação aceita apenas itens sustentados pelos trechos originais dos vídeos.')
    all_items = [item for item in workflow.related_items(job, [item['id'] for item in used])
                 if item.get('evidence') and all(e['source_id'] in video_ids for e in item['evidence'])]
    ids = {item['id'] for item in used}
    sources = {ident: source for ident, source in workflow.source_fragments(job, all_items).items()
               if ident in video_ids}
    video_first = bool(job.get('editorial', {}).get('video_first'))
    payload = {'_composition_contract': VERSION, '_local_context': True,
               'article_route': guidance.article_route(plan),
               'items': [workflow.writing_item(item) for item in used],
               'counterpoints': [workflow.writing_item(item) for item in all_items if item['id'] not in ids],
               'target_words_total': job['brief']['target_words'], '_context_sources': sources,
               'required_qualifications': qualifications(used),
               'creator_voice': creator_voice(job, sources)}
    slot = f'compose:{VERSION}:{job["plan"]["version"]}'

    def validate(output):
        workflow.known_ids(output['used_item_ids'], ids, 'Uso no artigo')
        workflow.known_ids(re.findall(r'\[\[([\w-]+)\]\]', output['markdown']), sources, 'Citações do artigo')
        _article(output, job)

    # Measure the complete wire request before any paid work. New cycles refuse
    # oversized drafts explicitly; their composition is always one full article.
    cached = engine.cached_invocation(job, 'writer', payload, slot=slot)
    if not cached:
        scope, _, _ = engine.invocation_inputs(job, 'writer', payload, None, slot)
        token = generation.agent_scope.set(scope)
        try:
            generation.prepare_structured(job, DraftArticle, INSTRUCTION, 'composition', payload)
        except generation.ContextLimitExceeded:
            if video_first:
                raise
            return None
        finally:
            generation.agent_scope.reset(token)
        workflow.reserve(job, 1, 'redigir o artigo')
    output = workflow.call(job, 'writer', DraftArticle, INSTRUCTION, payload, slot, validate)
    deps = {**workflow.dependencies(job), 'plan': job['plan']['version']}
    store.artifact(job, 'composition_draft', 'all', output, deps)
    article = _article(output, job)
    drafts.checkpoint(job, article, complete=True)
    missing = sorted(ids - set(output['used_item_ids']))
    report = text_checks.analyze({**job, 'article': article})
    issues = [finding for finding in report['findings'] if finding.get('auto_repair', True)]
    if not video_first and (missing or issues):
        repair = {'draft': output, 'missing_item_ids': missing, 'delivery_checks': report}
        repair_payload = {**payload, 'repair': repair}
        repair_slot = slot + ':repair:' + generation.article_hash(repair)
        cached_repair = engine.cached_invocation(job, 'writer', repair_payload, slot=repair_slot)
        failed_before = any(a['dependencies'] == deps and a['data'].get('slot') == repair_slot
                            for a in store.artifacts(job['id'], 'composition_repair_failure'))
        # One editorial repair only. Lack of budget/context leaves a reviewable
        # draft and explicit findings, never discards it or starts a retry loop.
        if not failed_before and (cached_repair or workflow.remaining(job) >= 8):
            try:
                repaired = workflow.call(job, 'writer', DraftArticle, INSTRUCTION,
                                         repair_payload, repair_slot, validate)
            except generation.ContextLimitExceeded:
                repaired = None
            except (generation.GenerationResponseError, APIConnectionError) as exc:
                # A failed optional repair must not erase a usable paid draft.
                # The independent review still checks every unresolved defect.
                store.artifact(job, 'composition_repair_failure', 'all',
                    {'reason': getattr(exc, 'reason', 'connection'), 'draft_preserved': True, 'slot': repair_slot}, deps)
                repaired = None
            if repaired:
                candidate = _article(repaired, job)
                candidate_report = text_checks.analyze({**job, 'article': candidate})
                candidate_missing = sorted(ids - set(repaired['used_item_ids']))
                def defects(missing_items, checks):
                    return (len(missing_items), sum(f['severity'] == 'blocking' for f in checks['findings']),
                            len(checks['findings']))
                before, after = defects(missing, report), defects(candidate_missing, candidate_report)
                accepted = all(new <= old for new, old in zip(after, before)) and after != before
                store.artifact(job, 'composition_repair', 'all',
                               {'draft': repaired, 'accepted': accepted}, deps)
                if accepted:
                    output, article, missing, report = repaired, candidate, candidate_missing, candidate_report
                    drafts.checkpoint(job, article, complete=True)
    store.artifact(job, 'draft_coverage', 'all', {'missing_item_ids': missing,
        'used_item_ids': output['used_item_ids'], 'delivery_checks': report,
        'notice': 'Declaração da redação; ainda depende da conferência factual e editorial.'}, deps)
    return article


def creator_voice(job, references):
    """Supply original creator names and timestamps without repeating transcript text."""
    result = []
    for source in job.get('sources', []):
        located = []
        for segment in source.get('segments', []):
            if segment['id'] not in references:
                continue
            start = segment.get('start')
            stamp = None
            if isinstance(start, (int, float)) and not isinstance(start, bool) and math.isfinite(start) and start >= 0:
                seconds = int(start)
                stamp = f'[{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}]' if seconds >= 3600 else f'[{seconds // 60:02}:{seconds % 60:02}]'
            located.append({'source_id': segment['id'], 'timestamp_reference': stamp})
        if located:
            result.append({'video_id': source['id'], 'author': source.get('author', ''),
                           'title': source.get('title', ''), 'url': source['url'], 'source_references': located})
    return result


def qualifications(items):
    return [{'item_id': item['id'], **{key: item.get(key, []) for key in
            ('conditions', 'restrictions', 'limitations')}} for item in items
            if any(item.get(key) for key in ('conditions', 'restrictions', 'limitations'))]
