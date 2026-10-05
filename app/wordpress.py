import httpx

from . import db
from .generation import article_hash, deterministic_findings, render_article, unresolved_findings
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
        return {'ok': True, 'name': user.get('name', ''), 'can_edit_posts': bool(caps.get('edit_posts'))}


def ensure_reviewed(job):
    review = job.get('review') or {}
    if not job.get('article') or review.get('article_hash') != article_hash(job['article']):
        raise ValueError('Revise a versão atual do artigo antes de enviar ao WordPress.')
    if deterministic_findings(job) or unresolved_findings(job):
        raise ValueError('Resolva as pendências factuais e execute a revisão novamente.')


def send_draft(job):
    ensure_reviewed(job)
    base, auth = connection()
    existing = job.get('wordpress', {})
    if existing.get('site') and existing['site'] != base:
        raise ValueError('Este artigo já foi enviado a outro site. Restaure a conexão original para atualizá-lo.')
    article = job['article']
    marker = f'<!-- seo-master:{job["id"]} -->'
    with httpx.Client(timeout=40, auth=auth, follow_redirects=False) as client:
        post_id = existing.get('id')
        if post_id:
            check = client.get(f'{base}/wp-json/wp/v2/posts/{post_id}', params={'context': 'edit'})
            if not check.is_success:
                raise ValueError('Não foi possível conferir o rascunho existente. Nenhum novo post foi criado.')
            post = check.json()
            if post.get('status') != 'draft' or marker not in post.get('content', {}).get('raw', ''):
                raise ValueError('O post foi publicado ou alterado fora do aplicativo; atualização automática interrompida.')
        else:
            lookup = client.get(base + '/wp-json/wp/v2/posts', params={'slug': article['slug'], 'context': 'edit',
                                                                      'status': 'draft,pending,private,publish,future'})
            if not lookup.is_success:
                raise ValueError('Não foi possível verificar posts existentes. Confira as permissões do usuário WordPress.')
            for post in lookup.json():
                if marker in post.get('content', {}).get('raw', '') and post.get('status') == 'draft':
                    post_id = post['id']
                    break
                raise ValueError('Já existe outro post com esse slug. Altere o slug no editor antes de enviar.')
            if not post_id and existing.get('uncertain'):
                raise ValueError('O envio anterior ficou sem confirmação. Confira o WordPress antes de qualquer novo envio; '
                                 'o sistema não repetirá uma criação que pode ter sido concluída.')
        payload = {'title': article['title'], 'slug': article['slug'], 'content': render_article(job) + '\n' + marker,
                   'excerpt': article['excerpt'], 'status': 'draft'}
        # Persist intent before the remote side effect; a crash or timeout cannot trigger blind recreation.
        job['wordpress'] = {'site': base, 'id': post_id, 'uncertain': True, 'attempted_at': db.now()}
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
        post = result.json()
        if not isinstance(post.get('id'), int) or post.get('status') != 'draft':
            raise ValueError('O WordPress retornou uma resposta inesperada; confira o post no site.')
        job['wordpress'] = {'site': base, 'id': post['id'], 'status': 'draft', 'uncertain': False,
                            'edit_url': f'{base}/wp-admin/post.php?post={post["id"]}&action=edit',
                            'synced_at': db.now(), 'article_hash': article_hash(article)}
        db.save_job(job)
        return job['wordpress']
