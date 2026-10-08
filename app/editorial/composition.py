"""Compose one reader-facing article before editing; keep paid drafts recoverable."""
import re
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


INSTRUCTION = '''Escreva o artigo completo para o leitor, em português brasileiro. Entregue também os
metadados nos campos próprios. O objetivo é responder à pergunta com clareza, utilidade e fidelidade
às fontes. A extensão solicitada é uma meta para o artigo inteiro, nunca para cada seção. Pode terminar
antes quando a resposta estiver completa; não acrescente conteúdo só para preencher palavras.

Abra com uma resposta direta em um parágrafo curto. Desenvolva a explicação com títulos H2 informativos,
frases concretas e parágrafos que avancem uma ideia. Explique termos ao usá-los. Use exemplos das fontes
quando ajudarem a entender. Encerre brevemente, sem repetir todas as recomendações.

Adapte o formato à intenção. Em tutorial, use etapas numeradas com ações identificáveis; explique o
motivo e o sinal para avançar quando houver apoio. Integre cuidados no passo em que são necessários.
Em comparação, organize critérios e diferenças. Em explicação ou análise, desenvolva conceitos ou
argumentos. Não imponha etapas a outros formatos nem uma lista como substituto de uma explicação.

O plano orienta a cobertura e a ordem. Uma informação repetida no plano precisa ser desenvolvida uma
vez; preserve relações e condições ao reuni-la. Não copie introduções, conclusões ou resumos entre
seções. Não crie uma seção para cada informação, fonte ou agente. Retire elogios genéricos ao método,
frases de preenchimento e promessas não sustentadas. Escreva para quem não assistiu aos vídeos.

Use somente items e as evidências fornecidas. counterpoints contém contexto para preservar condições
e divergências; não é uma lista de temas adicionais. Não invente fatos, vivências da marca, benefícios,
razões ou certezas. A voz e os produtos vêm do briefing e do perfil deste projeto. A pesquisa complementa
o percurso dos vídeos. Não transforme um caso individual ou um método em regra universal.
required_qualifications destaca condições, restrições e limites dos itens usados: integre os que
delimitam cada afirmação no mesmo passo ou explicação. Uma orientação sem sua ressalva material
não está desenvolvida com fidelidade. Não transforme uma ação opcional em obrigação, uma checagem
em garantia, nem acrescente uma ação diferente da que a evidência descreve.

Entregue paragraphs em ordem de leitura, incluindo subtítulos Markdown e listas quando apropriados.
Cada bloco traz as fontes que sustentam suas afirmações. Use quebras de linha reais dentro das listas,
sem códigos de serialização ou notas de rodapé inventadas. usage registra informações desenvolvidas
com fidelidade; ler um item não significa usá-lo. A revisão factual será independente.

Se repair estiver presente, melhore o rascunho salvo em uma única revisão. Resolva os apontamentos
indicados, mantendo fatos válidos, condições e citações. Não aumente o texto para comentar a revisão.
Entregue a versão completa, sem explicações ao editor.'''


def _article(output, job):
    article = Article.model_validate({k: v for k, v in output.items() if k != 'used_item_ids'}).model_dump()
    article['slug'] = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', article['slug']).encode(
        'ascii', 'ignore').decode().lower()).strip('-') or 'artigo-' + job['id'][:8]
    return article


def write(job, plan, used):
    from . import workflow
    all_items = workflow.related_items(job, [item['id'] for item in used])
    ids = {item['id'] for item in used}
    sources = workflow.source_fragments(job, all_items)
    payload = {'_composition_contract': VERSION, '_local_context': True,
               'article_route': guidance.article_route(plan),
               'items': [workflow.writing_item(item) for item in used],
               'counterpoints': [workflow.writing_item(item) for item in all_items if item['id'] not in ids],
               'target_words_total': job['brief']['target_words'], '_context_sources': sources,
               'required_qualifications': qualifications(used)}
    slot = f'compose:{VERSION}:{job["plan"]["version"]}'

    def validate(output):
        workflow.known_ids(output['used_item_ids'], ids, 'Uso no artigo')
        workflow.known_ids(re.findall(r'\[\[([\w-]+)\]\]', output['markdown']), sources, 'Citações do artigo')
        _article(output, job)

    # Measure the complete wire request. If it cannot fit, the section writer
    # handles partitioning before any composition request is charged.
    cached = engine.cached_invocation(job, 'writer', payload, slot=slot)
    if not cached:
        scope, _, _ = engine.invocation_inputs(job, 'writer', payload, None, slot)
        token = generation.agent_scope.set(scope)
        try:
            generation.prepare_structured(job, DraftArticle, INSTRUCTION, 'composition', payload)
        except generation.ContextLimitExceeded:
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
    issues = report['findings']
    if missing or issues:
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


def qualifications(items):
    return [{'item_id': item['id'], **{key: item.get(key, []) for key in
            ('conditions', 'restrictions', 'limitations')}} for item in items
            if any(item.get(key) for key in ('conditions', 'restrictions', 'limitations'))]
