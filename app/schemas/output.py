"""Output contract — ORCHESTRATOR.md §6 (envelope) and
PARSER.md §6 (parser output contract).

`AnalysisResult` is the `data` block. `AnswerBlock` is
the one-off lookup result for question/compare/explain
intents (ORCHESTRATOR.md §5.2). `ParserOutput` is the
full response envelope returned by /api/v1/parser/execute.
"""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.analysis import (
    ComparisonTable,
    ContextUpdate,
    EntityStatus,
    MissingData,
    ParserInput,
    ResultCounts,
)
from app.schemas.business import BusinessProfile
from app.schemas.domain import (
    ActionPlanItem,
    ChartData,
    Competitor,
    EvidenceItem,
    MarketGap,
    MetricCard,
    InsightItem,
    PricingTier,
    Product,
    Report,
    Source,
    SWOT,
)


class Metadata(BaseModel):
    model_config = {"protected_namespaces": ()}

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_used: str = "llm-brain"
    confidence: float = 0.0  # 0-1 scale (differs from insight confidence 0-100)
    processing_time_ms: int = 0


# ── The `data` block (ORCHESTRATOR.md §6.1) ────────
class AnalysisResult(BaseModel):
    """Complete frontend-ready analysis payload."""

    business_summary: str = ""
    profile: BusinessProfile | None = None
    executive_summary: str = ""
    market_info: dict[str, Any] = Field(default_factory=dict)
    positioning: str = ""
    gaps: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    pricing_tiers: list[PricingTier] = Field(default_factory=list)
    market_gaps: list[MarketGap] = Field(default_factory=list)
    insights: list[InsightItem] = Field(default_factory=list)
    recommendations: list[InsightItem] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)
    reports: list[Report] = Field(default_factory=list)
    report: str = ""  # narrative report text (C3: both report + reports[] emitted)
    charts: list[ChartData] = Field(default_factory=list)
    metric_cards: list[MetricCard] = Field(default_factory=list)
    swot: SWOT = Field(default_factory=SWOT)
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)
    # Identity echo (PARSER.md §2.13) — displayed in Sidebar/Chat header/Overview
    businessName: str = ""
    industry: str = ""
    idea: str = ""
    targetCustomers: str = ""
    geography: str = ""
    pricing: str = ""
    businessModel: str = ""
    differentiators: str = ""
    researchGoals: list[str] = Field(default_factory=list)


# ── Answer block (ORCHESTRATOR.md §5.2) ────────────
class LookupProfile(BaseModel):
    """Profile of a looked-up competitor.

    marketShare / growthRate are None when the upstream evidence
    did not yield a value — never defaulted to 0, which would
    fabricate a share the sources do not support (PARSER.md §11).
    """

    description: str = ""
    pricingTier: str = ""
    marketPosition: str = ""
    marketShare: float | None = None
    growthRate: float | None = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    funding: str | None = None
    founded: str | None = None
    hq: str | None = None


class LookupCompetitor(BaseModel):
    """One-off lookup result card (ORCHESTRATOR.md §5.2)."""

    id: str = ""
    name: str = ""
    source: str = ""  # "web" | "context"
    lookupConfidence: int | None = None  # 0-100; badge when < 60
    profile: LookupProfile = Field(default_factory=LookupProfile)
    sources: list[Source] = Field(default_factory=list)


class AnswerBlock(BaseModel):
    """Populated for question / compare / explain intents;
    null for bootstrap (ORCHESTRATOR.md §14.3)."""

    summary: str = ""
    competitors: list[LookupCompetitor] = Field(default_factory=list)
    comparedTo: list[LookupCompetitor] = Field(default_factory=list)
    question: str | None = None  # explain intent
    explanation: str | None = None  # explain intent
    evidence: list[EvidenceItem] = Field(default_factory=list)  # explain intent
    sources: list[Source] = Field(default_factory=list)


# ── Provenance / UI hints (PARSER.md §6) ───────────
class DerivedData(BaseModel):
    """Provenance of backend-derived values."""

    charts_generated: list[str] = Field(default_factory=list)
    calculations_performed: list[str] = Field(default_factory=list)


class UIState(BaseModel):
    """Hints for the UI (tab activation, highlighting)."""

    active_tab: str = "overview"
    highlighted_entities: list[str] = Field(default_factory=list)
    result_counts: ResultCounts = Field(default_factory=ResultCounts)


class EvictedEntity(BaseModel):
    """An entity moved out of the active set (ORCHESTRATOR.md §8.4)."""

    id: str = ""
    name: str = ""


# ── Parser output envelope (PARSER.md §6, ORCHESTRATOR.md §6.1) ─
class ParserOutput(BaseModel):
    """Structured response from /api/v1/parser/execute."""

    intent: str
    status: str  # success, partial, error
    data: AnalysisResult = Field(default_factory=AnalysisResult)
    answer: AnswerBlock | None = None
    missing_data: list[MissingData] = Field(default_factory=list)
    context_update: ContextUpdate | None = None
    evicted_entities: list[EvictedEntity] = Field(default_factory=list)
    error: str | None = None
    result_counts: ResultCounts = Field(default_factory=ResultCounts)
    operations_performed: list[str] = Field(default_factory=list)
    entity_statuses: dict[str, list[EntityStatus]] = Field(
        default_factory=dict,
        description="Per-entity status keyed by entity type",
    )
    derived_data: DerivedData = Field(default_factory=DerivedData)
    ui_state: UIState = Field(default_factory=UIState)


class ParserExecuteRequest(BaseModel):
    """Request body for POST /api/v1/parser/execute."""

    parser_input: ParserInput


# ── Legacy chat contract (README; /api/v1/chat) ────
class ChatRequest(BaseModel):
    """Request body for POST /api/v1/chat."""

    session_id: str | None = None
    message: str
    current_analysis: dict[str, Any] | None = None
    fresh_research: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Response from POST /api/v1/chat."""

    session_id: str
    answer: str
    mini_charts: list[ChartData] = Field(default_factory=list)
    metric_cards: list[MetricCard] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
