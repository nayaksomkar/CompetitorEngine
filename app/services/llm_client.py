import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger(__name__)


class LLMError(Exception):
    """Raised when LLM service fails."""
    pass


class LLMClient:
    """Async HTTP client for communicating with the LLM Brain service."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = base_url or settings.llm_service_url
        self.timeout = timeout or settings.llm_timeout
        self.max_retries = settings.llm_max_retries

    @retry(
        stop=stop_after_attempt(settings.llm_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def send_query(self, query: str) -> str:
        """
        Send a query to the LLM Brain service.
        Expects: {"query": "..."} -> response with generated text.
        """
        if not query or not query.strip():
            raise LLMError("Empty query provided")

        log = logger.bind(llm_url=self.base_url, query_length=len(query))
        log.info("sending_query_to_llm")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.base_url,
                    json={"query": query},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()

                # Support multiple response formats
                result = (
                    data.get("response")
                    or data.get("result")
                    or data.get("text")
                    or data.get("content")
                    or data.get("answer")
                    or str(data)
                )

                log.info("llm_response_received", response_length=len(str(result)))
                return str(result)

        except httpx.TimeoutException as e:
            log.error("llm_timeout", error=str(e))
            raise LLMError(f"LLM service timeout after {self.timeout}s") from e
        except httpx.HTTPStatusError as e:
            log.error("llm_http_error", status=e.response.status_code, error=str(e))
            raise LLMError(f"LLM service error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            log.error("llm_connection_error", error=str(e))
            raise LLMError(f"Cannot connect to LLM service at {self.base_url}") from e

    async def send_prompt_with_context(self, system_prompt: str, user_content: str) -> str:
        """Send a structured prompt with system context and user content."""
        combined = f"{system_prompt}\n\n---\n\n{user_content}"
        return await self.send_query(combined)
