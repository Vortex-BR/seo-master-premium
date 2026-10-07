import pytest
from copy import deepcopy
from unittest.mock import Mock
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-password-long-enough')
    monkeypatch.setenv('COOKIE_SECURE', '0')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('SUPADATA_API_KEY', raising=False)
    monkeypatch.delenv('TRANSCRIPT_PROVIDER', raising=False)
    monkeypatch.delenv('SUPADATA_MODE', raising=False)
    monkeypatch.delenv('TRANSCRIPT_TIMEOUT', raising=False)
    for name in ('WHISPER_MODEL', 'WHISPER_THREADS', 'AUDIO_MAX_MINUTES', 'AUDIO_MAX_MB',
                 'LOCAL_TRANSCRIPT_TIMEOUT', 'WHISPER_DEVICE', 'WHISPER_COMPUTE_TYPE', 'WHISPER_MODEL_REVISION'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv('PEXELS_API_KEY', raising=False)
    monkeypatch.delenv('PIXABAY_API_KEY', raising=False)
    monkeypatch.delenv('YOUTUBE_PROXY_URLS', raising=False)
    monkeypatch.delenv('YOUTUBE_CONNECTION_MODE', raising=False)
    monkeypatch.delenv('APP_URL', raising=False)
    with TestClient(app, headers={'X-Requested-With': 'SEO-Master'}) as c:
        yield c


@pytest.fixture
def authed(client):
    assert client.post('/api/login', json={'password': 'test-password-long-enough'}).status_code == 200
    return client


@pytest.fixture
def job(authed):
    source = {'id': 'v1', 'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'video_id': 'abcdefghijk',
              'title': 'Uma horta de manjericão', 'author': 'Autor de exemplo', 'thumbnail': '', 'status': 'ok',
              'segments': [{'id': 'v1s1', 'text': 'O autor observa o desenvolvimento das folhas do manjericão.', 'start': 10, 'end': 20}]}
    article = {'title': 'Como observar uma horta de manjericão', 'seo_title': 'Como observar uma horta de manjericão',
               'slug': 'horta-manjericao', 'meta_description': 'Observações sobre uma horta de manjericão.',
               'excerpt': 'Um relato de observação.', 'tags': ['horta'],
               'markdown': '## Observação da horta\n\nO autor observa o desenvolvimento das folhas do manjericão. [[v1s1]]\n\nO relato é uma experiência pessoal.'}
    value = {'id': 'test-job', 'created_at': db.now(), 'status': 'ready', 'brief': {'urls': [source['url']],
             'topic': 'Horta', 'keyword': 'horta', 'audience': 'Iniciantes', 'tone': 'Claro', 'instructions': '',
             'target_words': 800, 'research': False}, 'sources': [source], 'article': article, 'events': [], 'usage': []}
    from app.generation import article_hash
    value['review'] = {'article_hash': article_hash(article), 'findings': [], 'supported_claims': [], 'summary': 'Revisado'}
    db.save_job(value)
    return value


@pytest.fixture
def newsroom_ai(job, monkeypatch):
    """Deterministic provider boundary; real coordinator, persistence and validation."""
    from app import generation, pipeline
    from app.editorial.contracts import EditPlan, EditorialDecision
    from app.editorial.contracts import (ArticleMetadata, DraftSection, PlanStructure, BlockKnowledge, KnowledgeAudit, PassageAudit,
                                        TopicComparison, TopicPlan, TopicRouting, VideoContext)
    from app.schemas import Dossier, Article, Review
    dossier = {'main_question': 'Como observar a horta?', 'summary': 'Observação das folhas.',
               'claims': [{'statement': 'O autor observa as folhas.', 'kind': 'experiência',
                           'evidence': [{'source_id': 'v1s1', 'excerpt': 'observa o desenvolvimento das folhas'}]}],
               'examples': [], 'conflicts': [], 'gaps': [], 'outline': ['Observação']}

    def respond(current, schema, instruction, stage, extra=None):
        extra = extra or {}
        if schema is BlockKnowledge:
            owned = extra['block']['owned']
            def content(part):
                text=part['text'].strip()
                return text if len(text)<=1200 else text[:1200].split('. ')[0]
            return {'summary': 'Observações da fonte.', 'items': [{'topic': 'observação',
                'statement': content(part), 'kind': 'experiência', 'information_type': 'afirmação',
                'method': '', 'conditions': [], 'quantities': [], 'restrictions': [],
                'evidence': [{'source_id': part['source_id'], 'excerpt': content(part)}],
                'limitations': []} for part in owned], 'gaps': [], 'empty_reason': ''}
        if schema is KnowledgeAudit:
            return {'summary': 'Significado conferido.', 'checks': [
                {'item_id': item['id'], 'status': 'supported', 'reason': 'A fala sustenta a formulação.'}
                for item in extra['items']]}
        if schema is VideoContext:
            return {'summary': 'Contexto do vídeo preservado.', 'relations': [], 'gaps': []}
        if schema is TopicRouting:
            return {'summary': 'Assuntos agrupados.', 'catalog': ['observação'], 'topics': [{'topic': 'observação',
                    'item_ids': [i['id'] for i in extra['items']]}]}
        if schema is TopicComparison:
            return {'summary': 'Explicações complementares.', 'rows': [{
                'item_ids': [i['id'] for i in extra['items']], 'relation': 'complement',
                'explanation': 'Contribuições preservadas.', 'treatment': 'combine', 'essential': False}],
                'research_questions': []}
        if schema is TopicPlan:
            ids = [i['id'] for i in extra['items'] if i['check']['status'] == 'supported']
            return {'summary': 'Planejamento do assunto.', 'sections': [{'id': 'observacao',
                'title': 'Observação da horta', 'question': 'Como observar a horta?',
                'purpose': 'Explicar as observações disponíveis.', 'item_ids': ids,
                'prerequisites': [], 'conditions': [], 'transition': 'Encerrar a explicação.', 'pending': []}],
                'dispositions': [{'item_id': i['id'], 'status': 'used' if i['id'] in ids else 'pending',
                                 'reason': 'Contribuição à pergunta.'} for i in extra['items']]}
        if schema is PlanStructure:
            return {'main_question': 'Como observar a horta?', 'title': job['article']['title'],
                    'opening': 'Situar a observação da horta.', 'closing': 'Encerrar o raciocínio.',
                    'ready_to_write': True,
                    'sections': [s for p in extra['topic_plans'] for s in p['sections']],
                    'pending': []}
        if schema is PassageAudit:
            assessments = []
            for passage in extra['passages']:
                items = [i for i in extra['items'] if any('[['+e['source_id']+']]' in passage['text'] for e in i['evidence'])]
                assessments.append({'passage_id': passage['id'], 'status': 'supported' if items else 'not_factual',
                    'reason': 'Conferência semântica do trecho.',
                    'evidence': [e for i in items for e in i['evidence']], 'used_item_ids': [i['id'] for i in items]})
            return {'summary': 'Todos os trechos conferidos.', 'assessments': assessments}
        if schema is DraftSection:
            section=extra['section']
            if section['id'] in ('opening','closing'):
                text='Esta parte organiza a leitura e apresenta o caminho da explicação.' if section['id']=='opening' else 'O percurso da leitura encerra a explicação apresentada nas seções anteriores.'
                return {'markdown':text, 'used_item_ids':[]}
            return {'markdown':'## '+section['title']+'\n\n'+'\n\n'.join(
                i['statement'].split('. ')[0]+'. '+ ' '.join('[['+e['source_id']+']]' for e in i['evidence'])
                for i in extra['items']), 'used_item_ids':section['item_ids']}
        if schema is ArticleMetadata:
            return {k:v for k,v in job['article'].items() if k!='markdown'}
        if schema is Dossier:
            return deepcopy(dossier)
        if schema is Article:
            return deepcopy(job['article'])
        if schema is Review:
            return {'evaluated_title': current['article']['title'],
                    'editorial_alignment': {'matches_brief': True, 'reason': 'Atende à pauta.', 'passage': ''},
                    'summary': 'Fatos conferidos.', 'findings': [], 'supported_claims': []}
        if schema is EditPlan:
            return {'summary': 'Texto preservado.', 'changes': [], 'findings': []}
        if schema is EditorialDecision:
            return {'decision': 'ready', 'summary': 'Pronto para revisão editorial.', 'findings': []}
        return {'summary': 'Material conferido.', 'findings': []}

    provider = Mock(side_effect=respond)
    provider.respond = respond
    monkeypatch.setattr(generation, 'structured', provider)
    monkeypatch.setattr(pipeline, 'get_secret', lambda name: 'test-key')
    return provider
