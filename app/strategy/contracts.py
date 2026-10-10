"""Pydantic contracts for the strategic intelligence module.

Each of the 8 strategic agents has a typed output schema. The coordinator
produces an StrategyPlan that references agent deliveries and proposes
prioritised opportunities.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyOutput(BaseModel):
    """Closed output objects; missing measurements are represented by null."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False,
                              populate_by_name=True, serialize_by_alias=True)


class QueryPerformance(StrategyOutput):
    query: str = Field(default='', max_length=500)
    clicks: int | None = Field(default=None, ge=0, strict=True)
    impressions: int | None = Field(default=None, ge=0, strict=True)
    position: float | None = Field(default=None, ge=0, strict=True)
    ctr: float | None = Field(default=None, ge=0, le=1, strict=True)


class PagePerformance(StrategyOutput):
    url: str = Field(default='', max_length=2000)
    clicks: int | None = Field(default=None, ge=0, strict=True)
    impressions: int | None = Field(default=None, ge=0, strict=True)
    position: float | None = Field(default=None, ge=0, strict=True)
    ctr: float | None = Field(default=None, ge=0, le=1, strict=True)
    previous_clicks: int | None = Field(default=None, ge=0, strict=True)
    delta_pct: float | None = Field(default=None, strict=True)
    period: str = Field(default='', max_length=100)
    detail: str = Field(default='', max_length=1500)


class QueryIntent(StrategyOutput):
    query: str = Field(default='', max_length=500)
    intent_type: str = Field(default='', max_length=100)
    stage: str = Field(default='', max_length=200)
    format: str = Field(default='', max_length=500)
    questions: list[str] = Field(default_factory=list, max_length=20)


class CompetitorProfile(StrategyOutput):
    domain: str = Field(default='', max_length=500)
    overlap_queries: list[str] = Field(default_factory=list, max_length=30)
    # Both historical prose and explicit lists express the same assessment.
    strengths: str | list[str] = Field(default='')
    gaps: str | list[str] = Field(default='')


class ContentCluster(StrategyOutput):
    name: str = Field(default='', max_length=500)
    pillar_page: str = Field(default='', max_length=2000)
    supporting_pages: list[str] = Field(default_factory=list, max_length=30)
    queries: list[str] = Field(default_factory=list, max_length=30)


class PageOverlap(StrategyOutput):
    page_a: str = Field(default='', max_length=2000)
    page_b: str = Field(default='', max_length=2000)
    overlap_queries: list[str] = Field(default_factory=list, max_length=30)
    suggestion: str = Field(default='', max_length=2000)


class InternalLinkSuggestion(StrategyOutput):
    from_page: str = Field(default='', alias='from', max_length=2000)
    to_page: str = Field(default='', alias='to', max_length=2000)
    anchor: str = Field(default='', max_length=500)
    reason: str = Field(default='', max_length=1500)


class TechnicalIssue(StrategyOutput):
    url: str = Field(default='', max_length=2000)
    issue_type: str = Field(default='', max_length=200)
    severity: str = Field(default='', max_length=100)
    detail: str = Field(default='', max_length=2000)


class SelectedVideo(StrategyOutput):
    video_id: str = Field(default='', max_length=100)
    url: str = Field(default='', max_length=2000)
    title: str = Field(default='', max_length=500)
    channel: str = Field(default='', max_length=500)
    reason: str = Field(default='', max_length=2000)
    key_contributions: list[str] = Field(default_factory=list, max_length=20)
    transcript_available: bool | None = None


class ApprovedChannel(StrategyOutput):
    channel_id: str = Field(default='', max_length=200)
    name: str = Field(default='', max_length=500)
    url: str = Field(default='', max_length=2000)
    reason: str = Field(default='', max_length=1500)


class InterventionResult(StrategyOutput):
    opportunity_id: str = Field(default='', max_length=100)
    url: str = Field(default='', max_length=2000)
    action: str = Field(default='', max_length=100)
    baseline_clicks: int | None = Field(default=None, ge=0, strict=True)
    current_clicks: int | None = Field(default=None, ge=0, strict=True)
    delta_pct: float | None = Field(default=None, strict=True)
    period: str = Field(default='', max_length=100)


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class StrategyEvidence(StrategyOutput):
    """A single piece of evidence backing a strategic recommendation."""
    source: str = Field(max_length=200, description='Origin: gsc, ga4, serp, audit, business, video, research')
    metric: str = Field(default='', max_length=200)
    value: str = Field(default='', max_length=500)
    period: str = Field(default='', max_length=100, description='Date range or snapshot id')
    detail: str = Field(default='', max_length=1500)


class StrategyFinding(StrategyOutput):
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

class BusinessAnalysis(StrategyOutput):
    """Agent 1 — Negócio e nicho."""
    summary: str = Field(max_length=3000)
    business_topics: list[str] = Field(max_length=30, description='Topics that help the audience and the business')
    commercial_priorities: list[str] = Field(default_factory=list, max_length=10)
    restrictions: list[str] = Field(default_factory=list, max_length=10)
    context_gaps: list[str] = Field(default_factory=list, max_length=10, description='Missing business info')
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class PerformanceAnalysis(StrategyOutput):
    """Agent 2 — Desempenho no Google."""
    summary: str = Field(max_length=3000)
    total_clicks: int | None = Field(default=None, ge=0, strict=True)
    total_impressions: int | None = Field(default=None, ge=0, strict=True)
    period: str = Field(default='', max_length=100)
    top_queries: list[QueryPerformance] = Field(default_factory=list, max_length=50)
    declining_pages: list[PagePerformance] = Field(default_factory=list, max_length=20)
    growing_pages: list[PagePerformance] = Field(default_factory=list, max_length=20)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class IntentAnalysis(StrategyOutput):
    """Agent 3 — Público e intenção."""
    summary: str = Field(max_length=3000)
    intents: list[QueryIntent] = Field(default_factory=list, max_length=30)
    audience_segments: list[str] = Field(default_factory=list, max_length=10)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class CompetitorAnalysis(StrategyOutput):
    """Agent 4 — SERP e concorrentes."""
    summary: str = Field(max_length=3000)
    competitors: list[CompetitorProfile] = Field(default_factory=list, max_length=15)
    serp_features: list[str] = Field(default_factory=list, max_length=20)
    content_gaps: list[str] = Field(default_factory=list, max_length=20, description='Topics competitors cover that we do not')
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ContentArchitecture(StrategyOutput):
    """Agent 5 — Arquitetura de conteúdo."""
    summary: str = Field(max_length=3000)
    clusters: list[ContentCluster] = Field(default_factory=list, max_length=30)
    overlaps: list[PageOverlap] = Field(default_factory=list, max_length=10)
    internal_link_suggestions: list[InternalLinkSuggestion] = Field(default_factory=list, max_length=20)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class TechnicalHealth(StrategyOutput):
    """Agent 6 — Saúde técnica."""
    summary: str = Field(max_length=3000)
    issues: list[TechnicalIssue] = Field(default_factory=list, max_length=30)
    indexation_status: str = Field(default='', max_length=500)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ContentCuration(StrategyOutput):
    """Agent 7 — Curadoria e planejamento editorial."""
    summary: str = Field(max_length=3000)
    selected_videos: list[SelectedVideo] = Field(default_factory=list, max_length=10,
        description='video_id, url, title, channel, reason, key_contributions, transcript_available')
    approved_channels: list[ApprovedChannel] = Field(default_factory=list, max_length=10)
    research_gaps: list[str] = Field(default_factory=list, max_length=10)
    briefing_notes: str = Field(default='', max_length=5000)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


class ResultsAnalysis(StrategyOutput):
    """Agent 8 — Resultados e experimentação."""
    summary: str = Field(max_length=3000)
    interventions_reviewed: list[InterventionResult] = Field(default_factory=list, max_length=20,
        description='opportunity_id, url, action, baseline_clicks, current_clicks, delta_pct, period')
    insights: list[str] = Field(default_factory=list, max_length=10)
    next_actions: list[str] = Field(default_factory=list, max_length=10)
    findings: list[StrategyFinding] = Field(default_factory=list, max_length=15)


# ---------------------------------------------------------------------------
# Opportunity — a prioritised action proposed by the coordinator
# ---------------------------------------------------------------------------

class Opportunity(StrategyOutput):
    """A prioritised content action derived from strategic analysis."""
    opportunity_id: str = Field(max_length=64)
    action: Literal['create', 'update', 'consolidate', 'improve_links', 'fix_technical', 'investigate']
    main_question: str = Field(max_length=500, description='What the reader needs to resolve')
    target_page: str = Field(default='', max_length=500, description='Existing page URL if applicable')
    queries: list[str] = Field(default_factory=list, max_length=30)
    related_products: list[str] = Field(default_factory=list, max_length=10)
    selected_videos: list[SelectedVideo] = Field(default_factory=list, max_length=5)
    evidence: list[StrategyEvidence] = Field(default_factory=list, max_length=15)
    justification: str = Field(max_length=3000)
    gaps: list[str] = Field(default_factory=list, max_length=10)
    effort: Literal['low', 'medium', 'high'] = 'medium'
    priority_score: float | None = Field(default=None, ge=0, le=100, strict=True,
                                        description='Heuristic score; null if not assessed, never a success probability')
    monitoring_plan: str = Field(default='', max_length=1000)
    status: Literal['proposed', 'approved', 'rejected', 'in_progress', 'completed', 'cancelled'] = 'proposed'


# ---------------------------------------------------------------------------
# Coordinator output — the strategy plan
# ---------------------------------------------------------------------------

class StrategyPlan(StrategyOutput):
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
