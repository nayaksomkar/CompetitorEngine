"""
WebHunter HTTP client.

CompetitorEngine never crawls or searches the web itself. All
external research is delegated to WebHunter via HTTP. This client is
the only seam to the research layer.

Resolution is lazy: when no `WEBHUNTER_URL` is configured, the first
outbound call probes a candidate list (host.docker.internal:8765,
webhunter:8000, localhost:8765, plus any user-supplied extras) and
caches whichever upstream answers first. See
app/services/discovery.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings
from app.schemas.research import ResearchRequest, ResearchResponse
from app.services import discovery

logger = structlog.get_logger(__name__)


class WebHunterError(Exception):
    """Raised when WebHunter returns an error or unreachable."""


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
        """
        Ask WebHunter for fresh research on a list of topics.

        Args:
            business: dict describing the business (FormInput shape).
            research_types: list of research areas, e.g.
                ["competitor_research", "pricing_research",
                 "customer_reviews", "market_gap"].

        Returns:
            Dict keyed by research_type, each value is the normalized
            data WebHunter returned (sources, snippets, structured
            fields). Missing research_types are simply absent.
        """
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/api/v1/scrape"
        log = logger.bind(url=url, types=research_types)
        log.info("webhunter_request")

        request_payload = ResearchRequest(
            business=business,
            research_types=research_types,
        ).model_dump()

        try:
            response = await client.post(url, json=request_payload)
            response.raise_for_status()
            data = response.json()
            log.info("webhunter_response", status=response.status_code)
            # Validate via Pydantic but keep loose — WebHunter's
            # exact shape may evolve.
            ResearchResponse.model_validate(data)
            return data.get("results", {})
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
