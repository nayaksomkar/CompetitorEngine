from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.analysis import (
    ActionItem,
    ChartData,
    ComparisonTable,
    CompetitorCard,
    Insight,
    Recommendation,
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
    type: str = "web"  # web, llm_inference, user_input
    relevance: str = ""


class AnalysisResult(BaseModel):
    """Complete frontend-ready analysis output."""

    business_summary: str = ""
    profile: BusinessProfile | None = None
    competitors: list[CompetitorCard] = Field(default_factory=list)
    swot: SWOTAnalysis = Field(default_factory=SWOTAnalysis)
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    action_plan: list[ActionItem] = Field(default_factory=list)
    report: str = ""
    sources: list[SourceReference] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)
