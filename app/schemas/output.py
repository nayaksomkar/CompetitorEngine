from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.analysis import (
    ActionItem,
    ChartData,
    ComparisonTable,
    CompetitorCard,
    ContextUpdate,
    EntityStatus,
    Insight,
    MissingData,
    ParserInput,
    Recommendation,
    ResultCounts,
    SWOTAnalysis,
)
from app.schemas.business import BusinessProfile


class Metadata(BaseModel):
    model_config = {"protected_namespaces": ()}

    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model_used: str = "llm-brain"
    confidence: float = 0.0
    processing_time_ms: int = 0


class SourceReference(BaseModel):
    source: str
    type: str = "web"  # web, llm_inference, user_input, web_search
    relevance: str = ""


class MetricCard(BaseModel):
    """Single numeric KPI for the frontend to render as a card."""

    label: str
    value: float
    unit: str = ""
    change: float | None = None  # optional delta vs prior period
    explanation: str = ""


class AnalysisResult(BaseModel):
    """Complete frontend-ready analysis output."""

    business_summary: str = ""
    profile: BusinessProfile | None = None
    executive_summary: str = ""
    market_info: dict[str, Any] = Field(default_factory=dict)
    positioning: str = ""
    gaps: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    competitors: list[CompetitorCard] = Field(default_factory=list)
    swot: SWOTAnalysis = Field(default_factory=SWOTAnalysis)
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    metric_cards: list[MetricCard] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    action_plan: list[ActionItem] = Field(default_factory=list)
    report: str = ""
    sources: list[SourceReference] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)


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
    sources: list[SourceReference] = Field(default_factory=list)


# ── Parser-driven execution contract (PARSER.md §6) ──────────
class ParserOutput(BaseModel):
    """Structured response from the parser-driven orchestrator.

    Follows the PARSER.md §6 output contract. Wraps the existing
    AnalysisResult with parser-specific metadata (status, missing
    data, context update, result counts).
    """

    intent: str
    status: str  # success, partial, error
    data: AnalysisResult
    missing_data: list[MissingData] = Field(default_factory=list)
    context_update: ContextUpdate | None = None
    error: str | None = None
    result_counts: ResultCounts = Field(default_factory=ResultCounts)
    operations_performed: list[str] = Field(default_factory=list)
    entity_statuses: dict[str, list[EntityStatus]] = Field(
        default_factory=dict,
        description="Per-entity status keyed by entity type",
    )


class ParserExecuteRequest(BaseModel):
    """Request body for POST /api/v1/parser/execute.

    Accepts the parser's structured output and executes the
    required operations.
    """

    parser_input: ParserInput
