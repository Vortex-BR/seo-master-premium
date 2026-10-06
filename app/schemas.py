from typing import Literal
from pydantic import BaseModel, Field, field_validator


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=300)
    new_password: str = Field(min_length=12, max_length=300)


class EditorialDirection(BaseModel):
    topic: str = Field(default='', max_length=500)
    keyword: str = Field(default='', max_length=150)
    audience: str = Field(default='Pessoas buscando uma explicação clara e prática', max_length=500)
    tone: str = Field(default='Claro, próximo e profissional', max_length=300)
    instructions: str = Field(default='', max_length=3000)
    target_words: int = Field(default=1200, ge=500, le=2500)
    research: bool = True


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
    openai_api_key: str | None = Field(default=None, max_length=500)
    supadata_api_key: str | None = Field(default=None, max_length=500)
    youtube_proxy_urls: str | None = Field(default=None, max_length=30000)
    wp_url: str = Field(default='', max_length=500)
    wp_user: str = Field(default='', max_length=150)
    wp_password: str | None = Field(default=None, max_length=500)
    audio_fallback: bool = False


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
    markdown: str = Field(min_length=100, max_length=100000)
    tags: list[str] = Field(max_length=15)


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
    editorial_approval: bool = False


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
    quality: Literal['low', 'medium', 'high'] = 'low'
    size: Literal['1536x1024', '1024x1024', '1024x1536'] = '1536x1024'
    position: str = Field(default='start', max_length=80)
    featured: bool = False


class ReviewDecision(BaseModel):
    finding_index: int = Field(ge=0, le=1000)
    review_version: str = Field(min_length=1, max_length=100)
    article_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=20, max_length=1500)
    dismiss: bool = True
