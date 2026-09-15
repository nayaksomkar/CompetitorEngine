from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.schemas.business import FormInput


# ── Parser contract schemas (PARSER.md) ─────────────────────
class ParserInput(BaseModel):
    """Structured input from the parser.

    The parser's output becomes the orchestrator's input. This
    carries intent, extracted entities, facts, constraints, and
    the original business data when available.
    """

    intent: str = Field(
        default="bootstrap",
        description=(
            "bootstrap, question, refine, compare, explain, "
            "regenerate, follow-up"
        ),
    )
    entities: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extracted entities: competitor_names, product_names, "
            "industries, metrics, features, pricing_mentions, etc."
        ),
    )
    facts: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted facts about the business",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Active constraints from context",
    )
    requested_operations: list[str] = Field(
        default_factory=list,
        description=(
            "Operations to perform: extract, infer, calculate, "
            "normalize, categorize, generate"
        ),
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Fields the parser couldn't extract",
    )
    confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Confidence scores for extracted values",
    )
    # Original business data (required for bootstrap/refine)
    form_input: FormInput | None = None
    # Context from previous interactions
    context: dict[str, Any] | None = None
    # Session/chat fields
    session_id: str | None = None
    message: str = ""
    current_analysis: dict[str, Any] | None = None
    fresh_research: dict[str, Any] | None = None
    # Requested competitor count for dynamic data (max 3).
    requested_count: int = Field(
        default=3, ge=1, le=3, description="Max competitors (1-3)"
    )


class EntityStatus(BaseModel):
    """Per-entity status for fault-tolerant rendering."""

    id: str = ""
    name: str = ""
    status: str = "complete"  # complete, partial, loading, failed
    missing_fields: list[str] = Field(default_factory=list)


class MissingData(BaseModel):
    """Description of data that could not be obtained."""

    field: str
    reason: str
    severity: str = "warning"  # critical, warning, info


class ContextUpdate(BaseModel):
    """Compact context update for the parser to store."""

    version: int = 1
    business: dict[str, Any] = Field(default_factory=dict)
    entities: dict[str, Any] = Field(default_factory=dict)
    result_meta: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)


class ResultCounts(BaseModel):
    """Counts of requested/retrieved/valid/displayed entities."""

    requested: int = 0
    retrieved: int = 0
    valid: int = 0
    displayed: int = 0


class CompetitorCard(BaseModel):
    name: str
    description: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    pricing: str = ""
    market_position: str = ""
    source: str = ""
    explanation: str = Field("", description="Why this competitor matters to the user")


class SWOTItem(BaseModel):
    point: str
    explanation: str = ""
    source: str = ""


class SWOTAnalysis(BaseModel):
    strengths: list[SWOTItem] = Field(default_factory=list)
    weaknesses: list[SWOTItem] = Field(default_factory=list)
    opportunities: list[SWOTItem] = Field(default_factory=list)
    threats: list[SWOTItem] = Field(default_factory=list)


class ComparisonRow(BaseModel):
    feature: str
    values: dict[str, str]  # {entity_name: value}


class ComparisonTable(BaseModel):
    title: str
    entities: list[str] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)
    explanation: str = ""


class Insight(BaseModel):
    title: str
    description: str
    importance: str = "medium"  # low, medium, high
    source: str = ""
    explanation: str = ""


class Recommendation(BaseModel):
    title: str
    description: str
    priority: str = "medium"  # low, medium, high
    rationale: str = ""
    explanation: str = ""


class ChartData(BaseModel):
    chart_type: str  # bar, line, pie, radar, scatter
    title: str
    labels: list[str] = Field(default_factory=list)
    datasets: list[dict] = Field(default_factory=list)
    explanation: str = ""


class ActionItem(BaseModel):
    action: str
    timeline: str = ""  # e.g., "Week 1-2", "Month 1"
    priority: str = "medium"
    expected_outcome: str = ""
    explanation: str = ""


class ResearchStep(BaseModel):
    name: str
    research_type: str
    requires_research: bool = True
    description: str = ""


class ResearchPlan(BaseModel):
    steps: list[ResearchStep] = Field(default_factory=list)
    reasoning: str = ""
