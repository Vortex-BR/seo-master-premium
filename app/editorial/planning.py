"""Select scope and presentation globally when the complete evidence fits.

One editorial decision replaces topic classification, repeated comparisons and
per-topic plans. Extraction and independent factual checks remain in place.
"""
from .. import db, generation
from . import engine, guidance, store
from .contracts import EditorialPlan

VERSION = 1
INSTRUCTION = guidance.FORMAT_POLICY + '''
Planeje um único artigo para a pergunta real do leitor. Você recebe o inventário conferido completo,
as evidências originais e relações já examinadas dos vídeos. Compare diferenças de autor, método,
condição, unidade e alcance antes de selecionar. Não transforme experiência em regra universal,
nem concilie divergências por suposição. Escolha alternativas atribuídas ou registre a dúvida.

Decida globalmente a relevância dentro da meta de palavras do ARTIGO. Em dispositions, avalie cada
item como used, duplicate, out_of_scope, unsupported ou pending com justificativa concreta. Todo
material fica no inventário mesmo quando excluído. Nem todo detalhe verdadeiro é necessário.
Não transforme exemplos laterais, marcas mencionadas ou fases posteriores em requisitos de redação.
Preserve ações, condições, razões e ressalvas indispensáveis para entender e executar a tarefa.
Somente itens supported podem ser usados. Não descarte como unsupported um item já conferido.

Organize sections como H2, cada uma com pergunta e função próprias. item_ids deve conter exatamente
os itens escolhidos para desenvolver nela; preferencialmente cada informação tem um lugar principal.
Cada seção precisa de presentation: escolha mode pela tarefa, explique a função em reason e planeje
subheadings H3 quando as subdivisões precisarem de explicação própria. O redator recebe essa arquitetura.
Em passos que exigem explicação, planeje um H2 para o processo e H3 para as etapas com parágrafos;
materiais ou verificações curtas podem ser listas. Não espalhe etapas essenciais por listas temáticas.
Não crie seções de introdução ou conclusão: opening situa e closing encerra sem repetir tudo.

reader_journey considera main_question e intent, não somente genre. video_item_ids contém itens dos
vídeos usados como guia, nunca IDs de trechos. Um artigo não precisa citar todos os vídeos se algum
não contribuir. Pendências essenciais deixam ready_to_write=false. Registre pending sem inventar
respostas. Dúvidas de transcrição exigem conferir o áudio e não viram perguntas à web.
research_questions só inclui lacunas concretas que pesquisa complementar pode esclarecer e que
mudam a resposta central. Não pesquise detalhes descartados nem invente perguntas para preencher.
Não escreva o artigo nem inclua afirmações que não constem nos itens conferidos.'''


def plan(job, *, allow_research=True):
    from . import workflow
    pending = job['editorial'].get('planning_research_pending')
    if pending:
        from . import research
        changed = research.run(job, pending['questions'])
        changed = changed or pending['version'] != job['apuration']['version']
        job['editorial'].pop('planning_research_pending', None)
        if changed:
            job['plan']['valid'] = False
        db.save_job(job)
        if not changed:
            return job['plan']
        allow_research = False
    items = job['apuration']['items']
    if not any(i['check']['status'] == 'supported' for i in items):
        raise workflow.NeedsInput('Nenhuma informação foi sustentada pela conferência. Revise as fontes antes de planejar.')
    guide = guidance.source_guide(job)
    payload = {'_local_context': True, 'items': [workflow.compact(i) for i in items],
               'source_guidance': guide, 'pending': job['apuration']['pending'],
               '_context_sources': workflow.source_fragments(job, items)}
    slot = f'editorialplan:{VERSION}:{job["apuration"]["version"]}'
    if not engine.cached_invocation(job, 'planner', payload, slot=slot):
        scope, _, _ = engine.invocation_inputs(job, 'planner', payload, None, slot)
        token = generation.agent_scope.set(scope)
        try:
            generation.prepare_structured(job, EditorialPlan, INSTRUCTION, 'planner', payload)
        except generation.ContextLimitExceeded:
            # The existing partitioned path retains all evidence, without first
            # charging a request that cannot fit this model/context setting.
            return None
        finally:
            generation.agent_scope.reset(token)
    output = workflow.call(job, 'planner', EditorialPlan, INSTRUCTION, payload, slot,
                           lambda result: workflow.validate_plan(job, result))
    workflow.save_plan(job, output)
    store.artifact(job, 'source_guidance', 'all', guide, workflow.dependencies(job))
    if allow_research and job['brief'].get('research') and output['research_questions']:
        from . import research
        # Persist before research so an interrupted extraction can resume using
        # its durable baseline. No repeated research after new evidence arrives.
        job['editorial']['planning_research_pending'] = {
            'questions': output['research_questions'], 'version': job['apuration']['version']}
        db.save_job(job)
        changed = research.run(job, output['research_questions'])
        job['editorial'].pop('planning_research_pending', None)
        if changed:
            job['plan']['valid'] = False
            db.save_job(job)
            return workflow.plan(job, allow_research=False)
        db.save_job(job)
    return job['plan']
