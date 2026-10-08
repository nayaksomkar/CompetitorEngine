"""Tests for the ORCHESTRATOR.md §5 "Ask About Any Company"
flow, context management (§8), chart derivation (PARSER.md
§10), and normalization (§4.1)."""
import pytest
from unittest.mock import AsyncMock

from app.orchestrator import Orchestrator, _evicted_profile_cache
from app.schemas.analysis import ParserInput
from app.services.llmping_client import LLMPingClient
from app.services.webhunter_client import (
    WebHunterClient,
    WebHunterError,
)


def make_context(competitors=None):
    return {
        "version": 1,
        "business": {"name": "TestCo", "industry": "Fragrance"},
        "entities": {
            "competitors": competitors or [],
            "focus": None,
        },
        "result_meta": {
            "requested_count": 3,
            "retrieved_count": len(competitors or []),
            "filters": [],
        },
        "constraints": {"included": [], "excluded": []},
        "keywords": ["Fragrance"],
    }


def make_lookup_llmping(confidence=78, source_count=5):
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.extract_profile = AsyncMock(
        return_value={
            "name": "Fragante",
            "description": (
                "Fragante is a mid-range Indian fragrance "
                "brand launched in 2019."
            ),
            "pricingTier": "Mid-range",
            "marketPosition": "Emerging",
            "marketShare": None,
            "growthRate": None,
            "funding": None,
            "founded": "2019",
            "hq": "Mumbai, India",
            "strengths": ["Affordable luxury"],
            "weaknesses": ["Limited distribution"],
            "confidence": confidence,
            "sourceCount": source_count,
        }
    )
    llmping.compare = AsyncMock(return_value="Fragante vs the field.")
    return llmping


def make_lookup_webhunter():
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.search_company = AsyncMock(
        return_value=[
            {
                "url": "https://example.com/fragante-1",
                "title": "Fragante launches",
                "snippet": "Fragante launches in Mumbai.",
            },
            {
                "url": "https://example.com/fragante-2",
                "title": "Fragante review",
                "snippet": "A mid-range Indian fragrance.",
            },
        ]
    )
    return webhunter


@pytest.fixture(autouse=True)
def clear_evicted_cache():
    _evicted_profile_cache.clear()
    yield
    _evicted_profile_cache.clear()


# ── §5 lookup flow ────────────────────────────────────
@pytest.mark.asyncio
async def test_question_lookup_flow():
    """A question naming an unknown company triggers
    WebHunter search → LLMPing extraction → answer block."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Fragante",
            session_id="test-123",
            context_update=make_context(),
        )
    )

    assert result.answer is not None
    assert result.answer.competitors[0].name == "Fragante"
    assert result.answer.competitors[0].source == "web"
    assert result.answer.competitors[0].lookupConfidence == 78
    assert result.answer.competitors[0].profile.founded == "2019"
    assert result.answer.competitors[0].profile.hq == "Mumbai, India"
    assert len(result.answer.sources) == 2
    assert result.status == "success"

    # Upstreams were called exactly once per entity.
    webhunter.search_company.assert_awaited_once_with(
        "Fragante", "Fragrance"
    )
    llmping.extract_profile.assert_awaited_once()

    # Context was written back with the new entity.
    assert result.context_update is not None
    assert (
        "fragante"
        in result.context_update.entities["competitors"]
    )
    assert result.context_update.business["name"] == "TestCo"


@pytest.mark.asyncio
async def test_question_lookup_partial_when_low_confidence():
    """Extraction confidence < 40 marks the entity partial
    and reports it in missing_data (§5.1 step 3)."""
    llmping = make_lookup_llmping(confidence=20, source_count=5)
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Fragante",
            context_update=make_context(),
        )
    )
    assert result.status == "partial"
    assert result.answer.competitors[0].lookupConfidence == 20
    assert any(
        m.severity == "warning" and "Fragante" in m.reason
        for m in result.missing_data
    )


@pytest.mark.asyncio
async def test_lookup_failure_isolation():
    """WebHunter down → entity skipped, warning reported,
    partial status (§7.2 failure isolation)."""
    llmping = make_lookup_llmping()
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.search_company = AsyncMock(
        side_effect=WebHunterError("WebHunter down")
    )
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Fragante",
            context_update=make_context(),
        )
    )
    assert result.status == "partial"
    assert result.answer is not None
    assert result.answer.competitors == []
    assert any(
        m.severity == "warning" and "Fragante" in m.field
        for m in result.missing_data
    )
    # No context write-back happened (no entities resolved).
    assert result.context_update is not None
    assert result.context_update.model_dump() == make_context()


@pytest.mark.asyncio
async def test_lookup_budget_caps_at_three_searches():
    """A message naming 4 companies only looks up 3 (§12)."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        ParserInput(
            intent="question",
            message=(
                "compare Alpha Beta, Gamma Delta, "
                "Epsilon Zeta and Eta Theta"
            ),
            context_update=make_context(),
        )
    )
    assert webhunter.search_company.await_count <= 3


# ── Context management & eviction (§8) ────────────────
@pytest.mark.asyncio
async def test_eviction_at_fourth_entity():
    """Adding a 4th competitor evicts the oldest active
    entity and reports it in evicted_entities (§8.4)."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    context = make_context(
        competitors=["forest-essentials", "kama-ayurveda", "jo-malone"]
    )
    result = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Fragante",
            context_update=context,
        )
    )
    assert len(result.evicted_entities) == 1
    assert result.evicted_entities[0].id == "forest-essentials"
    # Active set is still capped at 3.
    assert (
        len(result.context_update.entities["competitors"]) == 3
    )
    assert (
        result.context_update.entities["competitors"][0]
        == "kama-ayurveda"
    )
    assert (
        result.context_update.entities["competitors"][-1]
        == "fragante"
    )


@pytest.mark.asyncio
async def test_evicted_profile_served_from_cache():
    """An evicted entity is answerable from the 24h cache
    without a fresh WebHunter call (§8.4)."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    context = make_context(
        competitors=["forest-essentials", "kama-ayurveda", "jo-malone"]
    )
    first = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Fragante",
            context_update=context,
        )
    )
    assert len(first.evicted_entities) == 1

    # The evicted entity was cached with the profile from
    # the current analysis... but here the current analysis
    # has no competitor with that slug, so the cache only
    # holds what the lookup produced for the new entity.
    # Seed the cache directly to simulate a prior eviction
    # of a fully-profiled entity.
    from app.orchestrator import Orchestrator as O
    O._evicted_cache_set(
        "forest-essentials",
        {
            "name": "Forest Essentials",
            "description": "Luxury Indian Ayurvedic brand.",
            "pricingTier": "Premium",
            "marketPosition": "Leader",
            "strengths": ["Brand"],
            "weaknesses": ["Price"],
        },
    )

    webhunter.search_company.reset_mock()
    second = await orch.execute(
        ParserInput(
            intent="question",
            message="tell me about Forest Essentials",
            context_update=first.context_update.model_dump()
            if first.context_update
            else None,
        )
    )
    # Answered from cache — no fresh search.
    webhunter.search_company.assert_not_awaited()
    assert second.answer.competitors[0].name == "Forest Essentials"
    assert second.answer.competitors[0].source == "context"


@pytest.mark.asyncio
async def test_refine_add_lookup_with_eviction():
    """refine that adds a new competitor runs the lookup
    flow and merges the entity into the analysis data."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    current = {
        "competitors": [
            {"id": "forest-essentials", "name": "Forest Essentials"},
            {"id": "kama-ayurveda", "name": "Kama Ayurveda"},
            {"id": "jo-malone", "name": "Jo Malone"},
        ]
    }
    result = await orch.execute(
        ParserInput(
            intent="refine",
            message="add Fragante as a competitor",
            current_analysis=current,
            context_update=make_context(
                competitors=[
                    "forest-essentials",
                    "kama-ayurveda",
                    "jo-malone",
                ]
            ),
        )
    )
    assert result.intent == "refine"
    # New competitor merged into the data.
    names = {c.name for c in result.data.competitors}
    assert "Fragante" in names
    # Cap enforced on the merged set.
    assert len(result.data.competitors) == 3
    assert len(result.evicted_entities) == 1
    assert result.answer is not None
    assert result.answer.competitors[0].name == "Fragante"


@pytest.mark.asyncio
async def test_refine_remove_competitor():
    """refine with a removal verb drops the entity from
    the data and the active context."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    result = await orch.execute(
        ParserInput(
            intent="refine",
            message="remove Kama Ayurveda",
            current_analysis={
                "competitors": [
                    {"id": "kama-ayurveda", "name": "Kama Ayurveda"},
                    {"id": "jo-malone", "name": "Jo Malone"},
                ]
            },
            context_update=make_context(
                competitors=["kama-ayurveda", "jo-malone"]
            ),
        )
    )
    assert result.intent == "refine"
    assert "Kama Ayurveda" not in {
        c.name for c in result.data.competitors
    }
    assert (
        result.context_update.entities["competitors"]
        == ["jo-malone"]
    )


# ── Reference resolution (§8.3) ───────────────────────
def test_resolve_reference_focus():
    orch = Orchestrator.__new__(Orchestrator)
    context = make_context(competitors=["compa"])
    context["entities"]["focus"] = "compa"
    current = {
        "competitors": [{"id": "compa", "name": "CompA"}]
    }
    assert (
        orch._resolve_reference("tell me about it", context, current)
        == "CompA"
    )


def test_resolve_reference_cheaper():
    orch = Orchestrator.__new__(Orchestrator)
    current = {
        "competitors": [
            {"id": "a", "name": "CompA"},
            {"id": "b", "name": "CompB"},
        ],
        "pricingTiers": [
            {"competitorId": "a", "name": "Pro", "priceMonthly": 99},
            {"competitorId": "b", "name": "Lite", "priceMonthly": 19},
        ],
    }
    assert (
        orch._resolve_reference(
            "which is the cheaper one?", None, current
        )
        == "CompB"
    )


def test_resolve_reference_leader():
    orch = Orchestrator.__new__(Orchestrator)
    current = {
        "competitors": [
            {"id": "a", "name": "CompA", "marketPosition": "Challenger"},
            {"id": "b", "name": "CompB", "marketPosition": "Leader"},
        ]
    }
    assert (
        orch._resolve_reference("tell me about the leader", None, current)
        == "CompB"
    )


# ── Entity extraction ─────────────────────────────────
def test_extract_company_mentions():
    orch = Orchestrator.__new__(Orchestrator)
    mentions = orch._extract_company_mentions(
        "tell me about Fragante", None, None
    )
    assert "Fragante" in mentions

    mentions = orch._extract_company_mentions(
        "compare Forest Essentials and Kama Ayurveda", None, None
    )
    assert "Forest Essentials" in mentions
    assert "Kama Ayurveda" in mentions

    # Quoted names win even when lowercase.
    mentions = orch._extract_company_mentions(
        'tell me about "fragante" please', None, None
    )
    assert "fragante" in mentions


def test_extract_company_mentions_ignores_non_entities():
    orch = Orchestrator.__new__(Orchestrator)
    mentions = orch._extract_company_mentions(
        "what about pricing?", None, None
    )
    assert mentions == []


# ── Chart derivation (PARSER.md §10) ──────────────────
FULL_LLM_RESPONSE = {
    "business_summary": "TestCo is an AI analytics platform.",
    "executive_summary": "Strong opportunity.",
    "market_info": {"size": "$5B"},
    "positioning": "AI-first",
    "gaps": [],
    "opportunities": [],
    "risks": [],
    "competitors": [
        {
            "name": "CompA",
            "description": "Established",
            "marketShare": 60,
            "growthRate": 35,
            "market_position": "Leader",
        },
        {
            "name": "CompB",
            "description": "Growing",
            "marketShare": 40,
            "growthRate": 12,
            "market_position": "Challenger",
        },
    ],
    "swot": {"strengths": [], "weaknesses": [],
             "opportunities": [], "threats": []},
    "comparisons": [],
    "visualizations": [],
    "metric_cards": [],
    "recommendations": [],
    "action_plan": [],
    "report": "",
    "sources": [],
}


def make_bootstrap_orchestrator(llm_response=FULL_LLM_RESPONSE):
    llmping = AsyncMock(spec=LLMPingClient)
    llmping.chat = AsyncMock(return_value=llm_response)
    webhunter = AsyncMock(spec=WebHunterClient)
    webhunter.research = AsyncMock(return_value={})
    return Orchestrator(llmping=llmping, webhunter=webhunter)


def make_form_input():
    from app.schemas.business import FormInput

    return FormInput(
        business_name="TestCo",
        idea="AI analytics",
        industry="SaaS",
        competitors=["CompA", "CompB"],
    )


@pytest.mark.asyncio
async def test_derive_charts_from_market_share_and_growth():
    """Bootstrap derives marketShare and growth bar+pie
    charts from real competitor data."""
    orch = make_bootstrap_orchestrator()
    result = await orch.execute(
        ParserInput(
            intent="bootstrap",
            form_input=make_form_input(),
            requested_count=2,
        )
    )
    titles = {(c.title, c.kind) for c in result.data.charts}
    assert ("Market Share", "bar") in titles
    assert ("Market Share", "pie") in titles
    assert ("Growth Rate", "bar") in titles
    assert ("Growth Rate", "pie") in titles
    assert set(result.derived_data.charts_generated) >= {
        "marketShare",
        "marketSharePie",
        "growth",
        "growthPie",
    }
    # Points carry the real values and consistent colors.
    share_bar = next(
        c
        for c in result.data.charts
        if c.title == "Market Share" and c.kind == "bar"
    )
    values = sorted(
        p.value for p in share_bar.series[0].points
    )
    assert values == [40.0, 60.0]


@pytest.mark.asyncio
async def test_market_share_normalized_to_100():
    """Shares that don't sum to 100 are redistributed."""
    response = dict(FULL_LLM_RESPONSE)
    response["competitors"] = [
        {
            "name": "CompA",
            "description": "Established",
            "marketShare": 80,
        },
        {
            "name": "CompB",
            "description": "Growing",
            "marketShare": 20,
        },
        {
            "name": "CompC",
            "description": "Niche",
            "marketShare": 20,
        },
    ]
    orch = make_bootstrap_orchestrator(response)
    result = await orch.execute(
        ParserInput(
            intent="bootstrap",
            form_input=make_form_input(),
            requested_count=3,
        )
    )
    total = sum(c.marketShare for c in result.data.competitors)
    assert abs(total - 100.0) < 0.01


@pytest.mark.asyncio
async def test_market_share_zeros_stay_zero_without_data():
    """Explicit zeros or missing shares → zeros stay zero and
    no share charts are derived (no fabricated values)."""
    for mutate in (
        lambda c: c.update(marketShare=0, growthRate=0),
        lambda c: [c.pop(k, None) for k in ("marketShare", "growthRate")],
    ):
        response = dict(FULL_LLM_RESPONSE)
        for c in response["competitors"]:
            mutate(c)
        orch = make_bootstrap_orchestrator(response)
        result = await orch.execute(
            ParserInput(
                intent="bootstrap",
                form_input=make_form_input(),
                requested_count=2,
            )
        )
        assert all(
            c.marketShare == 0 for c in result.data.competitors
        )
        assert result.data.charts == []
        assert result.derived_data.charts_generated == []


# ── Slugs & colors ────────────────────────────────────
def test_slugify():
    assert Orchestrator._slugify("Forest Essentials") == (
        "forest-essentials"
    )
    assert Orchestrator._slugify("Jo  Malone  India!") == (
        "jo-malone-india"
    )
    assert Orchestrator._slugify("  Café & Co.  ") == (
        "cafe-co"
    )
    assert Orchestrator._slugify("") == ""


def test_color_for_is_deterministic():
    assert Orchestrator._color_for("CompA") == (
        Orchestrator._color_for("CompA")
    )
    assert Orchestrator._color_for("CompA").startswith("#")
    assert Orchestrator._color_for("CompA") != (
        Orchestrator._color_for("CompB")
    )


# ── Evicted-profile cache ─────────────────────────────
def test_evicted_cache_roundtrip():
    orch = Orchestrator.__new__(Orchestrator)
    Orchestrator._evicted_cache_set("fragante", {"name": "F"})
    assert orch._evicted_cache_get("fragante") == {"name": "F"}


def test_evicted_cache_expires():
    orch = Orchestrator.__new__(Orchestrator)
    Orchestrator._evicted_cache_set("fragante", {"name": "F"})
    # Force expiry by backdating the entry.
    _evicted_profile_cache["fragante"]["cached_at"] -= 25 * 3600
    assert orch._evicted_cache_get("fragante") is None
    assert "fragante" not in _evicted_profile_cache


# ── Compare flow with lookups ─────────────────────────
@pytest.mark.asyncio
async def test_compare_mixed_context_and_lookup():
    """Compare resolves in-context entities and looks up
    unknown ones, returning competitors + comparedTo."""
    llmping = make_lookup_llmping()
    webhunter = make_lookup_webhunter()
    orch = Orchestrator(llmping=llmping, webhunter=webhunter)

    context = make_context(competitors=["forest-essentials"])
    current = {
        "competitors": [
            {
                "id": "forest-essentials",
                "name": "Forest Essentials",
                "description": "Luxury Ayurvedic brand.",
            }
        ]
    }
    result = await orch.execute(
        ParserInput(
            intent="compare",
            message="compare Forest Essentials and Fragante",
            current_analysis=current,
            context_update=context,
        )
    )
    assert result.answer is not None
    # First entity is the in-context one, second is looked up.
    assert result.answer.competitors[0].name == "Forest Essentials"
    assert result.answer.competitors[0].source == "context"
    assert result.answer.comparedTo[0].name == "Fragante"
    assert result.answer.comparedTo[0].source == "web"
    assert result.answer.summary == "Fragante vs the field."
