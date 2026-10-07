"""Article image storage, positioning and bounded-access import links."""
import hashlib
import hmac
import io
import re
import secrets
import time
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from markdown_it import MarkdownIt

from . import db

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
LINK_SECONDS = 7 * 86400
BANNER_SIZE = (1280, 420)


def headings(job):
    tokens = MarkdownIt('commonmark').parse((job.get('article') or {}).get('markdown', ''))
    result, counts = [], {}
    for index, token in enumerate(tokens):
        if token.type != 'heading_open' or token.level != 0:
            continue
        title = tokens[index + 1].content
        counts[title] = counts.get(title, 0) + 1
        key = hashlib.sha256(f'{title}:{counts[title]}'.encode()).hexdigest()[:16]
        result.append({'id': key, 'title': title, 'line': token.map[1]})
    return result


def busy(job):
    return any(t['status'] in ('queued', 'running') for t in job.get('image_tasks', []))


def find(job, image_id):
    item = next((m for m in job.get('media', []) if m['id'] == image_id), None)
    if not item:
        raise ValueError('Imagem não encontrada neste artigo.')
    return item


def path(job, item):
    if not re.fullmatch(r'[\w-]{1,64}', job['id']) or not re.fullmatch(r'[a-f0-9]{32}', item['id']):
        raise ValueError('Identificador de imagem inválido.')
    return db.data_dir() / 'media' / job['id'] / (item['id'] + '.webp')


def prepare(data, *, banner=False):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Use uma imagem de até 8 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP') or source.width * source.height > MAX_PIXELS:
                    raise ValueError('Use uma imagem JPEG, PNG ou WebP de até 40 megapixels.')
                if getattr(source, 'is_animated', False):
                    raise ValueError('Use uma imagem estática.')
                source.load()
                picture = ImageOps.exif_transpose(source).convert('RGBA' if source.has_transparency_data else 'RGB')
                if banner:
                    picture = ImageOps.fit(picture, BANNER_SIZE, method=Image.Resampling.LANCZOS,
                                           centering=(0.5, 0.5))
                else:
                    picture.thumbnail((2400, 2400))
                output = io.BytesIO()
                if banner:
                    # Lossless compression preserves the resized pixels without another lossy pass.
                    picture.save(output, format='WEBP', lossless=True, quality=100, method=6)
                else:
                    picture.save(output, format='WEBP', quality=86, method=4)
                return output.getvalue(), picture.width, picture.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Não foi possível abrir essa imagem. Use um arquivo JPEG, PNG ou WebP válido.') from None


def add(job, data, **details):
    banner = details.get('origin') == 'ai'
    data, width, height = prepare(data, banner=banner)
    item = {'id': uuid.uuid4().hex, 'width': width, 'height': height, 'bytes': len(data),
            'alt': '', 'caption': '', 'credit': '', 'position': 'start', 'in_body': True,
            'featured': False, 'created_at': db.now(), 'origin': 'upload', **details}
    if banner:
        item['compression'] = 'lossless'
    destination = path(job, item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    if item['featured']:
        for other in job.get('media', []):
            other['featured'] = False
    job.setdefault('media', []).append(item)
    db.save_job(job)
    return item


def validate_position(job, position):
    if position not in ('start', 'end') and position not in {h['id'] for h in headings(job)}:
        raise ValueError('A seção mudou. Escolha novamente a posição da imagem.')


def public_item(job, item):
    return {k: v for k, v in item.items() if k not in ('wordpress',)} | {
        'url': f'/api/jobs/{job["id"]}/images/{item["id"]}/file',
        'position_missing': item['position'] not in ('start', 'end') and
                            item['position'] not in {h['id'] for h in headings(job)}}


def active_images(job):
    return [item for item in job.get('media', []) if item['in_body'] or item['featured']]


def import_key():
    file = db.data_dir() / 'media-signing.key'
    if not file.exists():
        try:
            with file.open('xb') as stream:
                stream.write(secrets.token_bytes(32))
            file.chmod(0o600)
        except FileExistsError:
            pass
    return file.read_bytes()


def signature(job_id, image_id, expires):
    return hmac.new(import_key(), f'{job_id}:{image_id}:{expires}'.encode(), hashlib.sha256).hexdigest()


def import_url(job, item, base, expires=None):
    expires = expires or int(time.time()) + LINK_SECONDS
    name = re.sub(r'[^a-z0-9-]', '-', job['article']['slug'].lower())[:80] + '-' + item['id'][:8] + '.webp'
    return f'{base}/media-export/{job["id"]}/{item["id"]}/{expires}.{signature(job["id"], item["id"], expires)}/{name}'


def valid_token(job_id, image_id, token):
    try:
        expires, signed = token.split('.', 1)
        expires = int(expires)
        return int(time.time()) <= expires <= int(time.time()) + LINK_SECONDS + 60 and hmac.compare_digest(
            signed, signature(job_id, image_id, expires))
    except (ValueError, TypeError):
        return False
