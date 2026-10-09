"""Structured dashboard response contract tests.

Pins the contract the CompetitorAnalysisUI dashboard consumes:

    POST /api/v1/parser/execute
    {"parser_input": {"intent": "bootstrap", "form_input": {...}}}
    → ParserOutput envelope with the canonical `data` block.

Every WebHunter / LLMPing call in this module is a deterministic
mock — no live research is performed here. Live-service behavior
is exercised separately (deployed smoke checks), never in CI.

Scenarios (task §6):
  1.  valid dynamic bootstrap request
  2.  complete structured response
  3.  partial competitor retrieval
  4.  missing numeric fields
  5.  genuine numeric zero vs unknown values
  6.  missing pricing and market data
  7.  empty research results
  8.  upstream timeout or failure
  9.  sources and verification status
  10. response compatibility with the frontend canonical mapper
  11. bootstrap without chat history or conversational planning
"""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.orchestrator import Orchestrator
from app.schemas.analysis import ParserInput
from app.schemas.business import FormInput
from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import WebHunterClient, WebHunterError

# ── Fixtures ───────────────────────────────────────────

FULL_LLM_RESPONSE = {
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
            "pricingTier": "Enterprise",
            "marketPosition": "Leader",
            "marketShare": 60,
            "growthRate": 18,
            "funding": "$50M",
            "founded": "2015",
            "hq": "San Francisco",
        },
        {
            "name": "CompB",
            "description": "Growing challenger",
            "strengths": ["Innovation"],
            "weaknesses": ["Small team"],
            "pricingTier": "Mid-range",
            "marketPosition": "Challenger",
            "marketShare": 40,
            "growthRate": 32,
        },
    ],
    "products": [
        {
            "id": "prod-1",
            "competitorId": "compa",
            "name": "Analytics Suite",
            "tagline": "All-in-one",
            "category": "Analytics",
            "pricingModel": "Subscription",
            "startingPrice": 99.0,
            "features": [
                {
                    "id": "f-1",
                    "name": "Dashboards",
                    "description": "Custom dashboards",
                    "maturity": "GA",
                    "adoption": 80,
                }
            ],
        }
    ],
    "pricingTiers": [
        {
            "id": "tier-1",
            "competitorId": "compa",
            "name": "Pro",
            "priceMonthly": 99.0,
            "billing": "monthly",
            "features": ["Dashboards"],
            "bestFor": "Teams",
            "pricingModel": "Subscription",
            "highlighted": True,
        }
    ],
    "marketGaps": [
        {
            "id": "gap-1",
            "title": "No offline mode",
            "description": "Offline workflows unsupported",
            "opportunityScore": 70,
            "difficultyScore": 40,
            "estimatedRevenue": "$2M",
            "affectedSegments": ["Field teams"],
        }
    ],
    "insights": [
        {
            "id": "ins-1",
            "title": "SMB demand",
            "summary": "SMBs want affordable analytics",
            "detail": "Detail",
            "category": "Insight",
            "impact": "High",
            "confidence": 80,
            "relatedCompetitors": ["compb"],
            "explanation": "Why it matters",
        }
    ],
    "recommendations": [
        {
            "id": "rec-1",
            "title": "Launch free tier",
            "summary": "Capture SMBs",
            "detail": "Detail",
            "category": "Recommendation",
            "impact": "High",
            "confidence": 85,
            "explanation": "Lower CAC",
        }
    ],
    "action_plan": [
        {
            "id": "act-1",
            "title": "Ship free tier",
            "description": "Free tier launch",
            "rationale": "Removes friction",
            "owner": "Product",
            "priority": "P0",
            "horizon": "Now",
            "effort": "Medium",
            "impact": "High",
        }
    ],
    "reports": [
        {
            "id": "rep-1",
            "title": "Executive Summary",
            "summary": "Summary",
            "type": "Executive Summary",
            "date": "2026-01-01",
            "pages": 4,
            "sections": [{"heading": "Market", "body": "Body"}],
        }
    ],
    "report": "# TestCo Analysis\n\nStrong opportunity.",
    "swot": {
        "strengths": ["AI-first"],
        "weaknesses": ["New entrant"],
        "opportunities": ["EU expansion"],
        "threats": ["Big competitors"],
    },
    "comparisons": [
        {
            "title": "Pricing",
            "entities": ["TestCo", "CompA"],
            "rows": [
                {
                    "feature": "Entry price",
                    "values": {"TestCo": "$49", "CompA": "$99"},
                }
            ],
            "explanation": "TestCo undercuts",
        }
    ],
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
    "sources": [
        {
            "url": "https://example.com/a",
            "title": "CompA overview",
            "snippet": "CompA is an established player.",
        }
    ],
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
        competitors=["CompA", "CompB"],
        differentiators="AI-first",
        research_goals=["competitor_research"],
        user_query="How to compete?",
    )


def make_orchestrator(
    llm_response=FULL_LLM_RESPONSE,
    research_response=None,
):
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=llm_response)
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(
        return_value=research_response
        if research_response is not None
        else {"competitor_research": {"sources": ["https://example.com/raw"]}}
    )
    return Orchestrator(llmping=llmping, webhunter=webhunter)


def make_parser_input(
    form_input: FormInput | None = None,
    requested_count: int = 2,
    missing_information: list[str] | None = None,
) -> ParserInput:
    return ParserInput(
        intent="bootstrap",
        form_input=form_input if form_input is not None else make_form(),
        requested_count=requested_count,
        missing_information=missing_information or [],
    )


# ── Contract constants (FORbacked.md §6.14 + Backend→UI) ──

ENVELOPE_KEYS = {
    "intent", "status", "data", "answer", "missing_data",
    "context_update", "evicted_entities", "error",
    "result_counts", "operations_performed", "entity_statuses",
    "derived_data", "ui_state",
}

CANONICAL_DATA_KEYS = {
    "business_summary", "profile", "executive_summary",
    "market_info", "positioning", "gaps", "opportunities",
    "risks", "comparisons", "metric_cards", "report",
    "metadata", "competitors", "products", "pricing_tiers",
    "market_gaps", "insights", "recommendations",
    "action_plan", "reports", "charts", "swot", "sources",
    "businessName", "idea", "industry", "geography",
    "targetCustomers", "pricing", "businessModel",
    "differentiators", "researchGoals",
}

COMPETITOR_KEYS = {
    "id", "name", "logoColor", "description", "funding",
    "founded", "hq", "marketShare", "growthRate",
    "pricingTier", "marketPosition", "strengths",
    "weaknesses", "swot", "explanation", "status",
}

SOURCE_KEYS = {"id", "title", "url", "publisher", "date", "snippet"}


def assert_canonical_envelope(payload: dict) -> None:
    """The envelope and `data` block keep a stable shape for
    complete, partial, empty and failed analyses alike."""
    assert set(payload) == ENVELOPE_KEYS
    assert set(payload["data"]) == CANONICAL_DATA_KEYS
    assert payload["answer"] is None  # bootstrap never populates it
    assert set(payload["result_counts"]) == {
        "requested", "retrieved", "valid", "displayed",
    }
    assert set(payload["context_update"]) == {
        "version", "business", "entities", "result_meta",
        "constraints", "keywords",
    }
    assert set(payload["ui_state"]) == {
        "active_tab", "highlighted_entities", "result_counts",
    }
    assert set(payload["derived_data"]) == {
        "charts_generated", "calculations_performed",
    }


# ── 1 + 2: valid request → complete structured response ──
@pytest.mark.asyncio
async def test_valid_bootstrap_returns_complete_structured_response():
    orch = make_orchestrator()
    result = await orch.execute(make_parser_input())

    assert result.intent == "bootstrap"
    assert result.status in ("success", "partial")
    data = result.data
    assert data.businessName == "TestCo"
    assert data.idea == "AI analytics"
    assert data.industry == "SaaS"
    assert data.geography == "US"
    assert data.targetCustomers == "SMBs"
    assert data.pricing == "$49/month"
    assert data.businessModel == "Subscription"
    assert data.differentiators == "AI-first"
    assert data.researchGoals == ["competitor_research"]
    assert data.business_summary == "TestCo is an AI analytics platform."
    assert data.executive_summary == "Strong opportunity in SMB analytics."
    assert data.market_info == {"size": "$5B", "growth": "12%"}
    assert data.positioning == "AI-first, affordable."
    assert data.gaps == ["No mobile app"]
    assert data.opportunities == ["Expand to EU"]
    assert data.risks == ["Big competitors entering"]
    assert len(data.competitors) == 2
    assert len(data.products) == 1
    assert len(data.pricing_tiers) == 1
    assert len(data.market_gaps) == 1
    assert len(data.insights) == 1
    assert len(data.recommendations) == 1
    assert len(data.action_plan) == 1
    assert len(data.reports) == 1
    assert data.report.startswith("# TestCo Analysis")
    assert len(data.charts) >= 1
    assert len(data.metric_cards) == 1
    assert data.swot.strengths == ["AI-first"]
    assert len(data.comparisons) == 1
    assert len(data.sources) >= 1
    assert data.metadata.model_used == "llm-brain"
    assert data.metadata.processing_time_ms >= 0


# ── 10: compatibility with the frontend canonical mapper ──
@pytest.mark.asyncio
async def test_response_contract_matches_frontend_canonical_mapper():
    """Every field the UI's AnalysisData mapper reads is present
    with a predictable type — envelope snake_case, domain
    objects camelCase (FORbacked.md Backend→UI contract)."""
    orch = make_orchestrator()
    result = await orch.execute(make_parser_input())
    payload = result.model_dump(mode="json")

    assert_canonical_envelope(payload)

    comp = payload["data"]["competitors"][0]
    assert set(comp) == COMPETITOR_KEYS
    assert comp["name"] == "CompA"
    assert comp["id"] == "compa"
    assert comp["marketShare"] == 60.0
    assert comp["growthRate"] == 18.0
    assert comp["pricingTier"] == "Enterprise"
    assert comp["marketPosition"] == "Leader"
    assert comp["logoColor"].startswith("#")
    assert comp["funding"] == "$50M"
    assert comp["founded"] == "2015"
    assert comp["hq"] == "San Francisco"
    assert comp["status"] == "complete"

    tier = payload["data"]["pricing_tiers"][0]
    assert tier["priceMonthly"] == 99.0  # number, not "Custom"
    assert tier["billing"] == "monthly"
    assert tier["highlighted"] is True

    product = payload["data"]["products"][0]
    assert product["startingPrice"] == 99.0
    assert product["features"][0]["maturity"] == "GA"
    assert product["features"][0]["adoption"] == 80.0

    gap = payload["data"]["market_gaps"][0]
    assert gap["opportunityScore"] == 70.0
    assert gap["difficultyScore"] == 40.0
    assert gap["affectedSegments"] == ["Field teams"]

    insight = payload["data"]["insights"][0]
    assert insight["category"] == "Insight"
    assert insight["impact"] == "High"
    assert insight["confidence"] == 80.0

    action = payload["data"]["action_plan"][0]
    assert action["priority"] == "P0"
    assert action["horizon"] == "Now"

    report = payload["data"]["reports"][0]
    assert report["type"] == "Executive Summary"
    assert report["pages"] == 4
    assert report["sections"][0]["heading"] == "Market"

    chart = payload["data"]["charts"][0]
    assert chart["kind"] == "bar"
    assert chart["series"][0]["points"][0]["value"] == 49.0

    card = payload["data"]["metric_cards"][0]
    assert card["value"] == 5_000_000_000.0
    assert card["unit"] == "USD"

    source = payload["data"]["sources"][0]
    assert set(source) == SOURCE_KEYS
    assert source["url"] == "https://example.com/a"
    assert source["title"] == "CompA overview"


# ── 3: partial competitor retrieval ───────────────────
@pytest.mark.asyncio
async def test_partial_competitor_retrieval_preserves_successful_records(
    monkeypatch,
):
    """One requested competitor fails to resolve; the successful
    record, its sources and per-entity statuses survive."""
    import app.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "_LOOKUP_WH_RETRIES", 0)
    monkeypatch.setattr(orchestrator_module, "_LOOKUP_WH_DELAY_S", 0)

    response = dict(FULL_LLM_RESPONSE)
    response["competitors"] = []  # LLMPing found none → lookups run
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=response)
    llmping.extract_profile = AsyncMock(return_value={
        "name": "CompA",
        "description": "Established player",
        "pricingTier": "Enterprise",
        "marketPosition": "Leader",
        "confidence": 85,
        "sourceCount": 2,
        "strengths": ["Brand"],
        "weaknesses": ["Price"],
    })
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})

    async def search_company(company, _industry, _metric):
        if company == "CompB":
            raise WebHunterError("provider timeout for CompB")
        return [
            {
                "url": f"https://evidence.example/compa-{i}",
                "title": f"CompA source {i}",
                "snippet": "Verified details",
            }
            for i in range(2)
        ]

    webhunter.search_company = AsyncMock(side_effect=search_company)
    result = await Orchestrator(
        llmping=llmping, webhunter=webhunter
    ).execute(make_parser_input())

    assert result.status == "partial"
    assert len(result.data.competitors) == 1
    assert result.data.competitors[0].name == "CompA"
    assert result.result_counts.requested == 2
    assert result.result_counts.retrieved == 1

    statuses = {
        s.name: s.status for s in result.entity_statuses["competitors"]
    }
    assert statuses["CompA"] in ("complete", "partial")
    assert statuses["CompB"] == "failed"

    by_field = {m.field: m.reason for m in result.missing_data}
    assert "competitors[CompB]" in by_field
    # The failure is classified honestly as a timeout,
    # not relabeled as a generic failure.
    assert "timed out" in by_field["competitors[CompB]"]
    compb_status = next(
        s for s in result.entity_statuses["competitors"]
        if s.name == "CompB"
    )
    assert "timed_out" in compb_status.missing_fields
    assert any(
        m.field == "competitors" and "1 of 2" in m.reason
        for m in result.missing_data
    )
    # Successful lookup's sources are merged into the
    # response (1 from the LLM response + 2 from lookup).
    assert len(result.data.sources) == 3
    assert any(
        s.url == "https://evidence.example/compa-0"
        for s in result.data.sources
    )


# ── 4: missing numeric fields stay null ───────────────
@pytest.mark.asyncio
async def test_missing_numeric_fields_remain_null():
    """Absent marketShare / growthRate stay null — never 0."""
    response = dict(FULL_LLM_RESPONSE)
    for competitor in response["competitors"]:
        competitor.pop("marketShare", None)
        competitor.pop("growthRate", None)
    orch = make_orchestrator(llm_response=response)
    result = await orch.execute(make_parser_input())

    assert all(c.marketShare is None for c in result.data.competitors)
    assert all(c.growthRate is None for c in result.data.competitors)
    # No chart is fabricated from absent metrics.
    share_charts = [
        c for c in result.data.charts if c.title == "Market Share"
    ]
    assert share_charts == []


# ── 5: genuine zero vs unknown ────────────────────────
@pytest.mark.asyncio
async def test_genuine_zero_metrics_stay_zero():
    """A real 0 from upstream is preserved as 0 — distinct
    from an unknown (null) value."""
    response = dict(FULL_LLM_RESPONSE)
    for competitor in response["competitors"]:
        competitor["marketShare"] = 0
        competitor["growthRate"] = 0
    orch = make_orchestrator(llm_response=response)
    result = await orch.execute(make_parser_input())

    assert all(c.marketShare == 0 for c in result.data.competitors)
    assert all(c.growthRate == 0 for c in result.data.competitors)
    # All-zero metrics derive no share/growth charts (nothing
    # to plot that isn't fabricated); charts derived from
    # other real data (pricing, adoption) may remain.
    derived_titles = {c.title for c in result.data.charts}
    assert "Market Share" not in derived_titles
    assert "Growth Rate" not in derived_titles
    assert "marketShare" not in result.derived_data.charts_generated
    assert "growth" not in result.derived_data.charts_generated


# ── 6: missing pricing and market data ────────────────
@pytest.mark.asyncio
async def test_missing_pricing_and_market_data_reported():
    """Unavailable pricing/market data stays empty and is
    reported in missing_data — never invented."""
    response = dict(FULL_LLM_RESPONSE)
    response["pricingTiers"] = []
    response["market_info"] = {}
    for competitor in response["competitors"]:
        competitor.pop("pricingTier", None)
    orch = make_orchestrator(llm_response=response)
    result = await orch.execute(
        make_parser_input(
            missing_information=["pricing", "market size"]
        )
    )

    assert result.data.pricing_tiers == []
    assert result.data.market_info == {}
    assert all(c.pricingTier == "" for c in result.data.competitors)

    gaps = {m.field: m for m in result.missing_data}
    assert "pricing" in gaps
    assert "market size" in gaps
    assert gaps["pricing"].severity == "info"
    assert "pricing" in gaps["pricing"].reason


# ── 7: empty research results ─────────────────────────
@pytest.mark.asyncio
async def test_empty_research_results_return_analysis():
    """WebHunter returning an empty result set still yields a
    renderable analysis synthesized from the questionnaire."""
    orch = make_orchestrator(research_response={})
    result = await orch.execute(make_parser_input())

    assert result.status in ("success", "partial")
    assert len(result.data.competitors) == 2
    assert result.data.businessName == "TestCo"
    # Research was still requested exactly once.
    assert orch.webhunter.research.await_count == 1


# ── 8: upstream timeout or failure ────────────────────
@pytest.mark.asyncio
async def test_llmping_timeout_returns_error_envelope():
    """A genuine analysis failure (LLMPing down) retries, then
    returns an honest error envelope — not fabricated data."""
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(side_effect=LLMPingError("LLM timeout"))
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    result = await Orchestrator(
        llmping=llmping, webhunter=webhunter
    ).execute(make_parser_input())

    assert result.status == "error"
    assert "attempts" in (result.error or "")
    assert "LLM timeout" in result.error
    assert result.data.competitors == []
    assert any(
        m.severity == "critical" for m in result.missing_data
    )
    # 1 initial attempt + 2 retries.
    assert llmping.chat.await_count == 3


@pytest.mark.asyncio
async def test_webhunter_failure_keeps_collected_records():
    """WebHunter failure never kills the analysis — LLMPing
    still synthesizes from the questionnaire context."""
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=FULL_LLM_RESPONSE)
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(
        side_effect=WebHunterError("WebHunter upstream 503")
    )
    result = await Orchestrator(
        llmping=llmping, webhunter=webhunter
    ).execute(make_parser_input())

    assert result.status in ("success", "partial")
    assert len(result.data.competitors) == 2
    assert result.data.competitors[0].name == "CompA"


# ── 9: sources and verification status ────────────────
@pytest.mark.asyncio
async def test_sources_and_verification_status(monkeypatch):
    """Sources carry url/title/snippet; looked-up entities
    carry provenance (source, lookupConfidence) and their
    explanation keeps source references."""
    import app.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "_LOOKUP_WH_RETRIES", 0)
    monkeypatch.setattr(orchestrator_module, "_LOOKUP_WH_DELAY_S", 0)

    response = dict(FULL_LLM_RESPONSE)
    response["competitors"] = []
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=response)
    llmping.extract_profile = AsyncMock(return_value={
        "name": "CompA",
        "description": "Established player",
        "marketPosition": "Leader",
        "confidence": 78,
        "sourceCount": 2,
    })
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    webhunter.search_company = AsyncMock(return_value=[
        {
            "url": "https://evidence.example/compa-1",
            "title": "CompA source 1",
            "snippet": "Verified details",
        },
        {
            "url": "https://evidence.example/compa-2",
            "title": "CompA source 2",
            "snippet": "More verified details",
        },
    ])
    result = await Orchestrator(
        llmping=llmping, webhunter=webhunter
    ).execute(make_parser_input())

    assert result.data.sources
    for source in result.data.sources:
        assert source.url.startswith("https://")
        assert source.title
        assert source.snippet

    comp_status = next(
        s for s in result.entity_statuses["competitors"]
        if s.name == "CompA"
    )
    assert comp_status.source == "web"
    assert comp_status.status in ("complete", "partial")

    competitor = result.data.competitors[0]
    assert competitor.status in ("complete", "partial")
    assert competitor.explanation is not None
    assert competitor.explanation.sources


# ── 11: bootstrap without chat history / planning ─────
@pytest.mark.asyncio
async def test_bootstrap_without_chat_session_or_conversational_planning():
    """Bootstrap needs no session, no conversation history and
    no conversational planning: exactly one full_analysis call,
    one research call, and no explain/compare/extract calls."""
    orch = make_orchestrator()
    result = await orch.execute(
        ParserInput(
            intent="bootstrap",
            form_input=make_form(),
            requested_count=2,
            # No session_id, context_update or current_analysis.
        )
    )

    assert result.status in ("success", "partial")
    assert result.context_update is not None
    assert result.context_update.business["name"] == "TestCo"

    # Exactly one LLM call — the analysis itself.
    assert orch.llmping.chat.await_count == 1
    payload = orch.llmping.chat.await_args.args[0]
    assert payload["task"] == "full_analysis"
    assert payload["context"]["business"]["business_name"] == "TestCo"
    assert "research" in payload["context"]
    assert payload["required_outputs"]

    # Research planning is deterministic — no LLM planning step.
    assert orch.webhunter.research.await_count == 1
    research_kwargs = orch.webhunter.research.await_args.kwargs
    assert research_kwargs["research_types"] == ["competitor_research"]

    # No conversational endpoints of the pipeline are touched.
    orch.llmping.explain.assert_not_awaited()
    orch.llmping.compare.assert_not_awaited()
    orch.llmping.extract_profile.assert_not_awaited()
    orch.webhunter.search_company.assert_not_awaited()


# ── HTTP boundary: dashboard flow end-to-end ──────────
@pytest.fixture
def client():
    with patch("app.orchestrator.LLMPingClient") as LLM, \
         patch("app.orchestrator.WebHunterClient") as WH:
        llm_instance = AsyncMock()
        llm_instance.chat = AsyncMock(return_value=FULL_LLM_RESPONSE)
        wh_instance = AsyncMock()
        wh_instance.research = AsyncMock(return_value={})
        LLM.return_value = llm_instance
        WH.return_value = wh_instance
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_dashboard_bootstrap_http_flow(client):
    """The dashboard's single request returns the canonical
    envelope over HTTP — no chat session required."""
    resp = client.post(
        "/api/v1/parser/execute",
        json={
            "parser_input": {
                "intent": "bootstrap",
                "requested_count": 2,
                "form_input": make_form().model_dump(),
            }
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert_canonical_envelope(payload)
    assert payload["intent"] == "bootstrap"
    assert payload["status"] in ("success", "partial")
    assert payload["data"]["businessName"] == "TestCo"
    assert payload["data"]["competitors"][0]["name"] == "CompA"
    assert payload["context_update"]["business"]["name"] == "TestCo"


def test_dashboard_bootstrap_validates_required_fields(client):
    """A questionnaire without the required business name is
    rejected before any upstream call."""
    form = make_form().model_dump()
    del form["business_name"]
    resp = client.post(
        "/api/v1/parser/execute",
        json={
            "parser_input": {"intent": "bootstrap", "form_input": form}
        },
    )
    assert resp.status_code == 422
