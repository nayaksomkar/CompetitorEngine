import pytest
from unittest.mock import AsyncMock

from app.agents.research_planner import ResearchPlannerAgent
from app.schemas.business import BusinessProfile
from app.schemas.analysis import ResearchStep
from app.services.llm_client import LLMClient


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=LLMClient)


@pytest.fixture
def planner(mock_llm):
    return ResearchPlannerAgent(mock_llm)


@pytest.fixture
def sample_profile():
    return BusinessProfile(
        business_name="TestCo",
        idea="AI analytics",
        industry="SaaS",
        research_goals=["competitor_pricing", "market_gaps"],
        user_query="How to compete?",
        summary="Test summary",
    )


@pytest.mark.asyncio
async def test_create_plan_from_goals(planner, mock_llm, sample_profile):
    """Test plan creation from explicit research goals."""
    # Should use rule-based planning, no LLM call needed
    plan = await planner.create_plan(sample_profile)

    assert len(plan.steps) > 0
    step_types = [s.research_type for s in plan.steps]
    assert "competitor_research" in step_types or "pricing_research" in step_types


@pytest.mark.asyncio
async def test_create_plan_no_goals_uses_llm(planner, mock_llm):
    """Test that LLM is used when no explicit goals."""
    profile = BusinessProfile(
        business_name="TestCo",
        idea="AI analytics",
        industry="SaaS",
        research_goals=[],
        user_query="General analysis",
        summary="Test",
    )

    mock_llm.send_query.return_value = '{"steps": [{"name": "competitor_research", "research_type": "competitor_research", "requires_research": true, "description": "Test"}], "reasoning": "Default"}'

    plan = await planner.create_plan(profile)
    assert len(plan.steps) == 1
    mock_llm.send_query.assert_called_once()


@pytest.mark.asyncio
async def test_create_plan_fallback_on_llm_error(planner, mock_llm):
    """Test fallback to default plan when LLM fails."""
    profile = BusinessProfile(
        business_name="TestCo",
        idea="AI analytics",
        industry="SaaS",
        research_goals=[],
        user_query="Help",
        summary="Test",
    )

    mock_llm.send_query.return_value = "Invalid response"

    plan = await planner.create_plan(profile)
    assert len(plan.steps) > 0  # Should have default plan


def test_rule_based_plan(planner, sample_profile):
    """Test rule-based plan generation."""
    steps = planner._rule_based_plan(sample_profile)
    assert len(steps) > 0
    assert all(isinstance(s, ResearchStep) for s in steps)


def test_rule_based_plan_empty_goals(planner):
    """Test rule-based plan with no goals."""
    profile = BusinessProfile(
        business_name="TestCo",
        idea="Test",
        industry="SaaS",
        research_goals=[],
        summary="Test",
    )
    steps = planner._rule_based_plan(profile)
    assert len(steps) == 0
