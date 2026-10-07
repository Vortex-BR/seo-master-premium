"""Targeted research: generated notes are never promoted to original evidence."""
from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from .. import db, generation
from . import source_processing, store, workflow
from .contracts import BlockKnowledge, KnowledgeAudit, ResearchResolution


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.ignored += 1
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'li', 'br') and not self.ignored:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def page_text(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('A pesquisa precisa de uma página pública HTTPS.')
    request_url = httpx.URL(url)
    try:
        addresses = socket.getaddrinfo(request_url.host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError('O domínio da página não pôde ser localizado.') from None
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('A página precisa apontar somente para endereços públicos.')
    # Pin the checked IP, retaining the original HTTP host and TLS certificate validation.
    # A second DNS lookup could otherwise turn a public URL into a private destination.
    pinned_url = request_url.copy_with(host=addresses[0][4][0])
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', pinned_url, headers={'Host': request_url.netloc.decode('ascii'),
                'User-Agent': 'SEO-Master/1.4 (+editorial source verification)'},
                extensions={'sni_hostname': request_url.host}) as response:
            response.raise_for_status()
            if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
                raise ValueError('Página indisponível como texto HTML, redirecionada ou em formato não analisado.')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError('A página excede o limite de leitura; não foi utilizada como evidência.')
            markup = bytes(data).decode(response.encoding or 'utf-8', errors='replace')
    parser = PageText(); parser.feed(markup)
    text = '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in ''.join(parser.parts).splitlines())
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if len(text) < 80 or len(text) > 60000:
        raise ValueError('O texto da página está ausente ou excede 60 mil caracteres; a referência foi preservada sem apoio factual.')
    return text


def run(job, questions):
    if not job['brief'].get('research') or not questions:
        return
    profile = job['editorial']['profile']['profile']
    signature = generation.article_hash({'questions': questions, 'inputs': store.inputs_version(job),
                                         'model': job['editorial']['model'], 'tool_budget': profile['research_tool_calls']})
    if signature in job.get('research_requests_completed', []):
        return
    from . import engine
    workflow.reserve(job, 7, 'pesquisar e reservar a revisão')
    result = engine.invoke(job, 'source_checker', {'findings': [{'reason': q} for q in questions],
                           '_context_sources': {}}, generation.research, f'research:{signature}')[0]
    research = job.setdefault('research', {'text': '', 'sources': [], 'pages': []})
    research.setdefault('pages', [])
    research['text'] = '\n\n'.join(t for t in (research.get('text'), result.get('text')) if t)
    urls = list(dict.fromkeys(s['url'] for s in result.get('sources', [])))
    existing = {p['url']: p for p in research['pages']}
    for n, source in enumerate(result.get('sources', [])):
        if not any(s.get('kind') == 'research_note' and s['url'] == source['url'] for s in research['sources']):
            research['sources'].append({**source, 'id': f'rn{len(research["sources"])+1}', 'verified': False,
                                        'queried_at': db.now(), 'limitation': 'Nota gerada pela IA; não é trecho original.'})
    db.save_job(job)
    limit = profile['research_tool_calls'] * 2
    for url in urls:
        previous = existing.get(url)
        if previous and previous['status'] in ('checked', 'unavailable'):
            continue
        meta = next(s for s in result['sources'] if s['url'] == url)
        if not previous and len(research['pages']) >= limit:
            research['pages'].append({'url': url, 'title': meta['title'], 'status': 'unavailable',
                                      'reason': 'Limite de páginas desta pesquisa alcançado.', 'queried_at': db.now()})
            continue
        if previous:
            page_id = previous['id']
            page_evidence = next(s for s in research['sources'] if s['id'] == f'{page_id}s1')
            text = page_evidence['text']
        else:
            try:
                text = page_text(url)
            except (ValueError, httpx.HTTPError):
                research['pages'].append({'url': url, 'title': meta['title'], 'status': 'unavailable',
                                      'reason': 'Acesso ou leitura integral não concluídos; notas não usadas como evidência.',
                                      'queried_at': db.now()})
                db.save_job(job)
                continue
            page_id = f'wpage{len(research["pages"])+1}'
            page_evidence = {'id': f'{page_id}s1', 'url': url, 'title': meta['title'], 'text': text,
                             'kind': 'web_excerpt', 'verified': True, 'queried_at': db.now()}
            research['sources'].append(page_evidence)
            previous = {'id': page_id, 'url': url, 'title': meta['title'], 'status': 'reading',
                        'queried_at': db.now(), 'characters': len(text)}
            research['pages'].append(previous)
        source = {'id': page_id, 'url': url, 'title': meta['title'], 'status': 'ok',
                  'segments': [{'id': f'{page_id}s1', 'text': text, 'start': None, 'end': None}]}
        db.save_job(job)
        for block in source_processing.blocks(source, source_processing.block_limit(profile)):
            if any(b['id'] == block['id'] and b.get('status') == 'checked' and b['input_hash'] == block['input_hash']
                   for b in job['apuration']['inventory']['blocks']):
                continue
            workflow.reserve(job, 8, 'extrair a página consultada e reservar a revisão')
            received = {f'{page_id}s1': {**page_evidence, 'text': ''.join(p['text'] for p in block['owned'])}}
            extracted = workflow.call(job, 'extractor', BlockKnowledge, workflow.EXTRACT,
                                      {'block': block, 'research_questions': questions, '_context_sources': received},
                                      f'webextract:{signature}:{block["id"]}',
                                      lambda output: workflow.validate_evidence(output['items'], received))
            items = [dict(item, id=f'{block["id"]}k{n+1}', video_id=page_id, block_id=block['id'])
                     for n, item in enumerate(extracted['items'])]
            checked = workflow.call(job, 'source_checker', KnowledgeAudit, workflow.CHECK,
                                    {'items': items, 'block_context': block, '_context_sources': received},
                                    f'webcheck:{signature}:{block["id"]}',
                                    lambda output: workflow.exact_ids([c['item_id'] for c in output['checks']],
                                                                      [i['id'] for i in items], 'Conferência web'))
            for item in items:
                item['check'] = next(c for c in checked['checks'] if c['item_id'] == item['id'])
                if item['check']['status'] != 'supported':
                    store.issue(job, 'knowledge', item['id'], item['check']['reason'])
            job['apuration']['items'] = [i for i in job['apuration']['items'] if i['block_id'] != block['id']] + items
            block.update(status='checked', extracted_items=len(items), gaps=extracted['gaps'])
            for n, gap in enumerate(extracted['gaps']):
                store.issue(job, 'extraction', f'{block["id"]}:{n}', gap)
            job['apuration']['inventory']['blocks'] = [b for b in job['apuration']['inventory']['blocks'] if b['id'] != block['id']] + [block]
            store.artifact(job, 'block', block['id'], {'block': block, 'items': items}, workflow.dependencies(job))
            db.save_job(job)
        previous['status'] = 'checked'
        db.save_job(job)
    research.update(status='completed', notice='Só trechos de páginas efetivamente lidas entram como evidência. '
                    'Notas, redirecionamentos e páginas inacessíveis continuam identificados como limitações.')
    open_issues = [i for i in store.issues(job) if i['status'] == 'open']
    web_items = [i for i in job['apuration']['items'] if i['video_id'].startswith('wpage') and i['check']['status'] == 'supported']
    if open_issues and web_items:
        # Preserve every checked statement, condition and quantity. Quotations
        # are already present in web_sources, so link to them without repeating
        # the same long literal excerpt inside each candidate item.
        resolution_items = [{**workflow.compact(item),
                             'source_ids': list(dict.fromkeys(e['source_id'] for e in item['evidence']))}
                            for item in web_items]
        for n, group in enumerate(workflow.batches(open_issues, profile['context_chars'] // 6, max_items=12)):
            workflow.reserve(job, 7, 'conferir a resolução das lacunas e reservar a revisão')
            web_sources = workflow.source_fragments(job, web_items)
            def valid(output):
                workflow.exact_ids([a['issue_id'] for a in output['answers']], [i['id'] for i in group], 'Resolução de pesquisa')
                for answer in output['answers']:
                    workflow.validate_evidence([answer], web_sources)
                    workflow.validate_evidence([answer], generation.evidence_map(job))
                    if answer['status'] == 'resolved' and not answer['evidence']:
                        raise ValueError('Uma resolução factual precisa de evidência original.')
            answers = workflow.call(job, 'source_checker', ResearchResolution,
                '''Verifique se os trechos ORIGINAIS das páginas efetivamente resolvem cada pendência.
Entregue uma situação por issue_id. resolved exige evidência literal suficiente para a questão específica,
com método, condições e unidades. Uma nota de pesquisa ou repetição não prova resolução. Deixe unresolved
se o dado é ambíguo, parcial ou não responde à lacuna. Não altere a formulação original para facilitar aprovação.''',
                {'issues': group, 'web_items': resolution_items, '_context_sources': web_sources},
                f'research_resolution:{signature}:{n}', valid)
            for answer in answers['answers']:
                if answer['status'] == 'resolved':
                    store.resolve_issue(job, answer['issue_id'], answer['reason'],
                                        [e['source_id'] for e in answer['evidence']], actor='Checador das fontes')
    job.setdefault('research_requests_completed', []).append(signature)
    job['apuration']['pending'] = [i for i in store.issues(job) if i['status'] == 'open']
    snapshot = store.artifact(job, 'knowledge', 'all', job['apuration'], workflow.dependencies(job))
    job['apuration']['version'] = snapshot['version']
    db.save_job(job)
