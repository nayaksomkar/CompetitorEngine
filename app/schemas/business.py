from pydantic import BaseModel, Field


class FormInput(BaseModel):
    """Raw form submission from frontend."""

    business_name: str = Field(..., min_length=1, description="Name of the business")
    idea: str = Field(..., min_length=1, description="Core business idea or concept")
    industry: str = Field(..., description="Industry or sector")
    products_services: list[str] = Field(default_factory=list, description="Products or services offered")
    target_customers: str = Field(default="", description="Target customer segment")
    geography: str = Field(default="", description="Geographic market")
    pricing: str = Field(default="", description="Pricing strategy or model")
    business_model: str = Field(default="", description="Business model type")
    competitors: list[str] = Field(default_factory=list, description="Known competitors")
    differentiators: str = Field(default="", description="Key differentiators")
    research_goals: list[str] = Field(
        default_factory=list,
        description="What the user wants to research (e.g., competitor_pricing, market_gaps, customer_reviews)",
    )
    user_query: str = Field(default="", description="Optional specific question from user")


class BusinessProfile(BaseModel):
    """Validated structured business profile after parsing."""

    business_name: str
    idea: str
    industry: str
    products_services: list[str] = Field(default_factory=list)
    target_customers: str = ""
    geography: str = ""
    pricing: str = ""
    business_model: str = ""
    competitors: list[str] = Field(default_factory=list)
    differentiators: str = ""
    research_goals: list[str] = Field(default_factory=list)
    user_query: str = ""
    summary: str = Field("", description="Human-readable summary of the business")
