"""
WebHunter HTTP client.

CompetitorEngine never crawls or searches the web itself. All
external research is delegated to WebHunter via HTTP. This client is
the only seam to the research layer.

Resolution is lazy: when no `WEBHUNTER_URL` is configured, the first
outbound call probes a candidate list (host.docker.internal:8765,
webhunter:8000, localhost:8765, plus any user-supplied extras) and
caches whichever upstream answers first. See app/services/discovery.py.

Wire contract with WebHunter (observed from the live `ResearchRequest`
schema and from sampling real responses):
  POST /research/sync
  body: { "query": "<text>", "max_results": <int|null> }
  reply: {
    "status": "completed" | "running" | "failed",
    "query": "...",
    "search_queries": [...],
    "search_results": [{ "sub_query", "url", "title", "snippet" }],
    "crawled_contents": [{ "url", "title", "content", "sub_query" }],
    "stats": { ... },
    "errors": [...],
    "error": null | "..."
  }

The orchestrator passes an internal `business` dict and a list of
`research_types`. We collapse them into a single query string and
use the synchronous endpoint so the orchestrator can return a result
in a single request. The reply is mapped into a `{results: {<topic>:
{sources: [...], content: [...]}}}` shape so the orchestrator's
existing `_sanitize_sources()` and downstream code keeps working.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings
from app.services import discovery

logger = structlog.get_logger(__name__)


class WebHunterError(Exception):
    """Raised when WebHunter returns an error or unreachable."""


# Maps orchestrator-internal research topics onto a phrase the
# natural-language query can carry.
_RESEARCH_TYPE_PHRASES = {
    "competitor_research":  "competitors and comparable companies",
    "pricing_research":     "pricing tiers, average prices, and price benchmarks",
    "customer_reviews":     "customer reviews, sentiment, and common complaints",
    "market_gap":           "market gaps, unmet needs, and emerging opportunities",
    "market_research":      "market size, growth rate, and segment trends",
    "swot_research":        "strengths, weaknesses, opportunities, and threats",
}


def _build_query(business: dict[str, Any], research_types: list[str]) -> str:
    """Combine a business profile + research topics into one query."""
    name = business.get("business_name") or "the company"
    industry = business.get("industry") or business.get("idea") or ""
    geos = business.get("geography") or ""
    pricing = business.get("pricing") or ""
    model = business.get("business_model") or ""

    topics = research_types or list(_RESEARCH_TYPE_PHRASES.keys())
    phrases = [
        _RESEARCH_TYPE_PHRASES.get(t, t.replace("_", " ")) for t in topics
    ]
    topic_clause = "; ".join(phrases)

    facts = " ".join(
        f for f in [f"industry: {industry}" if industry else "",
                    f"geography: {geos}" if geos else "",
                    f"pricing: {pricing}" if pricing else "",
                    f"business model: {model}" if model else ""]
    )
    if facts:
        facts = f" ({facts})"
    return (
        f"Research {name}{facts}. Cover: {topic_clause}. "
        f"Return URLs, titles, snippets, and any crawled content."
    )


def _adapt_response(
    business: dict[str, Any],
    research_types: list[str],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Map WebHunter's `{search_results, crawled_contents, ...}` reply into
    the orchestrator's `{results: {<topic>: {sources: [...]}}}` shape.

    One WebHunter call returns results for all topics together, so we
    fan them out under each requested research type. Sources are
    built from `search_results` (metadata) and `crawled_contents`
    (full text) — the orchestrator's `_sanitize_sources` only reads
    `sources`, but we keep the crawled text available so callers can
    use it for richer analysis.
    """
    search_results = data.get("search_results") or []
    crawled_contents = data.get("crawled_contents") or []
    sources: list[dict[str, Any]] = []
    for r in search_results:
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        if not url:
            continue
        sources.append(
            {
                "url": url,
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "sub_query": r.get("sub_query", ""),
                "source": url,
                "type": "web",
            }
        )
    crawled: list[dict[str, Any]] = []
    for c in crawled_contents:
        if not isinstance(c, dict):
            continue
        url = c.get("url")
        if not url:
            continue
        crawled.append(
            {
                "url": url,
                "title": c.get("title", ""),
                "content": c.get("content", ""),
                "sub_query": c.get("sub_query", ""),
                "source": url,
                "type": "web",
            }
        )

    topics = research_types or list(_RESEARCH_TYPE_PHRASES.keys())
    results: dict[str, dict[str, Any]] = {}
    for topic in topics:
        results[topic] = {
            "sources": sources,
            "crawled": crawled,
            "query": data.get("query", ""),
            "stats": data.get("stats", {}),
        }
    return {"results": results, "raw": data}


class WebHunterClient:
    """Thin async client for the WebHunter research endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = (base_url if base_url is not None else settings.webhunter_url).rstrip("/")
        self.timeout = timeout or settings.webhunter_timeout

        self._client: httpx.AsyncClient | None = None
        self._resolve_lock = asyncio.Lock()

    def _candidates(self) -> list[str]:
        return discovery._normalize_candidates(
            settings.discovery_candidates_webhunter,
            discovery.DEFAULT_WEBHUNTER_CANDIDATES,
        )

    async def resolve(self) -> str | None:
        """Resolve and cache the upstream URL. Returns the URL or None."""
        async with self._resolve_lock:
            if self.base_url:
                return self.base_url
            url = await discovery.discover(
                self._candidates(),
                env_override=None,
                timeout=settings.discovery_timeout_seconds,
            )
            if url:
                self.base_url = url
            return url

    async def _ensure_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        url = await self.resolve()
        if not url:
            raise WebHunterError(
                "WebHunter URL not configured and no candidate answered"
            )
        return url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def research(
        self,
        business: dict[str, Any],
        research_types: list[str],
    ) -> dict[str, Any]:
        """Run a synchronous research call and return results.

        Uses WebHunter's `POST /research/sync` so the orchestrator can
        handle the response within a single request. The synchronous
        endpoint blocks for up to `self.timeout` seconds until the
        upstream finishes researching.

        Returns a dict keyed by `results: {<research_type>: {sources,
        crawled, query, stats}}` — the same shape the orchestrator's
        `_sanitize_sources()` already reads.
        """
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/research/sync"
        log = logger.bind(url=url, types=research_types)

        query = _build_query(business, research_types)
        body = {"query": query, "max_results": 8}
        log.info("webhunter_request", query_chars=len(query))

        try:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json() if response.content else {}
            log.info("webhunter_response", status=response.status_code)
            # Surface upstream async failures (status == "failed" or
            # error key set) as structured data — don't raise, the
            # orchestrator already knows how to skip a research_type
            # that returned empty.
            if data.get("status") == "failed" or data.get("error"):
                log.warning(
                    "webhunter_async_failed",
                    error=data.get("error"),
                    errors=data.get("errors"),
                )
                return {"results": {}, "raw": data}
            return _adapt_response(business, research_types, data)
        except httpx.TimeoutException as e:
            log.error("webhunter_timeout", error=str(e))
            raise WebHunterError(f"WebHunter timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            log.error(
                "webhunter_http_error",
                status=e.response.status_code,
                error=str(e),
            )
            raise WebHunterError(
                f"WebHunter returned {e.response.status_code}: {e}"
            ) from e
        except httpx.RequestError as e:
            self.base_url = ""
            log.error("webhunter_connection_error", error=str(e))
            raise WebHunterError(f"Cannot reach WebHunter: {e}") from e
        except ValueError as e:
            log.error("webhunter_invalid_json", error=str(e))
            raise WebHunterError(
                f"WebHunter returned non-JSON: {e}"
            ) from e
