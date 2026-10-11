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
from fastapi import FastAPI, HTTPException

from app.services.llmping_client import (
    LLMPingClient,
    LLMPingError,
    _adapt_response,
)
from app.services.webhunter_client import (
    WebHunterClient,
    WebHunterError,
)


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
        captured.setdefault("payloads", []).append(payload)
        return {
            "status": "completed",
            "query": payload.get("query", ""),
            "search_queries": [payload.get("query", "")],
            "search_results": [
                {
                    "sub_query": payload.get("query", ""),
                    "url": "https://example.com/a",
                    "title": "Fragrance competitor product and pricing plans",
                    "snippet": (
                        "Scentra fragrance competitors offer perfume products, "
                        "features, and published pricing plans."
                    ),
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
    captured: dict[str, Any] = {"payloads": []}
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
    assert "business_summary" not in reply
    assert "executive_summary" not in reply


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

    reply = await llmp.chat({
        "task": "full_analysis",
        "session_id": "s1",
        "context": {
            "business": {
                "business_name": "Scentra",
                "industry": "Fragrance",
                "competitors": ["Forest Essentials", "Kama Ayurveda"],
            },
            "research": {
                "results": {
                    "competitor_research": {
                        "sources": [{
                            "url": "https://evidence.example/fragrance",
                            "title": "Indian fragrance competitors",
                            "snippet": "Published evidence on fragrance brands.",
                        }],
                    },
                },
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
    assert "https://evidence.example/fragrance" in payload["query"]
    assert "Published evidence" in payload["query"]
    assert "JSON object only" in payload["query"]
    assert "Do not invent" in payload["query"]
    assert reply["_structured_output_error"] is True
    assert "report" not in reply
    assert "executive_summary" not in reply


def test_full_analysis_adapter_accepts_structured_json_and_rejects_markdown():
    prose = _adapt_response(
        {
            "answer": "# Executive Summary\nA long narrative report.",
            "provider": "test",
            "model": "test-model",
        },
        task="full_analysis",
    )
    assert prose["_structured_output_error"] is True
    assert "answer" not in prose
    assert "executive_summary" not in prose
    assert "report" not in prose

    structured = _adapt_response(
        {
            "answer": (
                '```json\n{"executive_summary":"Evidence-backed summary",'
                '"competitors":[]}\n```'
            ),
            "provider": "test",
            "model": "test-model",
        },
        task="full_analysis",
    )
    assert structured["executive_summary"] == "Evidence-backed summary"
    assert structured["competitors"] == []
    assert structured["_evidence_required"] is True
    assert "answer" not in structured


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
            "idea": "A curated premium perfume line with a scent quiz.",
            "industry": "Fragrance",
            "products_services": ["EDP", "Discovery sets"],
            "target_customers": "Urban fragrance buyers",
            "geography": "India",
            "pricing": "₹3500",
            "competitors": ["Zoho Creator", "ClickUp", "Fieldproxy"],
            "research_goals": ["Compare independently discovered options."],
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
    assert all(name in payload["query"] for name in [
        "Zoho Creator", "ClickUp", "Fieldproxy",
    ])
    assert "Compare independently discovered options" in payload["query"]
    assert "A curated premium perfume line" in payload["query"]
    assert "products/services: EDP, Discovery sets" in payload["query"]
    assert "target customers: Urban fragrance buyers" in payload["query"]
    assert "geography: India" in payload["query"]
    q_lower = payload["query"].lower()
    assert "competitor" in q_lower or "pricing" in q_lower

    # Adapter returns the orchestrator-friendly shape.
    assert "results" in results
    assert "competitor_research" in results["results"]
    assert "pricing_research" in results["results"]
    for topic in ("competitor_research", "pricing_research"):
        assert results["batch_statuses"][topic]["status"] == "completed"
        assert results["results"][topic]["sources"][0]["url"] == (
            "https://example.com/a"
        )
    assert len(captured["payloads"]) == 2
    assert {payload["max_pages"] for payload in captured["payloads"]} == {2}


@pytest.mark.asyncio
async def test_webhunter_filters_unrelated_and_explicit_results_and_retries_variant():
    queries: list[str] = []
    app = FastAPI()

    @app.post("/research/sync")
    async def research_sync(payload: dict):
        queries.append(payload["query"])
        if len(queries) == 1:
            return {
                "status": "completed",
                "search_results": [
                    {
                        "url": "https://adult.example/xxx-fleet",
                        "title": "Fleet management xxx video",
                        "snippet": "Adult video results.",
                    },
                    {
                        "url": "https://en.wikipedia.org/wiki/India",
                        "title": "India - Wikipedia",
                        "snippet": "Geography and history of India.",
                    },
                ],
                "crawled_contents": [],
            }
        url = "https://www.fleetfox.example/product?utm_source=search"
        return {
            "status": "completed",
            "search_results": [{
                "url": url,
                "title": "FleetFox competitor fleet management software",
                "snippet": (
                    "FleetFox coordinates logistics deliveries and routes; "
                    "compare competitors and alternatives."
                ),
            }],
            "crawled_contents": [{
                "url": "https://fleetfox.example/product",
                "title": "FleetFox fleet management software",
                "content": (
                    "FleetFox software supports fleet routing, logistics "
                    "deliveries, shipment tracking, and driver management."
                ),
            }],
        }

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fake",
    )
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client
    result = await wh.research(
        business={
            "business_name": "FleetFox",
            "idea": "Fleet management software for small logistics operators.",
            "industry": "Logistics technology",
            "products_services": ["fleet routing", "shipment tracking"],
            "target_customers": "Small logistics companies in India",
            "geography": "India",
            "research_goals": ["Discover fleet management competitors."],
        },
        research_types=["competitor_research"],
    )
    await client.aclose()

    batch = result["batch_statuses"]["competitor_research"]
    assert len(queries) == 2
    assert queries[0] != queries[1]
    assert "FleetFox" in queries[0]
    assert "Logistics technology" in queries[0]
    assert "shipment tracking" in queries[0]
    assert "Small logistics companies in India" in queries[0]
    assert batch["status"] == "completed"
    assert batch["attempts"][0]["rejected"] == 2
    assert batch["attempts"][0]["http_status"] == 200
    assert batch["attempts"][0]["duration_ms"] >= 0
    assert batch["rejected_reasons"] == {
        "adult_or_explicit_content": 1,
        "no_business_context_match": 1,
    }
    assert batch["accepted"] == 1
    accepted = result["results"]["competitor_research"]
    assert [source["url"] for source in accepted["sources"]] == [
        "https://www.fleetfox.example/product?utm_source=search",
    ]
    assert len(accepted["crawled"]) == 1
    assert result["raw"]["search_results"][0]["url"].startswith(
        "https://www.fleetfox.example/"
    )


@pytest.mark.asyncio
async def test_webhunter_preserves_successful_batch_and_deduplicates_urls():
    active = 0
    peak_active = 0
    app = FastAPI()

    @app.post("/research/sync")
    async def research_sync(payload: dict):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.03)
            if "published plans" in payload["query"].lower():
                return {
                    "status": "failed",
                    "error": "search provider unavailable",
                    "errors": [],
                }
            return {
                "status": "completed",
                "search_results": [{
                    "url": "https://fleetfox.example/company",
                    "title": "FleetFox logistics fleet competitor platform",
                    "snippet": (
                        "FleetFox supports logistics fleet routing and is a "
                        "competitor alternative."
                    ),
                }],
                "crawled_contents": [],
            }
        finally:
            active -= 1

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fake",
    )
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client
    result = await wh.research(
        business={
            "business_name": "FleetFox",
            "industry": "Fleet logistics",
            "idea": "Fleet management software",
            "products_services": ["shipment tracking"],
        },
        research_types=["competitor_research", "pricing_research"],
    )
    await client.aclose()

    assert peak_active == 2
    assert result["batch_statuses"]["competitor_research"]["status"] == "completed"
    assert result["batch_statuses"]["pricing_research"]["status"] == "failed"
    assert len(result["results"]["competitor_research"]["sources"]) == 1
    assert result["results"]["pricing_research"]["sources"] == []
    assert len(result["raw"]["search_results"]) == 1


@pytest.mark.asyncio
async def test_webhunter_bounds_concurrency_and_batch_duration(monkeypatch):
    import app.services.webhunter_client as webhunter_module

    active = 0
    peak_active = 0
    app = FastAPI()

    @app.post("/research/sync")
    async def research_sync(payload: dict):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.03)
            return {
                "status": "completed",
                "search_results": [{
                    "url": f"https://fleetfox.example/{active}",
                    "title": "FleetFox logistics fleet software official product page",
                    "snippet": (
                        "FleetFox is a competitor with product features and "
                        "published pricing plans. Customers review workflow needs; "
                        "market trends, gaps, threats, opportunities, strengths, "
                        "weaknesses, and vendor information."
                    ),
                }],
                "crawled_contents": [],
            }
        finally:
            active -= 1

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fake",
    )
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client
    result = await wh.research(
        business={
            "business_name": "FleetFox",
            "industry": "Fleet logistics",
            "idea": "Fleet management software",
        },
        research_types=[
            "competitor_research",
            "product_research",
            "pricing_research",
            "customer_needs",
            "market_research",
        ],
    )
    await client.aclose()
    assert peak_active <= 4
    assert peak_active == 4
    assert all(
        batch["status"] == "completed"
        for batch in result["batch_statuses"].values()
    )

    monkeypatch.setattr(webhunter_module, "_RESEARCH_BATCH_TIMEOUT_S", 0.01)
    slow_app = FastAPI()

    @slow_app.post("/research/sync")
    async def slow_research(payload: dict):
        await asyncio.sleep(0.1)
        return {"status": "completed", "search_results": [], "crawled_contents": []}

    slow_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=slow_app),
        base_url="http://fake",
    )
    slow_wh = WebHunterClient(base_url="http://fake")
    slow_wh._client = slow_client
    timed = await slow_wh.research(
        business={"business_name": "FleetFox", "industry": "Logistics"},
        research_types=["competitor_research"],
    )
    await slow_client.aclose()
    assert timed["batch_statuses"]["competitor_research"]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_webhunter_rejects_malformed_completed_payload():
    app = FastAPI()

    @app.post("/research/sync")
    async def research_sync(payload: dict):
        return {"status": "completed", "search_results": {}, "crawled_contents": []}

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fake",
    )
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client
    result = await wh.research(
        business={"business_name": "FleetFox", "industry": "Logistics"},
        research_types=["competitor_research"],
    )
    await client.aclose()

    batch = result["batch_statuses"]["competitor_research"]
    assert batch["status"] == "failed"
    assert batch["accepted"] == 0
    assert "malformed result lists" in batch["error"]


@pytest.mark.asyncio
async def test_webhunter_bootstrap_retries_reported_failure_and_keeps_batch_status():
    """Transient failures are retried with a changed query and exposed by batch."""
    attempts = 0
    queries = []

    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        nonlocal attempts
        attempts += 1
        queries.append(payload["query"])
        return {"status": "failed", "error": "upstream timeout", "errors": []}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    wh = WebHunterClient(base_url="http://fake")
    wh._client = client

    result = await wh.research(
        business={"business_name": "x", "industry": "y"},
        research_types=["competitor_research"],
    )
    await client.aclose()
    assert attempts == 2
    assert queries[0] != queries[1]
    assert result["batch_statuses"]["competitor_research"]["status"] == "failed"
    assert result["batch_statuses"]["competitor_research"]["error"] == (
        "provider reported research failure"
    )
    assert all(attempt["http_status"] == 200 for attempt in result[
        "batch_statuses"
    ]["competitor_research"]["attempts"])


# ── §5.1 LLMPing extraction & explain adapters ──
def _extract_app(captured: dict):
    app = FastAPI()

    @app.post("/chat")
    async def chat(payload: dict):
        captured["payload"] = payload
        return {
            "answer": (
                '{"name": "Fragante", '
                '"description": "Mid-range Indian fragrance.", '
                '"pricingTier": "Mid-range", '
                '"marketPosition": "Emerging", '
                '"confidence": 78, "sourceCount": 5}'
            )
        }

    return app


@pytest.mark.asyncio
async def test_llmping_extract_profile_wire_and_parse():
    captured: dict[str, Any] = {}
    transport = httpx.ASGITransport(app=_extract_app(captured))
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    profile = await llmp.extract_profile(
        "Fragante",
        "Fragrance",
        [
            {
                "url": "https://example.com/a",
                "title": "A",
                "snippet": "s",
            }
        ],
        session_id="s1",
    )
    await client.aclose()

    # Wire: task-scoped query carrying company, industry,
    # sources and session id.
    assert captured["payload"]["session_id"] == "s1"
    assert "Fragante" in captured["payload"]["query"]
    assert "Fragrance" in captured["payload"]["query"]
    assert "https://example.com/a" in captured["payload"]["query"]
    # Parsing: JSON extracted from the answer text.
    assert profile["name"] == "Fragante"
    assert profile["pricingTier"] == "Mid-range"
    assert profile["confidence"] == 78
    assert profile["sourceCount"] == 5


@pytest.mark.asyncio
async def test_llmping_extract_profile_raises_on_garbage():
    app = FastAPI()

    @app.post("/chat")
    async def chat(payload: dict):
        return {"answer": "no json here at all"}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client
    with pytest.raises(LLMPingError):
        await llmp.extract_profile("Fragante", "Fragrance", [])
    await client.aclose()


@pytest.mark.asyncio
async def test_llmping_explain_wire_and_parse():
    captured: dict[str, Any] = {}
    app = FastAPI()

    @app.post("/chat")
    async def chat(payload: dict):
        captured["payload"] = payload
        return {
            "answer": (
                '{"explanation": "Because reasons.", '
                '"evidence": [{"label": "Share", '
                '"detail": "40%"}]}'
            )
        }

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    result = await llmp.explain(
        "Why is CompA a leader?",
        {"competitors": [{"name": "CompA"}]},
        session_id="s2",
    )
    await client.aclose()

    assert captured["payload"]["session_id"] == "s2"
    assert "Why is CompA a leader?" in captured["payload"]["query"]
    assert result["explanation"] == "Because reasons."
    assert result["evidence"][0]["label"] == "Share"


@pytest.mark.asyncio
async def test_llmping_compare_wire():
    captured: dict[str, Any] = {}
    app = FastAPI()

    @app.post("/chat")
    async def chat(payload: dict):
        captured["payload"] = payload
        return {"answer": "A is stronger than B."}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    llmp = LLMPingClient(base_url="http://fake-upstream")
    llmp._client = client

    summary = await llmp.compare(
        [
            {
                "id": "a",
                "name": "CompA",
                "source": "context",
                "profile": {"description": "Established"},
            },
            {
                "id": "b",
                "name": "CompB",
                "source": "web",
                "profile": {"description": "Growing"},
            },
        ],
        session_id="s3",
    )
    await client.aclose()

    assert captured["payload"]["session_id"] == "s3"
    assert "CompA" in captured["payload"]["query"]
    assert "CompB" in captured["payload"]["query"]
    assert summary == "A is stronger than B."


# ── §5.1 per-company WebHunter search ───────────
@pytest.mark.asyncio
async def test_webhunter_search_company_wire_and_dedupe():
    captured: dict[str, Any] = {}
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        captured["payload"] = payload
        return {
            "status": "completed",
            "search_results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "snippet": "s1",
                },
                # Same domain as the first — must be deduped.
                {
                    "url": "https://example.com/b",
                    "title": "B",
                    "snippet": "s2",
                },
                {
                    "url": "https://other.org/c",
                    "title": "C",
                    "snippet": "s3",
                },
            ],
        }

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client

    sources = await wh.search_company("Fragante", "Fragrance")
    await client.aclose()

    # Wire: single query carrying company + industry.
    assert "Fragante" in captured["payload"]["query"]
    assert "Fragrance" in captured["payload"]["query"]
    # Deduped by domain, top results kept.
    assert len(sources) == 2
    assert sources[0]["url"] == "https://example.com/a"
    assert sources[1]["url"] == "https://other.org/c"


@pytest.mark.asyncio
async def test_webhunter_search_company_raises_on_error():
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        raise HTTPException(status_code=500)

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client
    with pytest.raises(WebHunterError):
        await wh.search_company("Fragante", "Fragrance")
    await client.aclose()


@pytest.mark.asyncio
async def test_webhunter_search_company_sends_bounding_params():
    """The lookup request must carry the documented bounding
    parameters (max_pages, timeout_ms) so WebHunter cannot crawl
    unboundedly on thin-result entities."""
    captured: dict[str, Any] = {}
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        captured["payload"] = payload
        return {"status": "completed", "search_results": []}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client
    await wh.search_company("Fragante", "Fragrance")
    await client.aclose()
    assert captured["payload"]["max_pages"] == 5
    assert captured["payload"]["timeout_ms"] == 30000


@pytest.mark.asyncio
async def test_webhunter_search_company_relevance_first():
    """Sources mentioning the requested company come first; the
    rest are preserved after them (partially relevant evidence
    is kept, not dropped)."""
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        return {
            "status": "completed",
            "search_results": [
                {
                    "url": "https://unrelated.com/x",
                    "title": "Something else",
                    "snippet": "not about the company",
                },
                {
                    "url": "https://a.com/fragante-news",
                    "title": "Fragante launches new line",
                    "snippet": "coverage of Fragante",
                },
                {
                    "url": "https://b.com/fragante-review",
                    "title": "Review site",
                    "snippet": "Fragante pricing analysis",
                },
            ],
        }

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client
    sources = await wh.search_company("Fragante", "Fragrance")
    await client.aclose()

    assert len(sources) == 3
    assert sources[0]["url"] == "https://a.com/fragante-news"
    assert sources[1]["url"] == "https://b.com/fragante-review"
    assert sources[2]["url"] == "https://unrelated.com/x"


@pytest.mark.asyncio
async def test_webhunter_search_company_bounds_zero_match_junk():
    """When NO result mentions the company, only a small head
    sample is passed downstream — WebHunter provably returns
    unrelated results on name collisions, and junk must not be
    treated as evidence."""
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        return {
            "status": "completed",
            "search_results": [
                {"url": f"https://junk{i}.com/x", "title": f"J{i}",
                 "snippet": "unrelated"}
                for i in range(6)
            ],
        }

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client
    sources = await wh.search_company("Fragante", "Fragrance")
    await client.aclose()
    assert len(sources) == 2


@pytest.mark.asyncio
async def test_webhunter_search_company_raises_on_upstream_failed():
    """An upstream-reported failure must raise (so the
    orchestrator's retry policy applies), not return []."""
    app = FastAPI()

    @app.post("/research/sync")
    async def sync(payload: dict):
        return {
            "status": "failed",
            "search_results": [],
            "error": "no search results returned",
        }

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fake-upstream"
    )
    wh = WebHunterClient(base_url="http://fake-upstream")
    wh._client = client
    with pytest.raises(WebHunterError, match="no search results"):
        await wh.search_company("Fragante", "Fragrance")
    await client.aclose()
