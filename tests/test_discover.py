"""Tests for app/services/discovery.py."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import discovery
from app.services.discovery import _normalize_candidates, discover


# ── Candidate normalization ─────────────────────────────────
def test_normalize_candidates_dedups_and_merges():
    out = _normalize_candidates(
        "http://a:9000, http://b:9000",
        ["http://a:9000", "http://c:9000"],
    )
    # User-supplied come first, defaults appended (and deduped).
    assert out == [
        "http://a:9000",
        "http://b:9000",
        "http://c:9000",
    ]


def test_normalize_candidates_strips_trailing_slash():
    out = _normalize_candidates(
        "http://a:9000/",
        ["http://b:9000/"],
    )
    assert out == ["http://a:9000", "http://b:9000"]


def test_normalize_candidates_handles_empty_csv():
    out = _normalize_candidates("", ["http://a:9000", "http://b:9000"])
    assert out == ["http://a:9000", "http://b:9000"]


def test_normalize_candidates_skips_blank_segments():
    out = _normalize_candidates("http://a:9000,,", [])
    assert out == ["http://a:9000"]


# ── discover() ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_discover_returns_first_healthy_url():
    with patch.object(
        discovery, "_probe", new=AsyncMock(side_effect=[False, True])
    ) as probe:
        url = await discover(
            ["http://first:9000", "http://second:9000"],
            timeout=1.0,
        )
    assert url == "http://second:9000"
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_discover_returns_none_when_all_fail():
    with patch.object(
        discovery, "_probe", new=AsyncMock(return_value=False)
    ):
        url = await discover(["http://x:1", "http://x:2"], timeout=1.0)
    assert url is None


@pytest.mark.asyncio
async def test_discover_env_override_takes_priority():
    with patch.object(
        discovery, "_probe", new=AsyncMock(return_value=True)
    ) as probe:
        url = await discover(
            ["http://default:9000"],
            env_override="http://override:9000",
            timeout=1.0,
        )
    assert url == "http://override:9000"
    # Override must be probed first; default may or may not be probed.
    called = [c.args[0] for c in probe.await_args_list]
    assert called[0] == "http://override:9000"


@pytest.mark.asyncio
async def test_discover_empty_returns_none():
    url = await discover([], timeout=1.0)
    assert url is None


@pytest.mark.asyncio
async def test_discover_probe_treats_any_http_response_as_healthy():
    """Any HTTP response (even 404) proves the host is alive.

    Discovery only checks reachability — it doesn't care whether
    the service has a root route. A 404 is still a healthy host.
    """
    # 2xx
    with patch.object(
        discovery, "_probe", new=AsyncMock(return_value=True)
    ):
        assert await discover(["http://ok:1"], timeout=1.0) == "http://ok:1"

    # Any HTTP status code — even 4xx / 5xx — means the server answered.
    for status in (200, 201, 301, 400, 404, 500, 503):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = status
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        with patch.object(
            discovery.httpx, "AsyncClient", return_value=mock_client
        ):
            assert await discovery._probe("http://x", 1.0) is True


@pytest.mark.asyncio
async def test_discover_probe_treats_network_error_as_unhealthy():
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(
        discovery.httpx, "AsyncClient", return_value=mock_client
    ):
        assert await discovery._probe("http://x", 1.0) is False


# ── discover_all() ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_discover_all_resolves_both_in_parallel():
    fake = AsyncMock(
        side_effect=[
            "http://llm:8000",
            "http://wh:8765",
        ]
    )
    with patch.object(discovery, "discover", new=fake):
        out = await discovery.discover_all()
    assert out == {"llmping": "http://llm:8000", "webhunter": "http://wh:8765"}
    assert fake.await_count == 2


@pytest.mark.asyncio
async def test_discover_all_returns_none_when_missing():
    with patch.object(discovery, "discover", new=AsyncMock(return_value=None)):
        out = await discovery.discover_all()
    assert out == {"llmping": None, "webhunter": None}
