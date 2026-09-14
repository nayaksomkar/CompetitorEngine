"""
LLMPing HTTP client.

CompetitorEngine is a pure orchestrator: it never reasons, prompts,
or generates text itself. All AI work is delegated to LLMPing via
HTTP. This client is the only seam to the LLM layer.

Resolution is lazy: when no `LLMPING_URL` is configured, the first
outbound call probes a candidate list (host.docker.internal:8000,
llmping:8000, localhost:8000, plus any user-supplied extras) and
caches whichever upstream answers first. See app/services/discovery.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings
from app.services import discovery

logger = structlog.get_logger(__name__)


class LLMPingError(Exception):
    """Raised when LLMPing returns an error or unreachable."""


class LLMPingClient:
    """Thin async client for the LLMPing /chat endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        api_key: str | None = None,
    ):
        self.base_url = (base_url if base_url is not None else settings.llmping_url).rstrip("/")
        self.timeout = timeout or settings.llmping_timeout
        self.api_key = api_key or settings.llmping_api_key

        self._client: httpx.AsyncClient | None = None
        self._resolve_lock = asyncio.Lock()

    def _candidates(self) -> list[str]:
        """Build the candidate list for this client."""
        return discovery._normalize_candidates(
            settings.discovery_candidates_llmping,
            discovery.DEFAULT_LLMPING_CANDIDATES,
        )

    async def resolve(self) -> str | None:
        """Resolve and cache the upstream URL. Returns the URL or None.

        Safe to call multiple times — resolution is gated by a lock
        and only the first call performs probes.
        """
        async with self._resolve_lock:
            if self.base_url:
                return self.base_url
            url = await discovery.discover(
                self._candidates(),
                env_override=None,  # explicit URL already consumed in __init__
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
            raise LLMPingError(
                "LLMPing URL not configured and no candidate answered"
            )
        return url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout, headers=headers
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Send a chat task to LLMPing and return its structured JSON
        response. Raises LLMPingError on any failure.

        `payload` shape is an open contract with LLMPing. Common keys:
          - task: str (e.g. "full_analysis", "decide_research_need",
                    "answer_question")
          - session_id: str
          - context: dict (business + research + current analysis)
          - message: str (for chat follow-ups)
          - required_outputs: list[str]
          - include_visualizations: bool
        """
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/chat"
        log = logger.bind(url=url, task=payload.get("task"))
        log.info("llmping_request")

        # LLMPing's /chat schema only knows {query, session_id}. Our internal
        # contract uses {message, session_id, ...}. Map message -> query here
        # so the wire body matches LLMPing's Pydantic model.
        message = payload.get("message")
        if not message:
            raise LLMPingError(
                "Cannot call LLMPing: payload is missing 'message'"
            )
        body = {
            "query": message,
            "session_id": payload.get("session_id", ""),
        }

        try:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
            log.info("llmping_response", status=response.status_code)
            return data
        except httpx.TimeoutException as e:
            log.error("llmping_timeout", error=str(e))
            raise LLMPingError(f"LLMPing timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            log.error(
                "llmping_http_error",
                status=e.response.status_code,
                error=str(e),
            )
            raise LLMPingError(
                f"LLMPing returned {e.response.status_code}: {e}"
            ) from e
        except httpx.RequestError as e:
            # Reset cached URL — next call will re-discover in case
            # the upstream just came online or moved.
            self.base_url = ""
            log.error("llmping_connection_error", error=str(e))
            raise LLMPingError(f"Cannot reach LLMPing: {e}") from e
        except ValueError as e:
            log.error("llmping_invalid_json", error=str(e))
            raise LLMPingError(f"LLMPing returned non-JSON: {e}") from e
