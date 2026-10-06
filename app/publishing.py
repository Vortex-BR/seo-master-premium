"""Shared article rendering and native WordPress WXR exports."""
import base64
import html
import json
import re
from datetime import datetime, timezone
from xml.dom.minidom import Document

import bleach
from markdown_it import MarkdownIt

from . import media


def caption(item):
    return ' — '.join(s for s in (item.get('caption'), item.get('credit')) if s)


def block(name, content, attributes=None):
    attrs = ' ' + json.dumps(attributes, ensure_ascii=False, separators=(',', ':')).replace('--', '\\u002d\\u002d').replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026') if attributes else ''
    return f'<!-- wp:{name}{attrs} -->\n{content.strip()}\n<!-- /wp:{name} -->'


def render(job, *, gutenberg=False, image_urls=None, image_ids=None, embedded=False):
    from .generation import evidence_map
    mapping = evidence_map(job)
    def citation(match):
        key = match.group(1)
        source = mapping.get(key)
        return f'[{key}]({source["url"]})' if source else f'[referência ausente: {key}]'
    markdown = re.sub(r'\[\[([\w-]+)\]\]', citation, job['article']['markdown'])
    parser = MarkdownIt('commonmark', {'html': False})
    tokens = parser.parse(markdown)
    heading_ids = iter(h['id'] for h in media.headings(job))
    valid_positions = {h['id'] for h in media.headings(job)} | {'start', 'end'}
    placements = {}
    for item in job.get('media', []):
        if item['in_body']:
            position = item['position'] if item['position'] in valid_positions else 'end'
            placements.setdefault(position, []).append(item)
    def pictures(position):
        figures = []
        for item in placements.get(position, []):
            url = (image_urls or {}).get(item['id'], media.public_item(job, item)['url'])
            if embedded:
                url = 'data:image/webp;base64,' + base64.b64encode(media.path(job, item).read_bytes()).decode()
            image_id = (image_ids or {}).get(item['id'])
            attributes = {'sizeSlug': 'full', 'linkDestination': 'none'}
            image_class = ''
            if image_id:
                attributes['id'] = image_id
                image_class = f' class="wp-image-{image_id}"'
            dimensions = '' if gutenberg else f' width="{item["width"]}" height="{item["height"]}" loading="lazy" decoding="async"'
            figure = (f'<figure class="wp-block-image size-full"><img src="{html.escape(url, quote=True)}" '
                      f'alt="{html.escape(item["alt"], quote=True)}"{image_class}{dimensions}/>')
            if caption(item):
                figure += f'<figcaption class="wp-element-caption">{html.escape(caption(item))}</figcaption>'
            figure += '</figure>'
            figures.append(block('image', figure, attributes) if gutenberg else figure)
        return figures
    rendered = pictures('start')
    index = 0
    while index < len(tokens):
        first, end, depth = tokens[index], index + 1, tokens[index].nesting
        while depth > 0 and end < len(tokens):
            depth += tokens[end].nesting
            end += 1
        fragment = parser.renderer.render(tokens[index:end], parser.options, {})
        fragment = bleach.clean(fragment, tags={'p', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em',
            'blockquote', 'a', 'code', 'pre', 'hr', 'br'}, attributes={'a': ['href', 'title']}, protocols={'https'}, strip=True)
        if gutenberg:
            if first.type == 'paragraph_open':
                fragment = block('paragraph', fragment)
            elif first.type == 'heading_open' and first.tag in ('h2', 'h3', 'h4'):
                fragment = fragment.replace(f'<{first.tag}>', f'<{first.tag} class="wp-block-heading">', 1)
                fragment = block('heading', fragment, {'level': int(first.tag[1])} if first.tag != 'h2' else None)
            else:
                # Complex lists/quotes/code retain sanitized HTML in a native Custom HTML block.
                fragment = block('html', fragment)
        rendered.append(fragment)
        if first.type == 'heading_open':
            rendered.extend(pictures(next(heading_ids)))
        index = end
    rendered.extend(pictures('end'))
    return '\n\n'.join(rendered)


def yoast_meta(job):
    article = job['article']
    return {'_yoast_wpseo_title': article['seo_title'], '_yoast_wpseo_metadesc': article['meta_description'],
            '_yoast_wpseo_focuskw': job['brief']['keyword']}


def wxr(job, base):
    """WordPress Importer downloads signed media URLs and rewrites them to local attachments."""
    doc = Document()
    rss = doc.createElement('rss')
    rss.setAttribute('version', '2.0')
    for prefix, uri in {'wp': 'http://wordpress.org/export/1.2/', 'content': 'http://purl.org/rss/1.0/modules/content/',
                        'excerpt': 'http://wordpress.org/export/1.2/excerpt/', 'dc': 'http://purl.org/dc/elements/1.1/'}.items():
        rss.setAttribute('xmlns:' + prefix, uri)
    doc.appendChild(rss)
    def node(parent, tag, text=None, cdata=False, **attrs):
        element = doc.createElement(tag)
        for key, value in attrs.items():
            element.setAttribute(key, str(value))
        if text is not None:
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(text))
            if cdata:
                parts = text.split(']]>')
                for index, part in enumerate(parts):
                    element.appendChild(doc.createCDATASection(('>' if index else '') + part + (']]' if index < len(parts)-1 else '')))
            else:
                element.appendChild(doc.createTextNode(text))
        parent.appendChild(element)
        return element
    channel = node(rss, 'channel')
    node(channel, 'title', 'SEO MASTER PREMIUM')
    node(channel, 'link', base)
    node(channel, 'description', 'Artigo exportado como rascunho')
    node(channel, 'language', 'pt-BR')
    node(channel, 'wp:wxr_version', '1.2')
    node(channel, 'wp:base_site_url', base)
    node(channel, 'wp:base_blog_url', base)
    author = node(channel, 'wp:author')
    node(author, 'wp:author_id', 1)
    node(author, 'wp:author_login', 'seo-master', cdata=True)
    node(author, 'wp:author_display_name', 'SEO MASTER', cdata=True)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    post_id = 10000
    def entry(item_id, title, slug, kind, status, content='', excerpt='', guid=''):
        item = node(channel, 'item')
        node(item, 'title', title, cdata=True)
        node(item, 'dc:creator', 'seo-master', cdata=True)
        node(item, 'guid', guid or f'{base}/?p={item_id}', isPermaLink='false')
        node(item, 'content:encoded', content, cdata=True)
        node(item, 'excerpt:encoded', excerpt, cdata=True)
        for name, value in {'post_id': item_id, 'post_date': stamp, 'post_date_gmt': stamp,
            'post_name': slug, 'status': status, 'post_parent': post_id if kind == 'attachment' else 0,
            'post_type': kind, 'is_sticky': 0, 'comment_status': 'closed', 'ping_status': 'closed'}.items():
            node(item, 'wp:' + name, value)
        return item
    def meta(item, key, value):
        pair = node(item, 'wp:postmeta')
        node(pair, 'wp:meta_key', key, cdata=True)
        node(pair, 'wp:meta_value', value, cdata=True)
    urls, featured_id = {}, None
    for index, image in enumerate(media.active_images(job), 10001):
        url = media.import_url(job, image, base)
        urls[image['id']] = url
        item = entry(index, image['alt'] or job['article']['title'], 'imagem-' + image['id'], 'attachment', 'inherit',
                     excerpt=caption(image), guid=url)
        node(item, 'wp:attachment_url', url)
        meta(item, '_wp_attachment_image_alt', image['alt'])
        if image['featured']:
            featured_id = index
    article = job['article']
    # Avoid source attachment IDs in block attributes; importer maps featured IDs separately.
    post = entry(post_id, article['title'], article['slug'], 'post', 'draft',
                 content=render(job, gutenberg=True, image_urls=urls), excerpt=article['excerpt'],
                 guid=base + '/seo-master/' + job['id'])
    for key, value in yoast_meta(job).items():
        meta(post, key, value)
    if featured_id:
        meta(post, '_thumbnail_id', featured_id)
    for tag in article['tags']:
        node(post, 'category', tag, cdata=True, domain='post_tag', nicename=tag)
    return doc.toxml(encoding='utf-8')
