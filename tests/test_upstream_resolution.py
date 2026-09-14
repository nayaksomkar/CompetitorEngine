"""Tests for lazy URL resolution in LLMPingClient / WebHunterClient.

The clients must:
  - Construct without raising when no URL is configured.
  - Resolve lazily on first outbound call.
  - Cache the resolved URL across subsequent calls.
  - Clear the cached URL when a connection error happens, so the
    next call retries discovery.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import WebHunterClient, WebHunterError


# ── LLMPingClient ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_llmping_client_constructs_without_url():
    """No more init-time raise."""
    c = LLMPingClient(base_url="")
    assert c.base_url == ""


@pytest.mark.asyncio
async def test_llmping_client_resolves_lazily_on_first_call():
    c = LLMPingClient(base_url="")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"ok": True})
    mock_response.raise_for_status = MagicMock()

    with patch.object(
        c, "resolve", new=AsyncMock(return_value="http://llm:8000")
    ) as resolve:
        with patch.object(c, "_get_client") as gc:
            client = MagicMock()
            client.post = AsyncMock(return_value=mock_response)
            gc.return_value = client
            data = await c.chat({"task": "test"})

    assert data == {"ok": True}
    # resolve() called exactly once during the request.
    assert resolve.await_count == 1


@pytest.mark.asyncio
async def test_llmping_client_raises_when_discovery_fails():
    c = LLMPingClient(base_url="")
    with patch.object(c, "resolve", new=AsyncMock(return_value=None)):
        with pytest.raises(LLMPingError, match="not configured"):
            await c.chat({"task": "test"})


@pytest.mark.asyncio
async def test_llmping_client_caches_resolved_url():
    c = LLMPingClient(base_url="")

    # Pre-warm by calling resolve directly.
    with patch(
        "app.services.llmping_client.discovery.discover",
        new=AsyncMock(return_value="http://llm:8000"),
    ) as probe:
        url = await c.resolve()
    assert url == "http://llm:8000"
    assert c.base_url == "http://llm:8000"
    assert probe.await_count == 1

    # Second resolve should NOT re-probe.
    with patch(
        "app.services.llmping_client.discovery.discover",
        new=AsyncMock(return_value="http://OTHER:9000"),
    ) as probe2:
        url2 = await c.resolve()
    assert url2 == "http://llm:8000"  # cached
    assert probe2.await_count == 0


@pytest.mark.asyncio
async def test_llmping_client_connection_error_clears_cache():
    c = LLMPingClient(base_url="http://llm:8000")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500  # not used, request will fail before

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(c, "_get_client", return_value=mock_client):
        with pytest.raises(LLMPingError):
            await c.chat({"task": "test"})

    # Cache cleared — next resolve will retry discovery.
    assert c.base_url == ""


# ── WebHunterClient ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_webhunter_client_constructs_without_url():
    c = WebHunterClient(base_url="")
    assert c.base_url == ""


@pytest.mark.asyncio
async def test_webhunter_client_resolves_lazily_on_first_call():
    c = WebHunterClient(base_url="")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"results": {}})
    mock_response.raise_for_status = MagicMock()

    with patch.object(
        c, "resolve", new=AsyncMock(return_value="http://wh:8765")
    ):
        with patch.object(c, "_get_client") as gc:
            client = MagicMock()
            client.post = AsyncMock(return_value=mock_response)
            gc.return_value = client
            # Patch ResearchResponse.model_validate to accept anything.
            with patch(
                "app.services.webhunter_client.ResearchResponse.model_validate",
                return_value=MagicMock(),
            ):
                results = await c.research(
                    business={"business_name": "x"},
                    research_types=["competitor_research"],
                )
    assert results == {}


@pytest.mark.asyncio
async def test_webhunter_client_raises_when_discovery_fails():
    c = WebHunterClient(base_url="")
    with patch.object(c, "resolve", new=AsyncMock(return_value=None)):
        with pytest.raises(WebHunterError, match="not configured"):
            await c.research(
                business={"business_name": "x"},
                research_types=["competitor_research"],
            )


@pytest.mark.asyncio
async def test_webhunter_client_connection_error_clears_cache():
    c = WebHunterClient(base_url="http://wh:8765")

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(c, "_get_client", return_value=mock_client):
        with pytest.raises(WebHunterError):
            await c.research(
                business={"business_name": "x"},
                research_types=["competitor_research"],
            )

    assert c.base_url == ""


# ── Orchestrator fire-and-forget resolution ────────────────
@pytest.mark.asyncio
async def test_orchestrator_records_resolved_upstreams(monkeypatch):
    from app.orchestrator import Orchestrator, _resolved_upstreams

    # Reset module-level state for the test.
    monkeypatch.setitem(_resolved_upstreams, "llmping", None)
    monkeypatch.setitem(_resolved_upstreams, "webhunter", None)

    llm = AsyncMock()
    llm.base_url = ""
    llm.resolve = AsyncMock(return_value="http://llm:8000")

    wh = AsyncMock()
    wh.base_url = ""
    wh.resolve = AsyncMock(return_value="http://wh:8765")

    orch = Orchestrator(llmping=llm, webhunter=wh)
    # Fire-and-forget resolution was scheduled; await it explicitly
    # (we can't rely on the loop scheduling it deterministically here).
    await orch._resolve_upstreams()

    from app.orchestrator import get_resolved_upstreams
    assert get_resolved_upstreams() == {
        "llmping": "http://llm:8000",
        "webhunter": "http://wh:8765",
    }


@pytest.mark.asyncio
async def test_orchestrator_handles_unresolved_without_raising(monkeypatch):
    """Discovery failure should not break orchestrator construction."""
    from app.orchestrator import Orchestrator, _resolved_upstreams

    monkeypatch.setitem(_resolved_upstreams, "llmping", None)
    monkeypatch.setitem(_resolved_upstreams, "webhunter", None)

    llm = AsyncMock()
    llm.base_url = ""
    llm.resolve = AsyncMock(return_value=None)

    wh = AsyncMock()
    wh.base_url = ""
    wh.resolve = AsyncMock(return_value=None)

    orch = Orchestrator(llmping=llm, webhunter=wh)
    # Must not raise.
    await orch._resolve_upstreams()

    from app.orchestrator import get_resolved_upstreams
    assert get_resolved_upstreams() == {"llmping": None, "webhunter": None}


def test_orchestrator_uses_explicit_urls_without_resolution(monkeypatch):
    """When clients have URLs already, no discovery pass runs."""
    from app.orchestrator import Orchestrator, _resolved_upstreams

    monkeypatch.setitem(_resolved_upstreams, "llmping", None)
    monkeypatch.setitem(_resolved_upstreams, "webhunter", None)

    llm = AsyncMock()
    llm.base_url = "http://explicit-llm:8000"
    llm.resolve = AsyncMock(return_value="http://should-not-be-used:1")

    wh = AsyncMock()
    wh.base_url = "http://explicit-wh:8765"
    wh.resolve = AsyncMock(return_value="http://should-not-be-used:2")

    Orchestrator(llmping=llm, webhunter=wh)

    from app.orchestrator import get_resolved_upstreams
    resolved = get_resolved_upstreams()
    # Explicit URL wins, recorded immediately.
    assert resolved["llmping"] == "http://explicit-llm:8000"
    assert resolved["webhunter"] == "http://explicit-wh:8765"
    llm.resolve.assert_not_called()
    wh.resolve.assert_not_called()
