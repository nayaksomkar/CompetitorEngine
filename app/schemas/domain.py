"""Domain objects — PARSER.md §2 field-level contracts.

These are the exact shapes the UI renders. Field names follow
PARSER.md (camelCase inside domain objects); the orchestrator
envelope fields are snake_case per ORCHESTRATOR.md §6.1.

Every enum field is a plain string with a documented default
(PARSER.md §4.1) rather than a strict Literal — upstream LLM
output varies (e.g. "Mid-range" vs "Mid", FORbacked conflict
C4) and the orchestrator normalizes instead of rejecting.
"""
from typing import Any

from pydantic import BaseModel, Field


# ── Enums (documented defaults, PARSER.md §4.1) ──────────
MARKET_POSITIONS = {"Leader", "Challenger", "Niche", "Emerging", "unknown"}
MATURITY_LEVELS = {"Beta", "GA", "Deprecated", "Roadmap"}
INSIGHT_CATEGORIES = {
    "Opportunity",
    "Risk",
    "Trend",
    "Recommendation",
    "Insight",
}
IMPACT_LEVELS = {"High", "Medium", "Low"}
PRIORITIES = {"P0", "P1", "P2"}
HORIZONS = {"Now", "Next", "Later"}
EFFORT_LEVELS = {"Low", "Medium", "High"}
BILLING_CADENCES = {"monthly", "yearly"}
REPORT_TYPES = {
    "Executive Summary",
    "Deep Dive",
    "Market Landscape",
    "Go-to-Market",
}
CHART_KINDS = {"bar", "line", "area", "radar", "pie"}
ENTITY_STATUSES = {"complete", "partial", "loading", "failed"}


def clamp_score(value: Any, low: int = 0, high: int = 100) -> int:
    """Clamp a numeric score to [low, high]; non-numeric → low."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return low
    return int(max(low, min(high, num)))


# ── Explanation ("Explain this" modal, PARSER.md §2.11) ──
class EvidenceItem(BaseModel):
    label: str = ""
    detail: str = ""


class Explanation(BaseModel):
    summary: str = ""
    whyItMatters: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    sources: list["Source"] = Field(default_factory=list)


# ── SWOT (string arrays, PARSER.md §2.12) ────────────────
class SWOT(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)


# ── Competitor (PARSER.md §2.2) ──────────────────────────
class Competitor(BaseModel):
    id: str = ""
    name: str = ""
    logoColor: str = ""
    description: str = ""
    funding: str | None = None
    founded: str | None = None
    hq: str | None = None
    marketShare: float | None = None
    growthRate: float | None = None
    pricingTier: str = ""
    marketPosition: str = "Emerging"
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    swot: SWOT = Field(default_factory=SWOT)
    explanation: Explanation | None = None
    status: str = "complete"


# ── Product / ProductFeature (PARSER.md §2.3) ────────────
class ProductFeature(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    maturity: str = "GA"
    adoption: float = 0.0


class Product(BaseModel):
    id: str = ""
    competitorId: str = ""
    name: str = ""
    tagline: str = ""
    category: str = ""
    pricingModel: str = ""
    startingPrice: float | None = None
    features: list[ProductFeature] = Field(default_factory=list)
    status: str = "complete"


# ── PricingTier (PARSER.md §2.4) ─────────────────────────
class PricingTier(BaseModel):
    id: str = ""
    competitorId: str = ""
    name: str = ""
    priceMonthly: float | str = "Custom"  # number or "Custom"
    billing: str = "monthly"
    features: list[str] = Field(default_factory=list)
    bestFor: str = ""
    pricingModel: str = ""
    highlighted: bool = False


# ── MarketGap (PARSER.md §2.5) ───────────────────────────
class MarketGap(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    opportunityScore: float = 0.0
    difficultyScore: float = 0.0
    estimatedRevenue: str = ""
    affectedSegments: list[str] = Field(default_factory=list)


# ── InsightItem (PARSER.md §2.6) ─────────────────────────
class InsightItem(BaseModel):
    id: str = ""
    title: str = ""
    summary: str = ""
    detail: str = ""
    category: str = "Insight"
    impact: str = "Medium"
    confidence: float = 0.0
    relatedCompetitors: list[str] = Field(default_factory=list)
    explanation: str = ""


# ── ActionPlanItem (PARSER.md §2.7) ──────────────────────
class ActionPlanItem(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    rationale: str = ""
    owner: str = ""
    priority: str = "P1"
    horizon: str = "Next"
    effort: str = "Medium"
    impact: str = "Medium"


# ── Report / ReportSection (PARSER.md §2.8) ──────────────
class ReportSection(BaseModel):
    heading: str = ""
    body: str = ""


class Report(BaseModel):
    id: str = ""
    title: str = ""
    summary: str = ""
    type: str = "Executive Summary"
    date: str = ""
    pages: int = 0
    sections: list[ReportSection] = Field(default_factory=list)


# ── ChartData (PARSER.md §2.9) ───────────────────────────
class ChartPoint(BaseModel):
    label: str = ""
    value: float = 0.0
    color: str | None = None


class ChartSeries(BaseModel):
    id: str = ""
    name: str = ""
    color: str = ""
    points: list[ChartPoint] = Field(default_factory=list)


class ChartData(BaseModel):
    title: str = ""
    kind: str = "bar"
    xLabel: str = ""
    yLabel: str = ""
    series: list[ChartSeries] = Field(default_factory=list)


# ── Source (PARSER.md §2.10) ─────────────────────────────
class Source(BaseModel):
    id: str = ""
    title: str = ""
    url: str = ""
    publisher: str = ""
    date: str = ""
    snippet: str = ""


# ── MetricCard (KPI card; schema not documented in PARSER.md) ──
class MetricCard(BaseModel):
    label: str = ""
    value: float = 0.0
    unit: str = ""
    change: float | None = None
    explanation: str = ""


Explanation.model_rebuild()
