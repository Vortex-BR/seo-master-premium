from typing import Literal
from pydantic import BaseModel, Field, field_validator


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=300)
    new_password: str = Field(min_length=12, max_length=300)


class EditorialDirection(BaseModel):
    topic: str = Field(default='', max_length=500)
    main_question: str = Field(default='', max_length=500)
    intent: str = Field(default='Explicar e responder à dúvida do leitor', max_length=500)
    genre: Literal['explicação', 'tutorial', 'comparação', 'análise', 'resenha'] = 'explicação'
    exclusions: str = Field(default='', max_length=2000)
    keyword: str = Field(default='', max_length=150)
    audience: str = Field(default='Pessoas buscando uma explicação clara e prática', max_length=500)
    tone: str = Field(default='Claro, próximo e profissional', max_length=300)
    instructions: str = Field(default='', max_length=3000)
    target_words: int | None = Field(default=1200, ge=500, le=2500,
        description='Meta opcional; null deixa a extensão seguir a cobertura das fontes e da pauta.')
    research: bool = False  # Compatibility field; article generation never starts new searches.


class Brief(EditorialDirection):
    urls: list[str] = Field(min_length=1, max_length=5)
    extract_only: bool = False

    @field_validator('urls')
    @classmethod
    def valid_urls(cls, values):
        from .youtube import video_id
        result = []
        for value in values:
            canonical = 'https://www.youtube.com/watch?v=' + video_id(value)
            if canonical not in result:
                result.append(canonical)
        return result


class Settings(BaseModel):
    brand_name: str = Field(default='', max_length=120)
    brand_voice: str = Field(default='', max_length=2500)
    model: str = Field(default='gpt-4.1-mini', min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._:-]+$')
    image_model: str = Field(default='gpt-image-2', min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._:-]+$')
    connection_test_budget_usd: float = Field(default=0.01, gt=0, strict=True, allow_inf_nan=False)
    openai_api_key: str | None = Field(default=None, max_length=500)
    supadata_api_key: str | None = Field(default=None, max_length=500)
    pexels_api_key: str | None = Field(default=None, max_length=500)
    pixabay_api_key: str | None = Field(default=None, max_length=500)
    youtube_proxy_urls: str | None = Field(default=None, max_length=30000)
    youtube_connection_mode: Literal['auto', 'direct', 'proxy'] = 'auto'
    wp_url: str = Field(default='', max_length=500)
    wp_user: str = Field(default='', max_length=150)
    wp_password: str | None = Field(default=None, max_length=500)
    audio_fallback: bool = False
    transcript_provider: Literal['local', 'auto', 'supadata', 'youtube'] = 'local'
    supadata_mode: Literal['native', 'auto'] = 'native'
    transcript_timeout: int = Field(default=180, ge=60, le=600)
    whisper_model: Literal['tiny', 'base', 'small', 'medium', 'large-v3'] = 'small'
    whisper_threads: int = Field(default=2, ge=1, le=16)
    audio_max_minutes: int = Field(default=180, ge=15, le=360)
    audio_max_mb: int = Field(default=256, ge=16, le=1024)
    local_transcript_timeout: int = Field(default=10800, ge=300, le=21600)


class TranscriptReset(BaseModel):
    confirm_new_request: bool = False


class Evidence(BaseModel):
    source_id: str
    excerpt: str


class Claim(BaseModel):
    statement: str
    kind: Literal['fato', 'opinião', 'experiência']
    evidence: list[Evidence]


class Dossier(BaseModel):
    main_question: str
    summary: str
    claims: list[Claim]
    examples: list[str]
    conflicts: list[str]
    gaps: list[str]
    outline: list[str]


class Article(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    seo_title: str = Field(max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    meta_description: str = Field(max_length=500)
    excerpt: str = Field(max_length=1500)
    markdown: str = Field(min_length=1, max_length=100000)
    tags: list[str] = Field(max_length=15)


class ArticleEdit(Article):
    base_article_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class Finding(BaseModel):
    severity: Literal['blocking', 'warning', 'info']
    passage: str = Field(description='Citação literal do artigo avaliado, nunca da transcrição. Vazio apenas para ausência de conteúdo.')
    reason: str
    suggestion: str
    source_ids: list[str]


class EditorialAlignment(BaseModel):
    matches_brief: bool = Field(description='O artigo avaliado atende ao tema e gênero pedidos? False se uma pauta sobre o assunto virou análise do vídeo ou apresentador.')
    reason: str = Field(description='Justificativa da avaliação do artigo recebido, não do artigo que poderia ser escrito.')
    passage: str = Field(description='Trecho literal do artigo que demonstra o desvio; vazio quando não há desvio.')


class ReviewedClaim(Claim):
    statement: str = Field(description='Trecho copiado literalmente do ARTIGO que está apoiado pelas fontes. Não copiar aqui uma afirmação presente apenas na transcrição.')


class Review(BaseModel):
    evaluated_title: str = Field(description='Copie exatamente o título do artigo recebido para revisão.')
    editorial_alignment: EditorialAlignment
    summary: str = Field(description='Resultado da auditoria: problemas encontrados ou ausência deles. Não é um resumo do tema.')
    findings: list[Finding]
    supported_claims: list[ReviewedClaim]


class ManualSource(BaseModel):
    video_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{11}$')
    text: str = Field(min_length=100, max_length=120000)


class ExportRequest(BaseModel):
    editorial_approval: bool = False  # Deprecated compatibility input; clicking send is sufficient.


class ImageDetails(BaseModel):
    alt: str = Field(default='', max_length=500)
    caption: str = Field(default='', max_length=1000)
    credit: str = Field(default='', max_length=300)
    position: str = Field(default='start', max_length=80)
    in_body: bool = True
    featured: bool = False


class ImageGeneration(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9-]{32,36}$')
    prompt: str = Field(default='', max_length=3000)
    style: Literal['photo', 'illustration'] = 'photo'
    quality: Literal['low', 'medium', 'high'] = 'high'
    # Accept old clients; all new deliveries use the fixed banner dimensions.
    size: Literal['1280x420', '1536x1024', '1024x1024', '1024x1536'] = '1280x420'
    reference_mode: Literal['auto', 'selected', 'none'] = 'auto'
    reference_query: str = Field(default='', max_length=100)
    reference_ids: list[str] = Field(default_factory=list, max_length=3)
    position: str = Field(default='start', max_length=80)
    featured: bool = False


class ImageReferenceSearch(BaseModel):
    query: str = Field(min_length=2, max_length=100)


class ReviewDecision(BaseModel):
    finding_index: int = Field(ge=0, le=1000)
    review_version: str = Field(min_length=1, max_length=100)
    article_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=20, max_length=1500)
    dismiss: bool = True
