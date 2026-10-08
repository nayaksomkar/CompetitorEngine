"""Parser contract schemas (PARSER.md §5–§7, ORCHESTRATOR.md §4).

ParserInput is what the UI sends to /api/v1/parser/execute.
ContextUpdate is the compact session context the UI stores in
sessionStorage and echoes back on every follow-up.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.business import FormInput
from app.schemas.domain import SWOT


ActionId = Literal[
    "explain_context",
    "show_pricing",
    "show_market_position",
    "show_weaknesses",
    "show_strengths",
    "show_sources",
    "compare_competitors",
    "show_market_share",
    "show_growth",
    "show_market_gap",
    "show_supporting_data",
    "explain_trend",
    "show_price_gaps",
    "explain_premium_positioning",
    "show_affected_competitors",
    "explain_opportunity",
    "show_supporting_evidence",
    "explore_related_products",
    "explore_implications",
    "show_underlying_data",
]


class StructuredAction(BaseModel):
    """Allowlisted contextual action emitted by the dashboard."""

    action: ActionId
    entity: str
    section: str
    target: str | None = None


# ── Parser input (ORCHESTRATOR.md §4.2) ──────────────────
class ParserInput(BaseModel):
    """Structured input from the parser / UI.

    The UI sends `{ parser_input: {...} }`. The orchestrator
    owns all routing decisions from here.
    """

    intent: str = Field(
        default="bootstrap",
        description=(
            "bootstrap, question, refine, compare, explain, "
            "regenerate, follow-up"
        ),
    )
    message: str = Field(
        default="",
        description="User's natural-language request (question/compare/explain/refine)",
    )
    session_id: str | None = Field(
        default=None,
        description="Stable session identifier (UI-generated UUID)",
    )
    context_update: dict[str, Any] | None = Field(
        default=None,
        description="Last context_update from a previous response (from sessionStorage)",
    )
    current_analysis: dict[str, Any] | None = Field(
        default=None,
        description="Last full data payload (for rich follow-ups)",
    )
    action: StructuredAction | None = Field(
        default=None,
        description="Allowlisted contextual action from the dashboard",
    )
    form_input: FormInput | None = Field(
        default=None,
        description="Bootstrap questionnaire payload (bootstrap/refine/regenerate)",
    )
    requested_count: int = Field(
        default=3, ge=1, le=3, description="Max competitors (1-3)"
    )
    # Parser-extracted semantic payloads (optional, for rich requests)
    entities: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extracted entities: competitor_names, product_names, "
            "industries, metrics, features, pricing_mentions, etc."
        ),
    )
    facts: dict[str, Any] = Field(
        default_factory=dict, description="Extracted facts about the business"
    )
    constraints: list[str] = Field(
        default_factory=list, description="Active constraints from context"
    )
    requested_operations: list[str] = Field(
        default_factory=list,
        description=(
            "Operations to perform: extract, infer, calculate, "
            "normalize, categorize, generate"
        ),
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Fields the parser couldn't extract"
    )
    confidence: dict[str, float] = Field(
        default_factory=dict, description="Confidence scores for extracted values"
    )


# ── Per-entity status (ORCHESTRATOR.md §6.3, PARSER.md §4.4) ──
class EntityStatus(BaseModel):
    """Per-entity status for fault-tolerant rendering."""

    id: str = ""
    name: str = ""
    status: str = "complete"  # complete, partial, loading, failed
    missing_fields: list[str] = Field(default_factory=list)
    source: str = ""  # "web" | "context"
    lookupConfidence: int | None = None  # 0-100, web-looked-up entities


# ── Missing data report (ORCHESTRATOR.md §7.3) ───────────
class MissingData(BaseModel):
    """Description of data that could not be obtained."""

    field: str
    reason: str
    severity: str = "warning"  # critical, warning, info


# ── Context update (PARSER.md §7.2, ORCHESTRATOR.md §8.2) ─
class ContextUpdate(BaseModel):
    """Compact context the UI stores in sessionStorage.

    Key: 'competitor_analysis_context'. Echoed back with every
    follow-up request. Never stores conversation history, full
    analysis payloads, or chart data (PARSER.md §7.4).
    """

    version: int = 1
    business: dict[str, Any] = Field(
        default_factory=dict,
        description="{name, industry, pricing, model}",
    )
    entities: dict[str, Any] = Field(
        default_factory=dict,
        description="{competitors: [slug], products: [slug], focus: slug|null}",
    )
    result_meta: dict[str, Any] = Field(
        default_factory=dict,
        description="{requested_count, retrieved_count, filters: []}",
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="{included: [], excluded: []}",
    )
    keywords: list[str] = Field(
        default_factory=list, description="5-10 compact keyword tags"
    )


# ── Result counts (ORCHESTRATOR.md §6.1) ─────────────────
class ResultCounts(BaseModel):
    """Counts of requested/retrieved/valid/displayed entities."""

    requested: int = 0
    retrieved: int = 0
    valid: int = 0
    displayed: int = 0


# ── Comparison (ORCHESTRATOR.md §6.1 `comparisons` field;
#    element shape not documented — kept from the legacy contract) ──
class ComparisonRow(BaseModel):
    feature: str
    values: dict[str, str]  # {entity_name: value}


class ComparisonTable(BaseModel):
    title: str
    entities: list[str] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)
    explanation: str = ""


# ── Parser research-plan vocabulary (PARSER.md §5 Step 6) ─
class ResearchStep(BaseModel):
    name: str
    research_type: str
    requires_research: bool = True
    description: str = ""


class ResearchPlan(BaseModel):
    steps: list[ResearchStep] = Field(default_factory=list)
    reasoning: str = ""


# Re-exported for callers that build form payloads.
__all__ = [
    "ParserInput",
    "EntityStatus",
    "MissingData",
    "ContextUpdate",
    "ResultCounts",
    "ComparisonRow",
    "ComparisonTable",
    "ResearchStep",
    "ResearchPlan",
    "StructuredAction",
    "SWOT",
]
