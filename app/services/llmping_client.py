"""
LLMPing HTTP client.

CompetitorEngine is a pure orchestrator: it never reasons, prompts,
or generates text itself. All AI work is delegated to LLMPing via
HTTP. This client is the only seam to the LLM layer.

Fail-fast: if LLMPING_URL is unset, the app refuses to start (see
app/config.py).
"""
from typing import Any

import httpx
import structlog

from app.config import settings

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
        self.base_url = (base_url or settings.llmping_url).rstrip("/")
        self.timeout = timeout or settings.llmping_timeout
        self.api_key = api_key or settings.llmping_api_key

        if not self.base_url:
            raise LLMPingError("LLMPING_URL is not configured")

        self._client: httpx.AsyncClient | None = None

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
        client = await self._get_client()
        url = f"{self.base_url}/chat"
        log = logger.bind(url=url, task=payload.get("task"))
        log.info("llmping_request")

        try:
            response = await client.post(url, json=payload)
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
            log.error("llmping_connection_error", error=str(e))
            raise LLMPingError(f"Cannot reach LLMPing: {e}") from e
        except ValueError as e:
            log.error("llmping_invalid_json", error=str(e))
            raise LLMPingError(f"LLMPing returned non-JSON: {e}") from e
