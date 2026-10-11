"""Preserve user requirements; ask the model to assess their actual fulfillment."""
from copy import deepcopy
import re

VERSION = 1

POLICY = '''
Cada requisito do briefing tem uma função no artigo. Interprete seu significado,
incluindo todas as condições dentro do mesmo requisito. A pergunta do leitor
determina a progressão; escolha a estrutura pela explicação necessária, sem
impor uma quantidade de seções, palavras ou listas. Um tutorial deve permitir
compreender a sequência, os critérios e os cuidados que as fontes sustentam.
As exclusões delimitam o conteúdo: não reproduza algo proibido só porque consta
no vídeo. O briefing não é evidência factual: se uma exigência factual contradiz
as fontes ou não tem apoio, identifique o conflito, sem inventar comprovação,
trocar números silenciosamente ou apresentar uma alternativa proibida como
recomendação. Construa a resposta com o conteúdo pertinente e sustentado.
Na organização e na redação, confira também os trechos originais disponíveis:
o resumo e os itens do plano podem ter perdido ações, razões e cuidados.
Integre os detalhes pertinentes mesmo quando não viraram um item extraído;
o plano orienta a organização, não limita a cobertura da fonte original.
'''

REVIEW_POLICY = '''
brief_assessments confere CADA requisito de brief_requirements, separadamente.
met significa cumprimento integral no texto real; partial é desenvolvimento
incompleto, missing é ausência, violated é contradição ou conteúdo proibido,
conflict é uma exigência que as fontes não permitem cumprir com fidelidade,
uncertain é impossibilidade de conferir. Justifique com passage_ids reais e uma
correction concreta quando necessária. Não aprove por repetir tema, palavras
do pedido, título ou prometer uma explicação. Nas perguntas, confira a resposta
e todas as condições; não avalie apenas se o assunto foi mencionado.
Uma exclusão cumprida pela ausência não exige passage_ids. violated deve apontar
o trecho que descumpre o pedido; met de pergunta exige desenvolvimento no corpo.
Não classifique uma restrição descumprida como mera preferência de estilo.
excluded_source_ids só cabe em requisitos do campo exclusions com status met:
identifique trechos INTEIROS excluídos do escopo; se parte permanece pertinente,
não exclua a fonte inteira. Essas exclusões não são lacunas a preencher.
excluded_item_ids identifica itens extraídos INTEIROS proibidos por uma exclusão
atendida, mesmo quando sua fonte também contém conteúdo pertinente. Não exclua
um item misto que ainda precisa ser desenvolvido; preserve sua parte pertinente.
Na cobertura das fontes, avalie os detalhes pertinentes ao briefing: omitir algo
expressamente excluído é correto. Não mande o redator reinserir conteúdo proibido.
No parecer geral, matches_brief só pode ser true se todos os requisitos forem met.
Não transforme divergência entre briefing e fonte em instrução inventada.
Preencha source_details ANTES de concluir a cobertura de source_assessments:
decomponha a fala em unidades de sentido e confira TODAS as informações pertinentes,
incluindo ações preparatórias, motivos, condições para parar, verificações e cuidados.
Não agrupe uma ação e um cuidado diferente como se a presença da ação cobrisse ambos.
source_details é um objeto separado com uma chave obrigatória para CADA referência
em evidence_options. Sob cada chave, liste as unidades de sentido
daquela fala; detail descreve a informação concreta, sem escrever uma citação.
O servidor copia o excerpt original. Use covered, missing, excluded ou uncertain.
covered exige passage_ids do corpo que desenvolvem aquele detalhe,
não apenas o tema da fonte. excluded exige motivo ligado ao escopo ou à irrelevância;
não exclua um cuidado só porque o plano ou a extração o omitiu. Uma fala longa pode
ter várias unidades. Repetições equivalentes podem ser agrupadas. Avalie a fonte
antes de avaliar o rascunho, para não procurar apenas confirmação do que já foi escrito.
'''


def requirements(job):
    if not job.get('editorial', {}).get('brief_contract_version'):
        return []
    result = []
    for field in ('topic', 'main_question', 'intent', 'genre', 'audience', 'tone', 'instructions', 'exclusions'):
        value = job.get('brief', {}).get(field) or ''
        # Preserve every clause. These boundaries assign stable references;
        # semantic interpretation and organization belong to the model.
        lines = value.splitlines()
        if field == 'main_question':
            lines = [part for line in lines for part in re.findall(r'[^?]+\??', line)]
        for line in lines:
            text = re.sub(r'^\s*(?:[-*•]\s+|\d+[.)]\s+)', '', line).strip()
            if text:
                result.append({'id': f'b{len(result)+1}', 'field': field, 'text': text})
    return result


def validate(output, payload, sources):
    from . import workflow
    inventory = {r['id']: r for r in payload.get('brief_requirements', [])}
    if not inventory:
        return
    assessments = output['brief_assessments']
    workflow.exact_ids([a['requirement_id'] for a in assessments], inventory, 'Requisitos do briefing')
    passages = {p['id']: p for p in payload['passages']}
    body = set(payload['_body_development_ids'])
    for a in assessments:
        workflow.known_ids(a['passage_ids'], passages, 'Trechos da conferência do briefing')
        workflow.known_ids(a['excluded_source_ids'], sources, 'Fontes excluídas pelo briefing')
        workflow.known_ids(a.get('excluded_item_ids', []), [i['id'] for i in payload.get('items', [])],
                           'Itens excluídos pelo briefing')
        requirement = inventory[a['requirement_id']]
        if (a['excluded_source_ids'] or a.get('excluded_item_ids')) and (requirement['field'] != 'exclusions' or a['status'] != 'met'):
            raise ValueError('Somente uma exclusão atendida pode retirar fontes do escopo.')
        if a['status'] == 'violated' and not a['passage_ids']:
            raise ValueError('A violação precisa identificar o trecho real do artigo.')
        if a['status'] == 'met' and requirement['field'] != 'exclusions' and not a['passage_ids']:
            raise ValueError('O atendimento precisa apontar onde o requisito está desenvolvido.')
        if a['status'] == 'met' and requirement['field'] == 'main_question' and not body.intersection(a['passage_ids']):
            raise ValueError('Um título ou metadado não responde à pergunta do leitor.')
        if a['status'] in ('partial', 'missing', 'violated') and not a['correction'].strip():
            raise ValueError('O requisito descumprido precisa de uma orientação concreta de correção.')
    for source in output['source_assessments']:
        details = source.get('detail_assessments') or []
        if not details:
            raise ValueError('A cobertura da fonte precisa conferir suas unidades de sentido.')
        for detail in details:
            workflow.validate_evidence([{'evidence': [{'source_id': source['source_id'], 'excerpt': detail['excerpt']}]}], sources)
            workflow.known_ids(detail['passage_ids'], body, 'Desenvolvimento do detalhe no corpo')
            if detail['status'] == 'covered' and not detail['passage_ids']:
                raise ValueError('O detalhe atendido precisa de desenvolvimento no corpo.')


def reconcile(output, payload):
    """Keep contradictory approval from overriding the model's itemized findings."""
    inventory = {r['id']: r for r in payload.get('brief_requirements', [])}
    if not inventory:
        return None, []
    parts = {p['id']: p for p in payload['passages']}
    verdicts = {a['passage_id']: a for a in output['assessments']}
    active_sources = {e['source_id'] for a in output['assessments']
                      if a['status'] != 'not_factual' and a['passage_id'] in payload['_body_development_ids']
                      for e in a['evidence']}
    active_items = {ident for a in output['assessments'] if a['passage_id'] in payload['_body_development_ids']
                    for ident in a['used_item_ids']}
    assessments = deepcopy(output['brief_assessments'])
    findings = []
    for a in assessments:
        req = inventory[a['requirement_id']]
        if a['status'] == 'met' and (active_sources.intersection(a['excluded_source_ids'])
                                   or active_items.intersection(a.get('excluded_item_ids', []))):
            a.update(claimed_status='met', status='uncertain',
                     reason='A exclusão integral de uma fonte ou item foi declarada, mas o corpo ainda o utiliza. A conformidade não foi confirmada.')
        # Factual disapproval of an alleged answer cannot certify fulfillment.
        if a['status'] == 'met' and req['field'] == 'main_question' and any(
                verdicts[p]['status'] in ('unsupported', 'uncertain') for p in a['passage_ids']):
            a.update(claimed_status='met', status='uncertain',
                     reason='A resposta foi declarada atendida, mas a própria revisão não confirmou seu apoio factual.')
        if a['status'] != 'met':
            findings.append({'severity': 'blocking', 'origin': 'brief_compliance',
                'recipient': 'writing', 'requirement_id': a['requirement_id'],
                'requirement_field': req['field'], 'requirement': req['text'],
                'assessment_status': a['status'], 'reason': a['reason'],
                'suggestion': a['correction'] or 'Confira a divergência entre o pedido e as fontes antes de acrescentar informação.',
                'passage': parts[a['passage_ids'][0]]['text'] if a['passage_ids'] else '', 'source_ids': []})
    excluded = {source for a in assessments if a['status'] == 'met'
                for source in a['excluded_source_ids']}
    # If a source is still actively used by factual body text, an assertion of
    # exclusion is incoherent. Do not erase that content's factual diagnosis.
    excluded -= active_sources
    excluded_items = {ident for a in assessments if a['status'] == 'met'
                      for ident in a.get('excluded_item_ids', [])} - active_items
    for source in output['source_assessments']:
        if source['source_id'] in excluded:
            source.update(claimed_status=source['status'], status='excluded', passage_ids=[], missing_details=[],
                          excluded_by_brief=True, reason='Trecho fora do escopo por uma exclusão atendida do briefing.')
            continue
        details = source.get('detail_assessments') or []
        missing = [d.get('detail') or d['excerpt'] for d in details if d['status'] == 'missing']
        incoherent = [d for d in details if d['status'] == 'uncertain' or d['status'] == 'covered' and any(
            verdicts[p]['status'] != 'supported' or not any(e['source_id'] == source['source_id']
                for e in verdicts[p]['evidence']) for p in d['passage_ids'])]
        if missing:
            source.update(claimed_status=source['status'],
                status='partial' if any(d['status'] == 'covered' for d in details) else 'missing',
                missing_details=list(dict.fromkeys([*source['missing_details'], *missing])),
                reason='A conferência dos detalhes da fonte identificou conteúdo pertinente ausente do corpo.')
        if incoherent:
            source.update(claimed_status=source.get('claimed_status', source['status']), status='uncertain',
                reason='A cobertura integral foi declarada, mas há detalhes sem apoio confirmado no corpo.')
    compliance = {'version': VERSION, 'requirements': list(inventory.values()),
                  'assessments': assessments, 'excluded_source_ids': sorted(excluded),
                  'excluded_item_ids': sorted(excluded_items)}
    reconcile_source_coverage(output, compliance)
    return compliance, findings


def reconcile_source_coverage(output, compliance):
    """Refresh approval after every factual coherence check, without another call."""
    if compliance is None:
        return
    gaps = [{'source_id': source['source_id'], 'status': source['status'],
             'reason': source['reason'], 'missing_details': source['missing_details']}
            for source in output['source_assessments'] if source['status'] in ('partial', 'missing', 'uncertain')]
    unmet = sum(a['status'] != 'met' for a in compliance['assessments'])
    compliance.update(source_gaps=gaps, satisfied=not (unmet or gaps))
    if unmet or gaps:
        alignment = output['editorial_alignment']
        alignment.setdefault('claimed_matches_brief', alignment['matches_brief'])
        alignment.update(matches_brief=False,
                         reason='Há requisitos ou detalhes pertinentes das fontes sem atendimento confirmado; consulte a conferência.',
                         passage='')
        output['summary'] = (f"A conferência identificou {unmet} requisito(s) sem atendimento confirmado."
                             if unmet else 'A conferência não confirmou o desenvolvimento de todos os detalhes pertinentes das fontes.')
