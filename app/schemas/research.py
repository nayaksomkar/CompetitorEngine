"""Contract between CompetitorEngine and WebHunter."""
from typing import Any

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """What CompetitorEngine sends to WebHunter."""

    business: dict[str, Any] = Field(
        description="Business profile (FormInput shape) to research"
    )
    research_types: list[str] = Field(
        default_factory=list,
        description="Areas of research, e.g. competitor_research",
    )


class ResearchResponse(BaseModel):
    """What CompetitorEngine expects back from WebHunter."""

    results: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Research data keyed by research_type",
    )
