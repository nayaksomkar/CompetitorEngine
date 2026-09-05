import pytest
from unittest.mock import AsyncMock

from app.orchestrator import Orchestrator
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult, ChatResponse
from app.services.llmping_client import LLMPingClient
from app.services.webhunter_client import WebHunterClient


FULL_LLM_RESPONSE = {
    "business_summary": "TestCo is an AI analytics platform.",
    "executive_summary": "Strong opportunity.",
    "market_info": {"size": "$5B"},
    "positioning": "AI-first",
    "gaps": ["No mobile"],
    "opportunities": ["EU expansion"],
    "risks": ["Big competitors"],
    "competitors": [
        {
            "name": "CompA",
            "description": "Established",
            "strengths": ["Brand"],
            "weaknesses": ["Price"],
            "pricing": "$99",
            "market_position": "Leader",
            "source": "research",
            "explanation": "Rival",
        }
    ],
    "swot": {
        "strengths": [{"point": "AI-first", "explanation": "x", "source": "y"}],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    },
    "comparisons": [],
    "visualizations": [
        {
            "chart_type": "bar",
            "title": "Pricing",
            "labels": ["A", "B"],
            "datasets": [{"label": "$", "data": [1, 2]}],
            "explanation": "x",
        }
    ],
    "metric_cards": [{"label": "MRR", "value": 1000.0}],
    "recommendations": [
        {
            "title": "Do X",
            "description": "...",
            "priority": "high",
            "rationale": "...",
            "explanation": "...",
        }
    ],
    "action_plan": [
        {
            "action": "Ship X",
            "timeline": "Month 1",
            "priority": "high",
            "expected_outcome": "...",
            "explanation": "...",
        }
    ],
    "report": "report",
    "sources": ["https://example.com/a"],
}


def make_form() -> FormInput:
    return FormInput(
        business_name="TestCo",
        idea="AI analytics",
        industry="SaaS",
        products_services=["Dashboard"],
        target_customers="SMBs",
        geography="US",
        pricing="$49",
        business_model="Subscription",
        competitors=["CompA"],
        differentiators="AI-first",
        research_goals=["competitor_research"],
        user_query="How to compete?",
    )


def make_orchestrator(
    llm_response=FULL_LLM_RESPONSE,
    research_response=None,
    chat_response=None,
) -> Orchestrator:
    llmping = AsyncMock(spec=LLMPingClient)
    if chat_response is not None:
        llmping.chat = AsyncMock(side_effect=[chat_response, llm_response])
    else:
        llmping.chat = AsyncMock(return_value=llm_response)

    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(
        return_value=research_response
        or {"competitor_research": {"sources": ["https://example.com/raw"]}}
    )

    return Orchestrator(llmping=llmping, webhunter=webhunter)


@pytest.mark.asyncio
async def test_overview_calls_webhunter_then_llmping():
    orch = make_orchestrator()
    result = await orch.run_overview(make_form())
    assert isinstance(result, AnalysisResult)
    assert result.profile.business_name == "TestCo"
    assert len(result.competitors) == 1
    assert result.metadata.processing_time_ms >= 0


@pytest.mark.asyncio
async def test_overview_continues_when_webhunter_fails():
    from app.services.webhunter_client import WebHunterError

    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=FULL_LLM_RESPONSE)
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(
        side_effect=WebHunterError("WebHunter down")
    )
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)
    # Must not raise — analysis should still complete using LLMPing
    # alone.
    result = await orch.run_overview(make_form())
    assert isinstance(result, AnalysisResult)


@pytest.mark.asyncio
async def test_overview_raises_when_llmping_missing_required_keys():
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value={"business_summary": "x"})
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)
    with pytest.raises(Exception):
        await orch.run_overview(make_form())


@pytest.mark.asyncio
async def test_overview_drops_charts_with_empty_data():
    bad = dict(FULL_LLM_RESPONSE)
    bad["visualizations"] = [
        {"chart_type": "bar", "title": "Empty", "labels": [], "datasets": [], "explanation": ""},
        FULL_LLM_RESPONSE["visualizations"][0],
    ]
    orch = make_orchestrator(llm_response=bad)
    result = await orch.run_overview(make_form())
    assert len(result.charts) == 1
    assert result.charts[0].title == "Pricing"


@pytest.mark.asyncio
async def test_overview_drops_metric_cards_without_numeric_value():
    bad = dict(FULL_LLM_RESPONSE)
    bad["metric_cards"] = [
        {"label": "Broken", "value": None},
        FULL_LLM_RESPONSE["metric_cards"][0],
    ]
    orch = make_orchestrator(llm_response=bad)
    result = await orch.run_overview(make_form())
    assert len(result.metric_cards) == 1


@pytest.mark.asyncio
async def test_chat_calls_webhunter_when_decision_says_yes():
    chat_response = {
        "needs_research": True,
        "research_plan": ["competitor_research"],
        "wants_visualizations": False,
    }
    orch = make_orchestrator(
        chat_response=chat_response,
        llm_response={"answer": "CompA is strong", "sources": []},
    )
    result = await orch.chat(
        session_id="sid",
        message="Tell me about CompA",
        current_analysis={"profile": {"business_name": "TestCo"}},
    )
    assert isinstance(result, ChatResponse)
    assert result.session_id == "sid"
    assert result.answer == "CompA is strong"
    orch.webhunter.research.assert_awaited()


@pytest.mark.asyncio
async def test_chat_skips_webhunter_when_decision_says_no():
    chat_response = {
        "needs_research": False,
        "research_plan": [],
        "wants_visualizations": False,
    }
    orch = make_orchestrator(
        chat_response=chat_response,
        llm_response={"answer": "Based on existing context"},
    )
    result = await orch.chat(
        session_id="sid",
        message="Summarize",
        current_analysis={"profile": {"business_name": "TestCo"}},
    )
    assert result.answer == "Based on existing context"
    orch.webhunter.research.assert_not_called()


@pytest.mark.asyncio
async def test_chat_generates_session_id_if_missing():
    orch = make_orchestrator(
        chat_response={"needs_research": False, "research_plan": []},
        llm_response={"answer": "ok"},
    )
    result = await orch.chat(
        session_id=None,
        message="hi",
        current_analysis=None,
    )
    assert result.session_id  # auto-generated
