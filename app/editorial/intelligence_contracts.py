"""Opt-in IEC request and provider contracts, separate from Video-First schemas."""
from decimal import Decimal, InvalidOperation
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


def safe_https_url(value: str) -> str:
    """Validate syntax locally; network readers must additionally pin public DNS."""
    if (not isinstance(value, str) or not value or len(value) > 2048
            or re.search(r'[\s\\\x00-\x1f\x7f]', value)):
        raise ValueError('A fonte complementar precisa de uma URL HTTPS pública válida.')
    try:
        parts = urlsplit(value)
        port = parts.port
        hostname = parts.hostname or ''
    except ValueError:
        raise ValueError('A fonte complementar precisa de uma URL HTTPS pública válida.') from None
    if (parts.scheme != 'https' or not hostname or parts.username is not None
            or parts.password is not None or port not in (None, 443)):
        raise ValueError('A fonte complementar precisa de uma URL HTTPS pública válida.')
    host = hostname.rstrip('.').encode('idna').decode('ascii').lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ('.' not in host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal'))
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                           for part in host.split('.'))):
            raise ValueError('A fonte complementar precisa de um domínio público válido.')
    else:
        if not address.is_global:
            raise ValueError('A fonte complementar não pode apontar para rede privada.')
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class IECRequest(Contract):
    article_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['shadow', 'suggest', 'apply'] = 'shadow'
    budget_usd: Decimal = Field(default=Decimal('0'), ge=0, allow_inf_nan=False)
    max_calls: int = Field(default=3, ge=1, le=6, strict=True)
    max_output_tokens: int = Field(default=2000, ge=256, le=8000, strict=True)
    allow_external: bool = Field(default=False, strict=True)
    external_urls: list[str] = Field(default_factory=list, max_length=6)
    trusted_domains: list[str] = Field(default_factory=list, max_length=20)
    freshness_hours: int = Field(default=24, ge=1, le=720, strict=True)

    @field_validator('budget_usd', mode='before')
    @classmethod
    def finite_budget(cls, value):
        if isinstance(value, bool):
            raise ValueError('O orçamento precisa ser numérico e finito.')
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('O orçamento precisa ser numérico e finito.') from None
        if not amount.is_finite():
            raise ValueError('O orçamento precisa ser numérico e finito.')
        return amount

    @field_validator('external_urls')
    @classmethod
    def valid_urls(cls, values):
        return list(dict.fromkeys(safe_https_url(value) for value in values))

    @field_validator('trusted_domains')
    @classmethod
    def valid_domains(cls, values):
        result = []
        for value in values:
            if (not value or re.search(r'[/@:#?*\\\s]', value)):
                raise ValueError('Informe somente domínios confiáveis, sem caminhos ou curingas.')
            try:
                host = value.rstrip('.').encode('idna').decode('ascii').lower()
            except UnicodeError:
                raise ValueError('Domínio confiável inválido.') from None
            safe_https_url('https://' + host)
            if host not in result:
                result.append(host)
        return result

    @model_validator(mode='after')
    def active_budget(self):
        if self.mode != 'shadow' and self.budget_usd <= 0:
            raise ValueError('A IEC ativa precisa de orçamento incremental positivo.')
        if self.external_urls and not self.allow_external:
            raise ValueError('As URLs complementares exigem pesquisa externa explicitamente habilitada.')
        if self.allow_external and not self.trusted_domains:
            raise ValueError('A pesquisa externa exige domínios confiáveis explícitos.')
        for url in self.external_urls:
            host = (urlsplit(url).hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
            if not any(host == domain or host.endswith('.' + domain) for domain in self.trusted_domains):
                raise ValueError('Uma URL complementar está fora dos domínios explicitamente confiáveis.')
        return self


class IECOpportunity(Contract):
    id: str = Field(min_length=1, max_length=100)
    block_id: str = Field(min_length=1, max_length=100)
    kind: Literal['missing_reason', 'prerequisite', 'context', 'condition', 'result', 'risk', 'ambiguity']
    question: str = Field(min_length=1, max_length=1000)
    benefit: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    external_query: str = Field(default='', max_length=1000)
    already_explained: bool = Field(default=False, strict=True)


class IECDiagnosis(Contract):
    status: Literal['no_change', 'opportunities']
    summary: str = Field(max_length=2000)
    opportunities: list[IECOpportunity] = Field(max_length=6)

    @model_validator(mode='after')
    def coherent(self):
        identifiers = [opportunity.id for opportunity in self.opportunities]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('As oportunidades precisam de IDs únicos.')
        if (self.status == 'no_change') != (not self.opportunities):
            raise ValueError('no_change exige oportunidades vazias.')
        return self


class IECSupport(Contract):
    reference_id: str = Field(min_length=1, max_length=150)
    excerpt: str = Field(min_length=1, max_length=12000)
    offset_start: StrictInt = Field(ge=0)
    offset_end: StrictInt = Field(gt=0)

    @model_validator(mode='after')
    def interval(self):
        if self.offset_end <= self.offset_start:
            raise ValueError('O intervalo da evidência precisa ser positivo.')
        return self


class IECProposal(Contract):
    opportunity_id: str = Field(min_length=1, max_length=100)
    block_id: str = Field(min_length=1, max_length=100)
    addition: str = Field(min_length=1, max_length=1800)
    origin: Literal['video', 'external_verified']
    claim_nature: Literal['assertion', 'opinion', 'experience', 'analogy', 'hypothesis']
    supports: list[IECSupport] = Field(min_length=1, max_length=12)
    limitations: str = Field(default='', max_length=1500)


class IECProposalBatch(Contract):
    summary: str = Field(max_length=2000)
    proposals: list[IECProposal] = Field(max_length=6)


class IECValidationAssessment(Contract):
    opportunity_id: str = Field(min_length=1, max_length=100)
    status: Literal['accept', 'reject', 'uncertain']
    precision: bool = Field(strict=True)
    attribution: bool = Field(strict=True)
    noncontradiction: bool = Field(strict=True)
    redundancy: bool = Field(strict=True)
    cohesion: bool = Field(strict=True)
    usefulness: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=1500)

    @model_validator(mode='after')
    def valid_acceptance(self):
        if self.status == 'accept' and not all((self.precision, self.attribution,
                self.noncontradiction, self.redundancy, self.cohesion, self.usefulness)):
            raise ValueError('Uma aprovação exige todas as verificações positivas.')
        return self


class IECValidation(Contract):
    summary: str = Field(max_length=2000)
    assessments: list[IECValidationAssessment] = Field(max_length=6)

    @model_validator(mode='after')
    def unique_assessments(self):
        identifiers = [assessment.opportunity_id for assessment in self.assessments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('A validação não pode repetir uma oportunidade.')
        return self
