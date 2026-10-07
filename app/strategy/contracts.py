"""Pydantic contracts for the strategic intelligence module.

Each of the 8 strategic agents has a typed output schema. The coordinator
produces an StrategyPlan that references agent deliveries and proposes
prioritised opportunities.
"""
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class StrategyEvidence(BaseModel):
    """A single piece of evidence backing a strategic recommendation."""
    source: str = Field(max_length=200, description='Origin: gsc, ga4, serp, audit, business, video, research')
    metric: str = Field(default='', max_length=200)
    value: str = Field(default='', max_length=500)
    period: str = Field(default='', max_length=100, description='Date range or snapshot id')
    detail: str = Field(default='', max_length=1500)


class StrategyFinding(BaseModel):
    """An observation or recommendation from a strategic agent."""
    severity: Literal['critical', 'opportunity', 'info']
    area: str = Field(max_length=200)
    summary: str = Field(max_length=2000)
    evidence: list[StrategyEvidence] = Field(default_factory=list, max_length=10)
    suggestion: str = Field(default='', max_length=2000)
    related_pages: list[str] = Field(default_factory=list, max_length=20)
    related_queries: list[str] = Field(default_factory=list, max_length=30)


# ---------------------------------------------------------------------------
# Agent output contracts (one per strategic role)
# ---------------------------------------------------------------------------

class BusinessAnalysis(BaseModel):
    """Agent 1 — Negócio e nicho."""
    summary: str = Field(max_length=3000)
    business_topics: list[str] = Field(max_length=30, description='Topics that help the audience and the business')
    commercial_priorities: list[str] = Field(default_factory=list, max_length=10)
    restrictions: list[str] = Field(default_factory=list, max_length=10)
    context_gaps: list[str] = Field(default_factory=list, max_length=10, description='Missing business info')
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class PerformanceAnalysis(BaseModel):
    """Agent 2 — Desempenho no Google."""
    summary: str = Field(max_length=3000)
    total_clicks: int = Field(default=0, ge=0)
    total_impressions: int = Field(default=0, ge=0)
    period: str = Field(default='', max_length=100)
    top_queries: list[dict] = Field(default_factory=list, max_length=50, description='query, clicks, impressions, position, ctr')
    declining_pages: list[dict] = Field(default_factory=list, max_length=20)
    growing_pages: list[dict] = Field(default_factory=list, max_length=20)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class IntentAnalysis(BaseModel):
    """Agent 3 — Público e intenção."""
    summary: str = Field(max_length=3000)
    intents: list[dict] = Field(default_factory=list, max_length=30, description='query, intent_type, stage, format, questions')
    audience_segments: list[str] = Field(default_factory=list, max_length=10)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class CompetitorAnalysis(BaseModel):
    """Agent 4 — SERP e concorrentes."""
    summary: str = Field(max_length=3000)
    competitors: list[dict] = Field(default_factory=list, max_length=15, description='domain, overlap_queries, strengths, gaps')
    serp_features: list[str] = Field(default_factory=list, max_length=20)
    content_gaps: list[str] = Field(default_factory=list, max_length=20, description='Topics competitors cover that we do not')
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ContentArchitecture(BaseModel):
    """Agent 5 — Arquitetura de conteúdo."""
    summary: str = Field(max_length=3000)
    clusters: list[dict] = Field(default_factory=list, max_length=30, description='name, pillar_page, supporting_pages, queries')
    overlaps: list[dict] = Field(default_factory=list, max_length=10, description='page_a, page_b, overlap_queries, suggestion')
    internal_link_suggestions: list[dict] = Field(default_factory=list, max_length=20)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class TechnicalHealth(BaseModel):
    """Agent 6 — Saúde técnica."""
    summary: str = Field(max_length=3000)
    issues: list[dict] = Field(default_factory=list, max_length=30, description='url, issue_type, severity, detail')
    indexation_status: str = Field(default='', max_length=500)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ContentCuration(BaseModel):
    """Agent 7 — Curadoria e planejamento editorial."""
    summary: str = Field(max_length=3000)
    selected_videos: list[dict] = Field(default_factory=list, max_length=10,
        description='video_id, url, title, channel, reason, key_contributions, transcript_available')
    approved_channels: list[dict] = Field(default_factory=list, max_length=10)
    research_gaps: list[str] = Field(default_factory=list, max_length=10)
    briefing_notes: str = Field(default='', max_length=5000)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ResultsAnalysis(BaseModel):
    """Agent 8 — Resultados e experimentação."""
    summary: str = Field(max_length=3000)
    interventions_reviewed: list[dict] = Field(default_factory=list, max_length=20,
        description='opportunity_id, url, action, baseline_clicks, current_clicks, delta_pct, period')
    insights: list[str] = Field(default_factory=list, max_length=10)
    next_actions: list[str] = Field(default_factory=list, max_length=10)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


# ---------------------------------------------------------------------------
# Opportunity — a prioritised action proposed by the coordinator
# ---------------------------------------------------------------------------

class Opportunity(BaseModel):
    """A prioritised content action derived from strategic analysis."""
    opportunity_id: str = Field(max_length=64)
    action: Literal['create', 'update', 'consolidate', 'improve_links', 'fix_technical', 'investigate']
    main_question: str = Field(max_length=500, description='What the reader needs to resolve')
    target_page: str = Field(default='', max_length=500, description='Existing page URL if applicable')
    queries: list[str] = Field(default_factory=list, max_length=30)
    related_products: list[str] = Field(default_factory=list, max_length=10)
    selected_videos: list[dict] = Field(default_factory=list, max_length=5)
    evidence: list[StrategyEvidence] = Field(default_factory=list, max_length=15)
    justification: str = Field(max_length=3000)
    gaps: list[str] = Field(default_factory=list, max_length=10)
    effort: Literal['low', 'medium', 'high'] = 'medium'
    priority_score: float = Field(default=0.0, ge=0, le=100, description='Heuristic score, not a success probability')
    monitoring_plan: str = Field(default='', max_length=1000)
    status: Literal['proposed', 'approved', 'rejected', 'in_progress', 'completed', 'cancelled'] = 'proposed'


# ---------------------------------------------------------------------------
# Coordinator output — the strategy plan
# ---------------------------------------------------------------------------

class StrategyPlan(BaseModel):
    """Coordinator output: synthesis of all agent analyses into a prioritised plan."""
    summary: str = Field(max_length=5000)
    opportunities: list[Opportunity] = Field(max_length=30)
    conflicts: list[str] = Field(default_factory=list, max_length=10, description='Divergences between agents')
    context_gaps: list[str] = Field(default_factory=list, max_length=10, description='Missing info that blocks decisions')
    next_cycle_focus: str = Field(default='', max_length=2000)


# ---------------------------------------------------------------------------
# Strategy cycle — execution state
# ---------------------------------------------------------------------------

class StrategyBudget(BaseModel):
    """Limits for a single strategy cycle."""
    max_agent_calls: int = Field(default=20, ge=8, le=60)
    max_tokens_estimate: int = Field(default=200000, ge=50000, le=2000000)
    max_research_queries: int = Field(default=4, ge=0, le=10)
    max_video_lookups: int = Field(default=5, ge=0, le=20)


class StrategyRequest(BaseModel):
    """User request to start a strategy cycle."""
    project_id: str = Field(default='default', max_length=100)
    focus: str = Field(default='', max_length=2000, description='Optional focus area for this cycle')
    budget: StrategyBudget = Field(default_factory=StrategyBudget)


class OpportunityDecision(BaseModel):
    """User decision on a proposed opportunity."""
    action: Literal['approve', 'reject']
    reason: str = Field(default='', max_length=1000)


class OpportunityProduce(BaseModel):
    """Optional video URLs to produce an opportunity as an editorial job."""
    urls: list[str] = Field(default_factory=list, max_length=5)

