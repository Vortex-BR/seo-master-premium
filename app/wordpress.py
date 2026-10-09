import html
import re
import httpx

from . import db, media, publishing
from .editorial import delivery
from .generation import article_hash
from .security import get_secret, public_https_url


def connection():
    url = db.get_setting('wp_url', '')
    user, password = db.get_setting('wp_user', ''), get_secret('wp_password')
    if not url or not user or not password:
        raise ValueError('Configure URL, usuário e senha de aplicativo do WordPress em Integrações.')
    return public_https_url(url), httpx.BasicAuth(user, password)


def test_connection():
    base, auth = connection()
    with httpx.Client(timeout=25, auth=auth, follow_redirects=False) as client:
        response = client.get(base + '/wp-json/wp/v2/users/me', params={'context': 'edit'})
        if not response.is_success:
            raise ValueError('Não foi possível autenticar no WordPress. Verifique o endereço final HTTPS e a senha de aplicativo.')
        user = response.json()
        caps = user.get('capabilities', {})
        return {'ok': True, 'name': user.get('name', ''), 'can_edit_posts': bool(caps.get('edit_posts')),
                'can_upload_files': bool(caps.get('upload_files'))}


def sync_image(client, base, job, item):
    existing = item.get('wordpress') or {}
    if existing.get('site') and existing['site'] != base:
        existing = {}  # Per-site reuse only; files on another site must be uploaded here.
    slug = f'seo-master-{job["id"]}-{item["id"]}'
    endpoint = base + '/wp-json/wp/v2/media'
    image_id = existing.get('id')
    if image_id is not None and (type(image_id) is not int or image_id <= 0):
        raise ValueError('O identificador da imagem salva é inválido. Confira o vínculo com a biblioteca do WordPress.')
    if image_id:
        remote = get_json(client, endpoint + f'/{image_id}', params={'context': 'edit'},
                          error='Uma imagem enviada anteriormente não está disponível no WordPress. Confira a biblioteca de mídia antes de continuar.')
        if not isinstance(remote, dict) or remote.get('slug') != slug:
            raise ValueError('Uma imagem enviada anteriormente não está disponível no WordPress. Confira a biblioteca de mídia antes de continuar.')
    else:
        records = get_json(client, endpoint, params={'slug': slug, 'context': 'edit'},
                           error='Não foi possível conferir a biblioteca de mídia do WordPress.')
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError('O WordPress retornou uma lista de imagens inválida.')
        matches = [m for m in records if m.get('slug') == slug]
        if matches:
            image_id = matches[0].get('id')
            if type(image_id) is not int or image_id <= 0:
                raise ValueError('O WordPress retornou um identificador de imagem inválido.')
        elif existing.get('uncertain'):
            raise ValueError('Uma imagem ficou sem confirmação no envio anterior. Confira a biblioteca do WordPress antes de repetir o envio.')
    fields = {'slug': slug, 'title': item['alt'] or job['article']['title'], 'alt_text': item['alt'],
              'caption': html.escape(publishing.caption(item))}
    item['wordpress'] = {'site': base, 'id': image_id, 'uncertain': True}
    db.save_job(job)
    try:
        if image_id:
            response = client.post(endpoint + f'/{image_id}', json=fields)
        else:
            name = re.sub(r'[^a-z0-9-]', '-', job['article']['slug'].lower())[:80] + '-' + item['id'][:8] + '.webp'
            try:
                image_data = media.path(job, item).read_bytes()
            except OSError:
                item['wordpress']['uncertain'] = False
                db.save_job(job)
                raise ValueError('O arquivo da imagem está indisponível. Restaure ou remova a imagem antes de enviar ao WordPress.') from None
            response = client.post(endpoint, data=fields, files={'file': (name, image_data, 'image/webp')})
    except httpx.HTTPError:
        raise ValueError('O WordPress não confirmou o envio da imagem. O próximo envio conferirá a biblioteca antes de criar outra cópia.') from None
    if not response.is_success:
        item['wordpress']['uncertain'] = response.status_code >= 500
        db.save_job(job)
        raise ValueError(f'O WordPress recusou a imagem (HTTP {response.status_code}). Confira a permissão para enviar arquivos.')
    try:
        remote = response.json()
    except ValueError:
        raise ValueError('O WordPress retornou uma resposta de imagem inválida. Confira a biblioteca de mídia.') from None
    if (not isinstance(remote, dict) or type(remote.get('id')) is not int
            or remote['id'] <= 0 or not str(remote.get('source_url', '')).startswith('https://')):
        raise ValueError('O WordPress retornou dados de imagem inesperados. Confira a biblioteca de mídia.')
    item['wordpress'] = {'site': base, 'id': remote['id'], 'url': remote['source_url'], 'uncertain': False}
    db.save_job(job)
    return item['wordpress']


def ensure_reviewed(job):
    """Compatibility entry point: saved content needs technical validity only."""
    return delivery.ensure_exportable(job)


def previous_article(job, existing):
    baseline = existing.get('article_hash')
    if not baseline:
        return None
    return job['article'] if article_hash(job['article']) == baseline else next(
        (item['data'] for item in db.revisions(job['id']) if article_hash(item['data']) == baseline), None)


def previous_content_hash(job, existing, marker):
    """Recover an old synchronization baseline without trusting a marker alone."""
    if existing.get('content_hash'):
        return existing['content_hash']
    article = previous_article(job, existing)
    if not article:
        return None
    images = {item['id']: item.get('wordpress') or {} for item in media.active_images(job)}
    if any(not image.get('url') or image.get('site') != existing.get('site') for image in images.values()):
        return None
    try:
        content = publishing.render({**job, 'article': article}, gutenberg=True,
                                    image_urls={key: value['url'] for key, value in images.items()},
                                    image_ids={key: value.get('id') for key, value in images.items()}) + '\n' + marker
    except ValueError:
        return None
    return article_hash(content)


def post_fields(post, defaults=None):
    """Track the editable fields that this integration writes."""
    fields = dict(defaults or {})
    for key in ('title', 'excerpt'):
        value = post.get(key)
        raw = value.get('raw') if isinstance(value, dict) else value
        if isinstance(raw, str):
            fields[key] = raw
    for key in ('slug', 'featured_media'):
        if key in post:
            fields[key] = post[key]
    return fields


def ensure_existing_post(post, marker, existing, job):
    content = post.get('content')
    raw = content.get('raw') if isinstance(content, dict) else None
    if post.get('status') not in ('draft', 'pending') or not isinstance(raw, str) or marker not in raw:
        raise ValueError('O post foi publicado ou alterado fora do aplicativo; atualização automática interrompida.')
    baseline = previous_content_hash(job, existing, marker)
    attempted = existing.get('attempted_content_hash') if existing.get('uncertain') else None
    if not baseline and not attempted:
        raise ValueError('O envio antigo não possui uma versão verificável da postagem. Confira o conteúdo no WordPress antes de atualizar; nenhuma alteração foi enviada.')
    raw_hash = article_hash(raw)
    if raw_hash not in (baseline, attempted):
        raise ValueError('O conteúdo do post mudou fora do aplicativo; atualização automática interrompida para preservar a edição no WordPress.')
    previous_fields = existing.get('post_fields')
    if not previous_fields:
        article = previous_article(job, existing)
        previous_fields = {key: article[key] for key in ('title', 'slug', 'excerpt')} if article else {}
        if article and not media.active_images(job):
            previous_fields['featured_media'] = 0
    candidates = []
    if raw_hash == baseline:
        candidates.append(previous_fields)
    if raw_hash == attempted:
        candidates.append(existing.get('attempted_post_fields') or {})
    observed = post_fields(post)
    if candidates and not any(all(key in candidate and candidate[key] == value
                                  for key, value in observed.items()) for candidate in candidates):
        raise ValueError('Os dados do post mudaram fora do aplicativo; atualização automática interrompida para preservar a edição no WordPress.')


def get_json(client, url, *, params, error):
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError:
        raise ValueError(error) from None
    if not response.is_success:
        raise ValueError(error)
    try:
        return response.json()
    except ValueError:
        raise ValueError('O WordPress retornou dados inválidos. Confira a conexão e as permissões.') from None


def send_for_review(job):
    article = delivery.ensure_exportable(job)
    base, auth = connection()
    existing = job.get('wordpress', {})
    if existing.get('site') and existing['site'] != base:
        raise ValueError('Este artigo já foi enviado a outro site. Restaure a conexão original para atualizá-lo.')
    marker = f'<!-- seo-master:{job["id"]} -->'
    with httpx.Client(timeout=40, auth=auth, follow_redirects=False) as client:
        post_id = existing.get('id')
        checked_content_hash = None
        checked_fields = None
        if post_id is not None and (type(post_id) is not int or post_id <= 0):
            raise ValueError('O identificador da postagem salva é inválido. Confira o vínculo com o WordPress antes de enviar.')
        if post_id:
            post = get_json(client, f'{base}/wp-json/wp/v2/posts/{post_id}', params={'context': 'edit'},
                            error='Não foi possível conferir a postagem existente. Nenhuma nova postagem foi criada.')
            if not isinstance(post, dict):
                raise ValueError('O WordPress retornou dados de postagem inválidos.')
            if post.get('id') != post_id:
                raise ValueError('O WordPress retornou outra postagem; atualização automática interrompida.')
            ensure_existing_post(post, marker, existing, job)
            checked_content_hash = article_hash(post['content']['raw'])
            checked_fields = post_fields(post, existing.get('post_fields'))
        else:
            posts = get_json(client, base + '/wp-json/wp/v2/posts', params={'slug': article['slug'], 'context': 'edit',
                                                                         'status': 'draft,pending,private,publish,future'},
                             error='Não foi possível verificar posts existentes. Confira as permissões do usuário WordPress.')
            if not isinstance(posts, list) or any(not isinstance(post, dict) for post in posts):
                raise ValueError('O WordPress retornou uma lista de postagens inválida.')
            for post in posts:
                content = post.get('content')
                raw = content.get('raw') if isinstance(content, dict) else None
                if isinstance(raw, str) and marker in raw and post.get('status') in ('draft', 'pending'):
                    ensure_existing_post(post, marker, existing, job)
                    if type(post.get('id')) is not int or post['id'] <= 0:
                        raise ValueError('O WordPress retornou um identificador de postagem inválido.')
                    post_id = post['id']
                    checked_content_hash = article_hash(raw)
                    checked_fields = post_fields(post, existing.get('post_fields'))
                    break
                raise ValueError('Já existe outro post com esse slug. Altere o slug no editor antes de enviar.')
            if not post_id and existing.get('uncertain'):
                raise ValueError('O envio anterior ficou sem confirmação. Confira o WordPress antes de qualquer novo envio; '
                                 'o sistema não repetirá uma criação que pode ter sido concluída.')
        images = {item['id']: sync_image(client, base, job, item) for item in media.active_images(job)}
        featured = next((images[item['id']]['id'] for item in media.active_images(job) if item['featured']), 0)
        payload = {'title': article['title'], 'slug': article['slug'],
                   'content': publishing.render(job, gutenberg=True, image_urls={k: v['url'] for k, v in images.items()},
                                               image_ids={k: v['id'] for k, v in images.items()}) + '\n' + marker,
                   'excerpt': article['excerpt'], 'status': 'pending', 'featured_media': featured}
        # Persist intent before the remote side effect; a crash or timeout cannot trigger blind recreation.
        job['wordpress'] = {**existing, 'site': base, 'id': post_id, 'uncertain': True,
                            'attempted_at': db.now(), 'attempted_content_hash': article_hash(payload['content']),
                            'attempted_post_fields': post_fields(payload)}
        if post_id:
            job['wordpress']['content_hash'] = checked_content_hash
            job['wordpress']['post_fields'] = checked_fields
        db.save_job(job)
        endpoint = base + '/wp-json/wp/v2/posts' + (f'/{post_id}' if post_id else '')
        try:
            result = client.post(endpoint, json=payload)
        except httpx.HTTPError:
            raise ValueError('O WordPress não confirmou o envio. Tente reconciliar novamente; não será criada uma cópia automaticamente.') from None
        if not result.is_success:
            # Keep uncertainty for server errors. Explicit 4xx responses mean no successful write.
            job['wordpress']['uncertain'] = result.status_code >= 500
            db.save_job(job)
            raise ValueError(f'O WordPress recusou o envio (HTTP {result.status_code}). Confira a conexão e as permissões.')
        try:
            post = result.json()
        except ValueError:
            raise ValueError('O WordPress retornou uma resposta inválida; confira o post no site antes de repetir o envio.') from None
        if (not isinstance(post, dict) or type(post.get('id')) is not int or post['id'] <= 0
                or post.get('status') != 'pending' or (post_id and post['id'] != post_id)):
            raise ValueError('O WordPress retornou uma resposta inesperada; confira o post no site.')
        remote_content = post.get('content')
        raw = remote_content.get('raw') if isinstance(remote_content, dict) else None
        job['wordpress'] = {'site': base, 'id': post['id'], 'status': 'pending', 'uncertain': False,
                            'edit_url': f'{base}/wp-admin/post.php?post={post["id"]}&action=edit',
                            'synced_at': db.now(), 'article_hash': article_hash(article),
                            'content_hash': article_hash(raw if isinstance(raw, str) else payload['content']),
                            'post_fields': post_fields(post, post_fields(payload))}
        db.save_job(job)
        return job['wordpress']


# Kept for callers from earlier releases; imports retain pending status.
send_draft = send_for_review
