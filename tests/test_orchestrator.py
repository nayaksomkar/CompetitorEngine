import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.orchestrator import Orchestrator
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult
from app.services.llm_client import LLMClient


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=LLMClient)


@pytest.fixture
def orchestrator(mock_llm):
    return Orchestrator(llm_client=mock_llm)


@pytest.fixture
def sample_form():
    return FormInput(
        business_name="TestCo",
        idea="AI analytics platform",
        industry="SaaS",
        products_services=["Dashboard"],
        target_customers="SMBs",
        geography="US",
        pricing="$49/month",
        business_model="Subscription",
        competitors=["CompA"],
        differentiators="AI-first",
        research_goals=["competitor_research"],
        user_query="How to compete?",
    )


def test_orchestrator_init(mock_llm):
    """Test orchestrator initializes all agents."""
    orch = Orchestrator(llm_client=mock_llm)
    assert orch.business_parser is not None
    assert orch.research_planner is not None
    assert orch.competitor_analyzer is not None
    assert orch.strategist is not None
    assert orch.reporter is not None


def test_orchestrator_init_default():
    """Test orchestrator initializes with default LLM client."""
    orch = Orchestrator()
    assert orch.llm is not None
    assert orch.scraper is not None


@pytest.mark.asyncio
async def test_run_analysis_success(orchestrator, mock_llm, sample_form):
    """Test successful end-to-end analysis run."""
    # Mock all LLM calls
    mock_llm.send_query.side_effect = [
        # Step 1: Summary
        "TestCo is an AI analytics platform for SMBs in the US.",
        # Step 2: Structured profile
        '{"business_name": "TestCo", "idea": "AI analytics", "industry": "SaaS", "products_services": ["Dashboard"], "target_customers": "SMBs", "geography": "US", "pricing": "$49/month", "business_model": "Subscription", "competitors": ["CompA"], "differentiators": "AI-first", "research_goals": ["competitor_research"], "user_query": "How to compete?"}',
        # WebSearchAgent: CompA summary
        "CompA is an analytics platform.",
        # WebSearchAgent: Dashboard summary
        "Dashboard is a product.",
        # Competitor analysis
        '[{"name": "CompA", "description": "Competitor", "strengths": ["Brand"], "weaknesses": ["Price"], "pricing": "$59/month", "market_position": "Leader", "source": "research", "explanation": "Main competitor"}]',
        # SWOT
        '{"strengths": [{"point": "AI-first", "explanation": "Unique", "source": "analysis"}], "weaknesses": [], "opportunities": [], "threats": []}',
        # Recommendations
        '[{"title": "Price below CompA", "description": "Undercut competitor", "priority": "high", "rationale": "Price advantage", "explanation": "Captures price-sensitive customers"}]',
        # Action plan
        '[{"action": "Set price at $39", "timeline": "Week 1", "priority": "high", "expected_outcome": "Competitive pricing", "explanation": "Undercuts CompA"}]',
        # Visualizations
        '{"charts": [{"chart_type": "bar", "title": "Pricing", "labels": ["TestCo", "CompA"], "datasets": [{"label": "Price", "data": [39, 59]}], "explanation": "Price comparison"}], "comparisons": []}',
        # Report
        "# TestCo Analysis Report\n\nExecutive summary...",
    ]

    result = await orchestrator.run_analysis(sample_form)

    assert isinstance(result, AnalysisResult)
    assert result.profile is not None
    assert result.profile.business_name == "TestCo"
    assert len(result.competitors) > 0
    assert result.report is not None


@pytest.mark.asyncio
async def test_run_analysis_parsing_failure(orchestrator, mock_llm, sample_form):
    """Test handling of parsing failure."""
    mock_llm.send_query.return_value = ""  # Empty response causes parsing failure

    with pytest.raises(Exception):
        await orchestrator.run_analysis(sample_form)


def test_generate_insights(orchestrator):
    """Test insight generation from context."""
    from app.schemas.analysis import SWOTAnalysis, SWOTItem

    swot = SWOTAnalysis(
        opportunities=[SWOTItem(point="Market gap", explanation="Untested")],
    )

    context = {
        "market_gap": {
            "structured": {
                "gaps_identified": [
                    {"gap": "No personalization", "opportunity_size": "high", "source": "research"}
                ]
            }
        }
    }

    insights = orchestrator._generate_insights(None, [], swot, context)
    assert len(insights) > 0
