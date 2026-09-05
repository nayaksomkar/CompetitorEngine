import pytest
from unittest.mock import AsyncMock, patch

from app.agents.business_parser import BusinessParserAgent, ParsingValidationError
from app.schemas.business import FormInput, BusinessProfile
from app.services.llm_client import LLMClient


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=LLMClient)


@pytest.fixture
def parser(mock_llm):
    return BusinessParserAgent(mock_llm)


@pytest.fixture
def sample_form():
    return FormInput(
        business_name="TestCo",
        idea="AI-powered analytics platform",
        industry="SaaS",
        products_services=["Analytics dashboard", "API access"],
        target_customers="Small businesses",
        geography="US",
        pricing="$49/month",
        business_model="Subscription",
        competitors=["CompetitorA", "CompetitorB"],
        differentiators="AI-first approach",
        research_goals=["competitor_pricing"],
        user_query="How should I price?",
    )


@pytest.mark.asyncio
async def test_parse_success(parser, mock_llm, sample_form):
    """Test successful parsing of form input."""
    # Mock LLM responses
    mock_llm.send_query.side_effect = [
        "TestCo is an AI-powered analytics platform targeting small businesses in the US with a subscription model at $49/month.",
        '{"business_name": "TestCo", "idea": "AI analytics", "industry": "SaaS", "products_services": ["Analytics"], "target_customers": "Small businesses", "geography": "US", "pricing": "$49/month", "business_model": "Subscription", "competitors": ["A", "B"], "differentiators": "AI-first", "research_goals": ["competitor_pricing"], "user_query": "How should I price?"}',
    ]

    result = await parser.parse(sample_form)

    assert isinstance(result, BusinessProfile)
    assert result.business_name == "TestCo"
    assert result.industry == "SaaS"
    assert result.summary is not None


@pytest.mark.asyncio
async def test_parse_with_markdown_json(parser, mock_llm, sample_form):
    """Test parsing when LLM returns JSON in markdown block."""
    mock_llm.send_query.side_effect = [
        "Summary of TestCo...",
        '```json\n{"business_name": "TestCo", "idea": "AI analytics", "industry": "SaaS", "products_services": [], "target_customers": "", "geography": "", "pricing": "", "business_model": "", "competitors": [], "differentiators": "", "research_goals": [], "user_query": ""}\n```',
    ]

    result = await parser.parse(sample_form)
    assert result.business_name == "TestCo"


@pytest.mark.asyncio
async def test_parse_invalid_json_raises(parser, mock_llm, sample_form):
    """Test that invalid JSON raises ParsingValidationError."""
    mock_llm.send_query.side_effect = [
        "Summary...",
        "This is not valid JSON at all",
    ]

    with pytest.raises(ParsingValidationError):
        await parser.parse(sample_form)


@pytest.mark.asyncio
async def test_parse_empty_summary_raises(parser, mock_llm, sample_form):
    """Test that empty summary raises error."""
    mock_llm.send_query.return_value = ""

    with pytest.raises(ParsingValidationError):
        await parser.parse(sample_form)


def test_extract_json_raw(parser):
    """Test JSON extraction from raw text."""
    text = '{"key": "value"}'
    assert parser._extract_json(text) == '{"key": "value"}'


def test_extract_json_markdown(parser):
    """Test JSON extraction from markdown block."""
    text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
    assert parser._extract_json(text) == '{"key": "value"}'


def test_extract_json_plain_markdown(parser):
    """Test JSON extraction from plain markdown block."""
    text = '```\n{"key": "value"}\n```'
    assert parser._extract_json(text) == '{"key": "value"}'
