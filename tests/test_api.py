import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from app.schemas.business import FormInput


SAMPLE_LLMPING_RESPONSE = {
    "business_summary": "TestCo is an AI analytics platform.",
    "executive_summary": "Strong opportunity in SMB analytics.",
    "market_info": {"size": "$5B", "growth": "12%"},
    "positioning": "AI-first, affordable.",
    "gaps": ["No mobile app"],
    "opportunities": ["Expand to EU"],
    "risks": ["Big competitors entering"],
    "competitors": [
        {
            "name": "CompA",
            "description": "Established player",
            "strengths": ["Brand"],
            "weaknesses": ["Price"],
            "pricing": "$99/mo",
            "market_position": "Leader",
            "source": "research",
            "explanation": "Main rival",
        }
    ],
    "swot": {
        "strengths": [{"point": "AI-first", "explanation": "Unique", "source": "analysis"}],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    },
    "comparisons": [],
    "visualizations": [
        {
            "chart_type": "bar",
            "title": "Pricing",
            "labels": ["TestCo", "CompA"],
            "datasets": [{"label": "Price", "data": [49, 99]}],
            "explanation": "TestCo undercuts",
        }
    ],
    "metric_cards": [
        {"label": "Market size", "value": 5_000_000_000, "unit": "USD"}
    ],
    "recommendations": [
        {
            "title": "Launch free tier",
            "description": "Capture SMBs",
            "priority": "high",
            "rationale": "Lower CAC",
            "explanation": "SMBs want to try before buying",
        }
    ],
    "action_plan": [
        {
            "action": "Ship free tier",
            "timeline": "Month 1",
            "priority": "high",
            "expected_outcome": "3x signups",
            "explanation": "Removes friction",
        }
    ],
    "report": "# TestCo Analysis\n\nStrong opportunity.",
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
        pricing="$49/month",
        business_model="Subscription",
        competitors=["CompA"],
        differentiators="AI-first",
        research_goals=["competitor_research"],
        user_query="How to compete?",
    )


@pytest.fixture
def client():
    with patch("app.orchestrator.LLMPingClient") as LLM, \
         patch("app.orchestrator.WebHunterClient") as WH:
        llm_instance = AsyncMock()
        llm_instance.chat = AsyncMock(return_value=SAMPLE_LLMPING_RESPONSE)
        wh_instance = AsyncMock()
        wh_instance.research = AsyncMock(return_value={
            "competitor_research": {"sources": ["https://example.com/raw"]}
        })
        LLM.return_value = llm_instance
        WH.return_value = wh_instance
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "CompetitorEngine"
    assert "overview" in data["endpoints"]
    assert "chat" in data["endpoints"]


def test_analyze_endpoint_validation_error(client):
    resp = client.post("/api/v1/analyze", json={})
    assert resp.status_code == 422


def test_analyze_endpoint_success(client):
    resp = client.post("/api/v1/analyze", json=make_form().model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["business_name"] == "TestCo"
    assert len(data["competitors"]) >= 1
    assert len(data["charts"]) >= 1
    assert len(data["metric_cards"]) >= 1
    assert data["metadata"]["processing_time_ms"] >= 0


def test_chat_endpoint(client):
    resp = client.post(
        "/api/v1/chat",
        json={
            "session_id": "abc-123",
            "message": "Tell me more about CompA",
            "current_analysis": None,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "abc-123"


def test_parser_execute_endpoint(client):
    """The parser endpoint returns the full §6.1
    envelope: data + context_update + result_counts +
    entity_statuses + derived_data + ui_state."""
    resp = client.post(
        "/api/v1/parser/execute",
        json={
            "parser_input": {
                "intent": "bootstrap",
                "requested_count": 3,
                "form_input": {
                    "business_name": "TestCo",
                    "idea": "AI analytics",
                    "industry": "SaaS",
                },
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "bootstrap"
    assert data["status"] in ("success", "partial")
    assert data["result_counts"]["requested"] == 3
    assert "competitors" in data["entity_statuses"]
    assert data["context_update"] is not None
    assert data["context_update"]["business"]["name"] == "TestCo"
    assert "derived_data" in data
    assert "ui_state" in data


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
