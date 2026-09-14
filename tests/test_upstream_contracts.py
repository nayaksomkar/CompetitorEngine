"""Contract tests for the orchestrator's wire-level integration with
LLMPing and WebHunter.

These run a small in-process FastAPI fake for each upstream, send a
real request through the production client, and assert that the
payload hit the wire in the shape the upstream expects. The point
is to lock the contract: if anyone changes the client's payload
shape, these tests fail before the container blackholes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.services.llmping_client import LLMPingClient
from app.services.webhunter_client import WebHunterClient


# ── Fake LLMPing ────────────────────────────────────────────
def _make_llmping_fake(captured: dict[str, Any]) -> FastAPI:
    app = FastAPI()

    @app.post("/chat")
    async def chat(payload: dict):
        captured["payload"] = payload
        return {
            "answer": "Scentra is a mid-range DTC fragrance brand.",
            "provider": "test",
            "model": "test-model",
        }

    return app


# ── Fake WebHunter ───────────────────────────────────────────
def _make_webhunter_fake(captured: dict[str, Any]) -> FastAPI:
    app = FastAPI()

    @app.post("/research/sync")
    async def research_sync(payload: dict):
        captured["payload"] = payload
        return {
            "status": "completed",
            "query": payload.get("query", ""),
            "search_queries": [payload.get("query", "")],
            "search_results": [
                {
                    "sub_query": payload.get("query", ""),
                    "url": "https://example.com/a",
                    "title": "Top competitors",
                    "snippet": "Competitors include X, Y, Z.",
                }
            ],
            "crawled_contents": [],
            "stats": {"search_results_count": 1, "crawled_pages_count": 0,
                      "elapsed_ms": 12},
            "errors": [],
            "error": None,
        }

    return app


def _start_fake(app: FastAPI) -> tuple[httpx.ASGITransport, str]:
    """Start a fake FastAPI server in-process; return (transport, url)."""
    transport = httpx.ASGITransport(app=app)
    url = "http://fake-upstream"
    return transport, url


# ── LLMPing contract ────────────────────────────────────────
@pytest.mark.asyncio
async def test_llmping_wire_payload_has_query_and_session_id():
    captured: dict[str, Any] = {}
    fake = _make_llmping_fake(captured)
    transport, _ = _start_fake(fake)

    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://fake-upstream",
    )
    # Use the production LLMPingClient with explicit URL and a fake
    # transport so the request never leaves the process.
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    # Inside the production chat() call, _get_client() returns the
    # already-set client (and skips creating a new one).
    reply = await llmp.chat({
        "task": "answer_question",
        "session_id": "abc-123",
        "message": "Tell me about Scentra",
    })

    await client.aclose()

    assert captured["payload"] == {
        "query": "Tell me about Scentra",
        "session_id": "abc-123",
    }, f"unexpected wire payload: {captured['payload']!r}"
    assert reply["answer"] == "Scentra is a mid-range DTC fragrance brand."
    # Response adapter fills in the keys the orchestrator reads.
    assert reply["business_summary"].startswith("Scentra")
    assert reply["executive_summary"].startswith("Scentra")


@pytest.mark.asyncio
async def test_llmping_full_analysis_builds_query_from_business():
    """When there is no user message (full_analysis task), the wire
    `query` field must still be populated from the business profile."""
    captured: dict[str, Any] = {}
    fake = _make_llmping_fake(captured)
    transport, _ = _start_fake(fake)

    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    await llmp.chat({
        "task": "full_analysis",
        "session_id": "s1",
        "context": {
            "business": {
                "business_name": "Scentra",
                "industry": "Fragrance",
                "competitors": ["Forest Essentials", "Kama Ayurveda"],
            },
        },
    })

    await client.aclose()

    payload = captured["payload"]
    assert "query" in payload
    assert payload["session_id"] == "s1"
    # Query mentions Scentra and at least one competitor.
    assert "Scentra" in payload["query"]
    assert "Forest Essentials" in payload["query"] or "competitor" in payload["query"].lower()


@pytest.mark.asyncio
async def test_llmping_decide_research_need_includes_context():
    captured: dict[str, Any] = {}
    fake = _make_llmping_fake(captured)
    transport, _ = _start_fake(fake)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    await llmp.chat({
        "task": "decide_research_need",
        "session_id": "s2",
        "message": "Compare X and Y?",
        "current_context": {"profile": {"name": "TestCo"}},
    })
    await client.aclose()

    q = captured["payload"]["query"]
    assert "Compare X and Y?" in q
    assert "TestCo" in q


# ── WebHunter contract ───────────────────────────────────────
@pytest.mark.asyncio
async def test_webhunter_wire_payload_has_query_and_max_results():
    captured: dict[str, Any] = {}
    fake = _make_webhunter_fake(captured)
    transport, _ = _start_fake(fake)

    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client

    results = await wh.research(
        business={
            "business_name": "Scentra",
            "industry": "Fragrance",
            "geography": "India",
            "pricing": "₹3500",
        },
        research_types=["competitor_research", "pricing_research"],
    )
    await client.aclose()

    payload = captured["payload"]
    # Wire-level keys the upstream expects.
    assert "query" in payload
    assert "max_results" in payload
    assert isinstance(payload["max_results"], int)
    assert payload["max_results"] > 0
    # Query mentions the brand + at least one topic.
    assert "Scentra" in payload["query"]
    q_lower = payload["query"].lower()
    assert "competitor" in q_lower or "pricing" in q_lower

    # Adapter returns the orchestrator-friendly shape.
    assert "results" in results
    assert "competitor_research" in results["results"]
    assert "pricing_research" in results["results"]
    assert results["results"]["competitor_research"]["sources"][0]["url"] == (
        "https://example.com/a"
    )


@pytest.mark.asyncio
async def test_webhunter_async_failure_does_not_raise():
    """When WebHunter returns status=failed, the client returns an
    empty result and does NOT raise — the orchestrator treats that
    as missing research and continues."""
    captured: dict[str, Any] = {}

    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        return {"status": "failed", "error": "upstream timeout", "errors": []}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client

    results = await wh.research(
        business={"business_name": "x", "industry": "y"},
        research_types=["competitor_research"],
    )
    await client.aclose()
    assert results["results"] == {}
