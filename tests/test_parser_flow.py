"""Tests for the parser-driven orchestrator flow (PARSER.md)."""
import pytest
from unittest.mock import AsyncMock

from app.orchestrator import Orchestrator
from app.schemas.analysis import ParserInput
from app.schemas.business import FormInput
from app.schemas.output import (
    AnalysisResult,
    ChatResponse,
    ParserOutput,
)
from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import WebHunterClient, WebHunterError


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
        },
        {
            "name": "CompB",
            "description": "Growing fast",
            "strengths": ["Innovation"],
            "weaknesses": ["Small team"],
            "pricing": "$79",
            "market_position": "Challenger",
            "source": "research",
            "explanation": "Rising rival",
        },
        {
            "name": "CompC",
            "description": "Niche player",
            "strengths": ["Focus"],
            "weaknesses": ["Limited"],
            "pricing": "$49",
            "market_position": "Niche",
            "source": "research",
            "explanation": "Niche rival",
        },
        {
            "name": "CompD",
            "description": "Extra competitor",
            "strengths": ["X"],
            "weaknesses": ["Y"],
            "pricing": "$59",
            "market_position": "Emerging",
            "source": "research",
            "explanation": "Extra",
        },
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
        competitors=["CompA", "CompB", "CompC"],
        differentiators="AI-first",
        research_goals=["competitor_research"],
        user_query="How to compete?",
    )


def make_parser_input(
    intent: str = "bootstrap",
    form_input: FormInput | None = None,
    requested_count: int = 3,
    message: str = "",
    entities: dict | None = None,
    missing_information: list[str] | None = None,
    current_analysis: dict | None = None,
    session_id: str | None = None,
) -> ParserInput:
    return ParserInput(
        intent=intent,
        form_input=form_input,
        requested_count=requested_count,
        message=message,
        entities=entities or {},
        missing_information=missing_information or [],
        current_analysis=current_analysis,
        session_id=session_id,
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


# ── Bootstrap tests ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_parser_bootstrap_returns_parser_output():
    orch = make_orchestrator()
    result = await orch.execute(make_parser_input(form_input=make_form()))
    assert isinstance(result, ParserOutput)
    assert result.intent == "bootstrap"
    assert result.status in ("success", "partial")


@pytest.mark.asyncio
async def test_parser_bootstrap_caps_at_3_companies():
    """Dynamic data must never exceed 3 competitors."""
    orch = make_orchestrator(llm_response=FULL_LLM_RESPONSE)
    result = await orch.execute(
        make_parser_input(form_input=make_form(), requested_count=3)
    )
    assert len(result.data.competitors) <= 3
    assert result.result_counts.requested == 3
    assert result.result_counts.retrieved <= 3


@pytest.mark.asyncio
async def test_parser_bootstrap_respects_requested_count_of_2():
    """If user requests 2, return at most 2."""
    orch = make_orchestrator(llm_response=FULL_LLM_RESPONSE)
    result = await orch.execute(
        make_parser_input(form_input=make_form(), requested_count=2)
    )
    assert len(result.data.competitors) <= 2


@pytest.mark.asyncio
async def test_parser_bootstrap_respects_requested_count_of_1():
    """If user requests 1, return at most 1."""
    orch = make_orchestrator(llm_response=FULL_LLM_RESPONSE)
    result = await orch.execute(
        make_parser_input(form_input=make_form(), requested_count=1)
    )
    assert len(result.data.competitors) <= 1


@pytest.mark.asyncio
async def test_parser_bootstrap_returns_entity_statuses():
    """Each competitor should have a tracked status."""
    orch = make_orchestrator()
    result = await orch.execute(make_parser_input(form_input=make_form()))
    assert "competitors" in result.entity_statuses
    statuses = result.entity_statuses["competitors"]
    assert len(statuses) > 0
    for s in statuses:
        assert s.status in ("complete", "partial", "failed")


@pytest.mark.asyncio
async def test_parser_bootstrap_builds_context_update():
    """Context update should be compact and useful."""
    orch = make_orchestrator()
    result = await orch.execute(make_parser_input(form_input=make_form()))
    assert result.context_update is not None
    assert result.context_update.business["name"] == "TestCo"
    assert len(result.context_update.entities["competitors"]) <= 3
    assert len(result.context_update.keywords) <= 10


@pytest.mark.asyncio
async def test_parser_bootstrap_error_when_no_business_name():
    """Missing business name returns structured error."""
    orch = make_orchestrator()
    # Pass None form_input with entities that lack a business name.
    result = await orch.execute(
        make_parser_input(
            form_input=None,
            entities={"competitor_names": ["X"]},  # no business name
        )
    )
    assert result.status == "error"
    assert "Business name required" in result.error
    assert len(result.missing_data) > 0


@pytest.mark.asyncio
async def test_parser_bootstrap_retries_on_llmping_failure():
    """Bootstrap should retry up to 2 times before failing."""
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(
        side_effect=LLMPingError("LLM down")
    )
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(make_parser_input(form_input=make_form()))
    assert result.status == "error"
    # 1 initial + 2 retries = 3 calls
    assert llmping.chat.call_count == 3


@pytest.mark.asyncio
async def test_parser_bootstrap_continues_when_webhunter_fails():
    """WebHunter failure should not break the analysis."""
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=FULL_LLM_RESPONSE)
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(
        side_effect=WebHunterError("WebHunter down")
    )
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(make_parser_input(form_input=make_form()))
    assert result.status in ("success", "partial")
    assert len(result.data.competitors) > 0


@pytest.mark.asyncio
async def test_parser_bootstrap_reports_missing_data():
    """Missing information from parser should be reported."""
    orch = make_orchestrator()
    # Use missing fields that won't be filled by the mock response.
    result = await orch.execute(
        make_parser_input(
            form_input=make_form(),
            missing_information=["founding date", "employee count"],
        )
    )
    # Should have missing_data entries for unfilled fields.
    assert any(m.field for m in result.missing_data)


# ── Question / follow-up tests ──────────────────────────────
@pytest.mark.asyncio
async def test_parser_question_returns_answer():
    """Question intent should return a chat-style answer."""
    chat_response = {
        "needs_research": False,
        "research_plan": [],
        "wants_visualizations": False,
    }
    answer_response = {"answer": "CompA is strong because...", "sources": []}
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(side_effect=[chat_response, answer_response])
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        make_parser_input(
            intent="question",
            message="Tell me about CompA",
            current_analysis={"profile": {"business_name": "TestCo"}},
        )
    )
    assert isinstance(result, ParserOutput)
    assert result.intent == "question"
    assert result.data.business_summary == "CompA is strong because..."


# ── Failure isolation tests ─────────────────────────────────
@pytest.mark.asyncio
async def test_parser_failure_isolation_preserves_valid_data():
    """One failed operation should not invalidate successful data."""
    # LLMPing returns partial data (missing some fields).
    partial_response = dict(FULL_LLM_RESPONSE)
    partial_response["competitors"] = [
        {
            "name": "GoodComp",
            "description": "Has description",
            "strengths": ["Brand"],
            "weaknesses": ["Price"],
            "pricing": "$99",
            "market_position": "Leader",
            "source": "research",
            "explanation": "Rival",
        },
        {
            "name": "",  # Invalid - missing name
            "description": "",
            "strengths": [],
            "weaknesses": [],
            "pricing": "",
            "market_position": "Invalid",
            "source": "",
            "explanation": "",
        },
    ]
    orch = make_orchestrator(llm_response=partial_response)
    result = await orch.execute(make_parser_input(form_input=make_form()))
    # Should still return the valid competitor.
    assert len(result.data.competitors) >= 1
    assert result.data.competitors[0].name == "GoodComp"


@pytest.mark.asyncio
async def test_parser_no_hallucinated_filler():
    """Missing results should never be fabricated to meet the limit."""
    # LLMPing returns only 1 competitor even though 3 requested.
    sparse_response = dict(FULL_LLM_RESPONSE)
    sparse_response["competitors"] = [
        {
            "name": "OnlyOne",
            "description": "Solo competitor",
            "strengths": ["X"],
            "weaknesses": ["Y"],
            "pricing": "$50",
            "market_position": "Leader",
            "source": "research",
            "explanation": "Only one found",
        }
    ]
    orch = make_orchestrator(llm_response=sparse_response)
    result = await orch.execute(
        make_parser_input(form_input=make_form(), requested_count=3)
    )
    # Should return 1, not fabricate 2 more.
    assert len(result.data.competitors) == 1
    assert result.result_counts.retrieved == 1
    assert result.result_counts.valid == 1


# ── Form building from parser entities ──────────────────────
@pytest.mark.asyncio
async def test_parser_builds_form_from_entities():
    """When no form_input, orchestrator builds it from entities."""
    orch = make_orchestrator()
    parser_input = ParserInput(
        intent="bootstrap",
        entities={
            "business_names": ["EcoSneakers"],
            "industries": ["sustainable fashion"],
            "competitor_names": ["Allbirds", "Veja"],
            "geographic_mentions": ["Europe"],
            "product_names": ["Sneakers"],
        },
        facts={
            "product_type": "sneakers",
            "target_market": "eco-conscious millennials",
        },
        requested_count=2,
    )
    result = await orch.execute(parser_input)
    assert isinstance(result, ParserOutput)
    assert result.data.profile is not None
    assert result.data.profile.business_name == "EcoSneakers"


# ── Partial results tests ───────────────────────────────────
@pytest.mark.asyncio
async def test_parser_partial_status_when_some_data_missing():
    """Partial data should yield 'partial' status, not 'error'."""
    partial_response = dict(FULL_LLM_RESPONSE)
    partial_response["swot"] = {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }
    orch = make_orchestrator(llm_response=partial_response)
    result = await orch.execute(make_parser_input(form_input=make_form()))
    # Should be partial (not error) since competitors exist.
    assert result.status in ("partial", "success")


# ── Compare intent tests ────────────────────────────────────
@pytest.mark.asyncio
async def test_parser_compare_returns_comparison():
    """Compare intent should return comparison data."""
    chat_response = {
        "needs_research": False,
        "research_plan": [],
        "wants_visualizations": False,
    }
    answer_response = {"answer": "Here is the comparison...", "sources": []}
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(side_effect=[chat_response, answer_response])
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        make_parser_input(
            intent="compare",
            message="Compare CompA and CompB",
        )
    )
    assert isinstance(result, ParserOutput)
    assert result.intent == "compare"
    assert result.data.business_summary == "Here is the comparison..."


# ── Explain intent tests ────────────────────────────────────
@pytest.mark.asyncio
async def test_parser_explain_returns_explanation():
    """Explain intent should return an explanation."""
    chat_response = {
        "needs_research": False,
        "research_plan": [],
        "wants_visualizations": False,
    }
    answer_response = {"answer": "This matters because...", "sources": []}
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(side_effect=[chat_response, answer_response])
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        make_parser_input(
            intent="explain",
            message="Why is CompA a leader?",
        )
    )
    assert isinstance(result, ParserOutput)
    assert result.intent == "explain"
    assert result.data.business_summary == "This matters because..."


# ── Regenerate intent tests ─────────────────────────────────
@pytest.mark.asyncio
async def test_parser_regenerate_reruns_bootstrap():
    """Regenerate intent should re-run the full analysis."""
    orch = make_orchestrator()
    result = await orch.execute(
        make_parser_input(intent="regenerate", form_input=make_form())
    )
    assert isinstance(result, ParserOutput)
    assert result.intent == "regenerate"
    assert len(result.data.competitors) > 0
