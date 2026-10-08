from typing import Literal

from pydantic import BaseModel, Field
from ..schemas import EditorialAlignment, Evidence


class VoiceProfile(BaseModel):
    tone: str = Field(default='Próximo, claro e profissional.', max_length=1000)
    vocabulary: str = Field(default='Palavras familiares. Explique termos técnicos necessários na primeira ocorrência.', max_length=1500)
    rhythm: str = Field(default='Frases diretas com variação natural. Cada parágrafo desenvolve uma ideia central com contexto e ligação com a seção e as ideias anteriores. O artigo tem início que situa a pergunta, meio que desenvolve a resposta e fim que encerra o raciocínio. Transições apenas quando esclarecem relações reais.', max_length=1500)
    address: str = Field(default='Use você quando fizer sentido; mantenha a mesma forma de tratamento.', max_length=500)
    avoid: str = Field(default='Introduções genéricas, jargões desnecessários, repetição de palavra-chave, conectivos em excesso e experiências pessoais inventadas.', max_length=2000)
    approved_examples: str = Field(default='', max_length=6000)
    exceptions: str = Field(default='Preserve precisão, ressalvas e termos técnicos essenciais ao assunto.', max_length=2000)
    auto_apply: bool = True
    auto_write: bool = True
    max_rounds: int = Field(default=0, ge=0, le=0)
    max_calls: int = Field(default=8, ge=4, le=8)
    research_tool_calls: int = Field(default=2, ge=1, le=2)
    context_chars: int = Field(default=90000, ge=30000, le=240000)
    block_chars: int = Field(default=7000, ge=3000, le=12000)


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


class Quantity(BaseModel):
    value: str = Field(max_length=150)
    unit: str = Field(max_length=100)
    context: str = Field(max_length=500)


class SpokenInsight(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    spoken_explanation: str = Field(min_length=1, max_length=6000,
        description='A explicação do criador, preservando o tom natural, o raciocínio e as analogias.')
    practical_tips: list[str] = Field(max_length=20, description='Dicas práticas citadas pelo criador.')
    analogies: list[str] = Field(max_length=20, description='Analogias ou metáforas usadas pelo criador.')
    warnings: list[str] = Field(max_length=20, description='Alertas de erros que o criador mencionou.')
    source_segment_ids: list[str] = Field(min_length=1, max_length=30,
        description='IDs dos trechos originais deste vídeo que sustentam a explicação.')


class VideoSpokenInsights(BaseModel):
    video_id: str = Field(min_length=1, max_length=80)
    summary: str = Field(max_length=2000)
    insights: list[SpokenInsight] = Field(max_length=80)
    gaps: list[str] = Field(max_length=20)


class SpokenExtraction(BaseModel):
    summary: str = Field(max_length=3000)
    videos: list[VideoSpokenInsights] = Field(min_length=1, max_length=5)


class BackgroundTerm(BaseModel):
    model_config = {'extra': 'forbid'}

    term: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=1500)
    source_segment_ids: list[str] = Field(min_length=1, max_length=8)
    internal_context_only: Literal[True] = True


class BackgroundKnowledge(BaseModel):
    model_config = {'extra': 'forbid'}

    terms: list[BackgroundTerm] = Field(max_length=30)
    internal_context_only: Literal[True] = True


class KnowledgeItem(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=1200)
    kind: Literal['fato', 'opinião', 'experiência']
    information_type: Literal['conceito', 'procedimento', 'exemplo', 'comparação', 'ressalva', 'afirmação']
    method: str = Field(max_length=500)
    conditions: list[str] = Field(max_length=12)
    quantities: list[Quantity] = Field(max_length=12)
    restrictions: list[str] = Field(max_length=12)
    evidence: list['Evidence'] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(max_length=12)


class BlockKnowledge(BaseModel):
    summary: str = Field(max_length=1200)
    items: list[KnowledgeItem] = Field(max_length=30)
    gaps: list[str] = Field(max_length=20)
    empty_reason: str = Field(max_length=500, description='Justifique apenas quando o bloco não contém conhecimento extraível.')


class ItemCheck(BaseModel):
    item_id: str
    status: Literal['supported', 'uncertain', 'unsupported']
    reason: str = Field(max_length=1000)


class KnowledgeAudit(BaseModel):
    summary: str = Field(max_length=1200)
    checks: list[ItemCheck]


class ResearchAnswer(BaseModel):
    issue_id: str
    status: Literal['resolved', 'unresolved']
    reason: str = Field(min_length=20, max_length=1500)
    evidence: list['Evidence']


class ResearchResolution(BaseModel):
    summary: str
    answers: list[ResearchAnswer]


class SourceRelation(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=8)
    relation: Literal['condition', 'restriction', 'contradiction', 'sequence', 'example']
    explanation: str = Field(max_length=1500)


class VideoContext(BaseModel):
    summary: str = Field(max_length=2000)
    relations: list[SourceRelation]
    gaps: list[str]


class TopicGroup(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    item_ids: list[str] = Field(min_length=1)


class TopicRouting(BaseModel):
    summary: str
    topics: list[TopicGroup] = Field(min_length=1)
    catalog: list[str] = Field(default_factory=list, max_length=8)


class ComparisonRow(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=8)
    relation: Literal['complement', 'repetition', 'agreement', 'different_methods', 'divergence', 'insufficient']
    explanation: str = Field(max_length=1500)
    treatment: Literal['combine', 'attribute_alternatives', 'keep_separate', 'research', 'exclude', 'pending']
    essential: bool


class TopicComparison(BaseModel):
    summary: str = Field(max_length=2000)
    rows: list[ComparisonRow] = Field(min_length=1)
    research_questions: list[str] = Field(max_length=12)


class ClaimDisposition(BaseModel):
    item_id: str
    status: Literal['used', 'duplicate', 'out_of_scope', 'unsupported', 'pending']
    reason: str = Field(min_length=1, max_length=1000)


class SectionPresentation(BaseModel):
    mode: Literal['explanation', 'steps', 'checklist', 'comparison']
    reason: str = Field(min_length=10, max_length=500)
    subheadings: list[str] = Field(max_length=12,
        description='H3 somente para subdivisões que precisam de desenvolvimento próprio; vazio quando desnecessários.')


class SectionPlan(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=1000)
    item_ids: list[str]
    prerequisites: list[str]
    conditions: list[str]
    transition: str = Field(max_length=1000)
    pending: list[str]
    presentation: SectionPresentation | None = None


class TopicPlan(BaseModel):
    summary: str
    sections: list[SectionPlan] = Field(max_length=10)
    dispositions: list[ClaimDisposition]


class PlanIssuePriority(BaseModel):
    issue_id: str
    essential: bool
    reason: str = Field(min_length=20, max_length=1500)


class ReaderJourney(BaseModel):
    kind: Literal['sequencial', 'explicativo', 'comparativo', 'analítico', 'resenha', 'misto']
    goal: str = Field(min_length=10, max_length=600)
    reason: str = Field(min_length=20, max_length=1200)
    video_item_ids: list[str] = Field(description='Itens conferidos dos vídeos que guiam este percurso; não são IDs de fontes.')


class PlanStructure(BaseModel):
    main_question: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=3, max_length=200)
    opening: str = Field(min_length=1, max_length=1500)
    closing: str = Field(min_length=1, max_length=1500)
    ready_to_write: bool = Field(description='False quando falta informação indispensável para responder à pergunta central.')
    sections: list[SectionPlan] = Field(min_length=1, max_length=30)
    pending: list[str]
    issue_priorities: list[PlanIssuePriority] = Field(default_factory=list)
    reader_journey: ReaderJourney | None = None  # Existing plans remain readable without inventing a decision.


class ArticlePlan(PlanStructure):
    dispositions: list[ClaimDisposition]


class EditorialPlan(ArticlePlan):
    research_questions: list[str] = Field(max_length=4)


class PlanUpdate(BaseModel):
    base_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    plan: ArticlePlan


class IssueResolution(BaseModel):
    reason: str = Field(min_length=20, max_length=2000)
    source_ids: list[str] = Field(max_length=30)


class DraftSection(BaseModel):
    markdown: str = Field(min_length=30, max_length=18000)
    used_item_ids: list[str]


class ArticleMetadata(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    seo_title: str = Field(max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    meta_description: str = Field(max_length=500)
    excerpt: str = Field(max_length=1500)
    tags: list[str] = Field(max_length=15)


class DraftArticle(ArticleMetadata):
    markdown: str = Field(min_length=30, max_length=60000)
    used_item_ids: list[str]


class PassageAssessment(BaseModel):
    passage_id: str
    status: Literal['supported', 'not_factual', 'unsupported', 'uncertain']
    reason: str = Field(max_length=1500)
    evidence: list['Evidence'] = Field(max_length=12)
    used_item_ids: list[str]


class PassageAudit(BaseModel):
    summary: str = Field(max_length=2000)
    assessments: list[PassageAssessment]


class VideoFidelityReview(PassageAudit):
    editorial_alignment: EditorialAlignment
