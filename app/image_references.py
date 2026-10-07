"""Stock search metadata only: images remain remote visual references."""
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

import httpx

from . import db
from .security import get_secret

PROVIDERS = {'pexels': 'Pexels', 'pixabay': 'Pixabay'}
IMAGE_HOSTS = {'pexels': {'images.pexels.com'}, 'pixabay': {'pixabay.com', 'cdn.pixabay.com'}}
PAGE_HOSTS = {'pexels': {'pexels.com', 'www.pexels.com'}, 'pixabay': {'pixabay.com', 'www.pixabay.com'}}
CACHE_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


def configured():
    return [name for name in PROVIDERS if get_secret(name + '_api_key')]


def safe_url(value, hosts):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and parsed.hostname in hosts and not parsed.username
                and not parsed.password and parsed.port in (None, 443) and not parsed.fragment)
    except (TypeError, ValueError):
        return False


def normalize(provider, rows):
    items = []
    for row in rows[:12]:
        try:
            if provider == 'pexels':
                url = row['src']['large']
                preview = row['src']['medium']
                page, author, description = row['url'], row['photographer'], row.get('alt', '')
                width, height = int(row['width']), int(row['height'])
            else:
                url = row['webformatURL']
                preview = row['previewURL']
                page, author, description = row['pageURL'], row['user'], row.get('tags', '')
                width, height = int(row['imageWidth']), int(row['imageHeight'])
            if (width <= 0 or height <= 0 or width < height
                    or not safe_url(url, IMAGE_HOSTS[provider])
                    or not safe_url(preview, IMAGE_HOSTS[provider])
                    or not safe_url(page, PAGE_HOSTS[provider])):
                continue
            items.append({'id': f'{provider}:{int(row["id"])}', 'provider': provider,
                          'image_url': url, 'preview_url': preview, 'page_url': page,
                          'author': str(author)[:150], 'description': str(description)[:500],
                          'width': width, 'height': height})
        except (KeyError, TypeError, ValueError):
            continue
    return items


def search_provider(provider, query, key):
    # Include a key fingerprint so replacing a revoked credential bypasses old results.
    cache_key = hashlib.sha256(f'{provider}:{query.casefold()}:{key}'.encode()).hexdigest()
    current = time.time()
    with db.connect() as connection:
        connection.execute('DELETE FROM image_reference_cache WHERE expires <= ?', (current,))
        cached = connection.execute('SELECT data FROM image_reference_cache WHERE key=? AND expires>?',
                                    (cache_key, current)).fetchone()
    if cached:
        return json.loads(cached['data'])
    with httpx.Client(timeout=12, follow_redirects=False) as client:
        if provider == 'pexels':
            response = client.get('https://api.pexels.com/v1/search', headers={'Authorization': key},
                                  params={'query': query, 'orientation': 'landscape', 'per_page': 8,
                                          'locale': 'pt-BR'})
            field = 'photos'
        else:
            response = client.get('https://pixabay.com/api/', params={'key': key, 'q': query,
                                  'lang': 'pt', 'image_type': 'photo', 'orientation': 'horizontal',
                                  'safesearch': 'true', 'per_page': 8})
            field = 'hits'
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError('Resposta inválida do banco de imagens.')
        rows = payload.get(field)
        if not isinstance(rows, list):
            raise ValueError('Resposta inválida do banco de imagens.')
        items = normalize(provider, rows)
    with db.connect() as connection:
        connection.execute('INSERT OR REPLACE INTO image_reference_cache VALUES (?,?,?)',
                            (cache_key, current + CACHE_SECONDS, json.dumps(items, ensure_ascii=False)))
    return items


def search(query):
    query = ' '.join(query.split())[:100]
    if len(query) < 2:
        raise ValueError('Informe um tema com pelo menos dois caracteres para buscar referências.')
    available, warnings, groups = configured(), [], []
    for provider in available:
        try:
            groups.append(search_provider(provider, query, get_secret(provider + '_api_key')))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            # HTTP errors can include the Pixabay key in their request URL.
            logger.warning('Stock reference search failed for %s: %s', provider, type(exc).__name__)
            warnings.append(f'{PROVIDERS[provider]} indisponível. Confira a chave e o limite de uso em Integrações.')
    # Interleave providers so automatic references do not come from just one bank.
    items = [group[index] for index in range(8) for group in groups if index < len(group)]
    if not available:
        warnings.append('Configure Pexels ou Pixabay em Integrações para usar referências visuais.')
    elif not items and not warnings:
        warnings.append('Nenhuma referência encontrada. Tente um termo mais específico.')
    return {'query': query, 'items': items, 'warnings': warnings, 'searched_at': db.now(),
            'expires_at': time.time() + CACHE_SECONDS}


def selected(job, ids):
    result = job.get('image_reference_results') or {}
    if not ids:
        raise ValueError('Selecione de uma a três referências, ou use a busca automática.')
    if result.get('expires_at', 0) <= time.time():
        raise ValueError('As referências expiraram. Faça uma nova busca antes de gerar.')
    candidates = {item['id']: item for item in result.get('items', [])}
    if len(set(ids)) != len(ids) or any(key not in candidates for key in ids):
        raise ValueError('Escolha referências da busca atual deste artigo.')
    return [candidates[key] for key in ids]


def default_query(job):
    brief = job.get('brief') or {}
    return (brief.get('keyword') or brief.get('topic') or job['article']['title'])[:100]
