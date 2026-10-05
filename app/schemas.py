from typing import Literal
from pydantic import BaseModel, Field, field_validator


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=300)
    new_password: str = Field(min_length=12, max_length=300)


class Brief(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=5)
    topic: str = Field(default='', max_length=500)
    keyword: str = Field(default='', max_length=150)
    audience: str = Field(default='Pessoas buscando uma explicação clara e prática', max_length=500)
    tone: str = Field(default='Claro, próximo e profissional', max_length=300)
    instructions: str = Field(default='', max_length=3000)
    target_words: int = Field(default=1200, ge=500, le=2500)
    research: bool = True
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
    passage: str
    reason: str
    suggestion: str
    source_ids: list[str]


class Review(BaseModel):
    summary: str
    findings: list[Finding]
    supported_claims: list[Claim]


class ManualSource(BaseModel):
    video_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{11}$')
    text: str = Field(min_length=100, max_length=120000)


class ExportRequest(BaseModel):
    editorial_approval: bool = False
