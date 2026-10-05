from typing import Literal

from pydantic import BaseModel, Field


class VoiceProfile(BaseModel):
    tone: str = Field(default='Próximo, claro e profissional.', max_length=1000)
    vocabulary: str = Field(default='Palavras familiares. Explique termos técnicos necessários na primeira ocorrência.', max_length=1500)
    rhythm: str = Field(default='Frases diretas com variação natural. Uma ideia central por parágrafo. Transições apenas quando ajudam a conectar ideias.', max_length=1500)
    address: str = Field(default='Use você quando fizer sentido; mantenha a mesma forma de tratamento.', max_length=500)
    avoid: str = Field(default='Introduções genéricas, jargões desnecessários, repetição de palavra-chave, conectivos em excesso e experiências pessoais inventadas.', max_length=2000)
    approved_examples: str = Field(default='', max_length=6000)
    exceptions: str = Field(default='Preserve precisão, ressalvas e termos técnicos essenciais ao assunto.', max_length=2000)
    auto_apply: bool = True
    max_rounds: int = Field(default=1, ge=0, le=3)
    max_calls: int = Field(default=24, ge=12, le=60)


class ProfileUpdate(BaseModel):
    base_version: str = Field(max_length=64)
    profile: VoiceProfile


class Observation(BaseModel):
    severity: Literal['blocking', 'warning', 'info']
    passage: str = Field(description='Trecho literal do material examinado; vazio apenas para algo ausente.')
    reason: str
    suggestion: str
    source_ids: list[str]
    rule_ids: list[str]
    recipient: Literal['apuration', 'writing', 'seo', 'quality']


class Audit(BaseModel):
    summary: str
    findings: list[Observation]


class Edit(BaseModel):
    field: Literal['title', 'seo_title', 'slug', 'meta_description', 'excerpt', 'markdown']
    before: str = Field(description='Trecho literal e único do campo atual. Para substituir campo vazio use string vazia.')
    after: str
    reason: str
    rule_ids: list[str]
    source_ids: list[str]


class EditPlan(BaseModel):
    summary: str
    changes: list[Edit] = Field(max_length=20)
    findings: list[Observation]


class EditorialDecision(BaseModel):
    decision: Literal['ready', 'revise', 'needs_input']
    summary: str
    findings: list[Observation]


class ChangeDecision(BaseModel):
    article_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['apply', 'reject', 'undo']
