from pydantic import BaseModel, Field


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
