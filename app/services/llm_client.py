import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger(__name__)


class LLMError(Exception):
    """Raised when LLM service fails."""
    pass


class LLMClient:
    """
    Async HTTP client for communicating with LLM providers.
    Supports multiple providers with automatic fallback chain.
    """

    def __init__(
        self,
        provider: str | None = None,
        fallback_chain: list[str] | None = None,
    ):
        """
        Initialize LLM client.

        Args:
            provider: Primary provider name (from config.json)
            fallback_chain: List of provider names to try on failure
        """
        self.provider_name = provider or settings.default_provider
        self.fallback_chain = fallback_chain or settings.fallback_chain
        self.max_retries = settings.llm_max_retries

    def _get_provider_url(self, provider_name: str) -> str | None:
        """Get URL for a provider."""
        provider = settings.get_provider(provider_name)
        if provider and provider.get("enabled", True):
            return provider.get("url")
        return None

    def _get_provider_timeout(self, provider_name: str) -> int:
        """Get timeout for a provider."""
        provider = settings.get_provider(provider_name)
        if provider:
            return provider.get("timeout", settings.llm_timeout)
        return settings.llm_timeout

    def _get_provider_model(self, provider_name: str) -> str | None:
        """Get model for a provider."""
        provider = settings.get_provider(provider_name)
        if provider:
            return provider.get("model")
        return None

    async def send_query(self, query: str) -> str:
        """
        Send a query to LLM with automatic fallback.
        Tries primary provider first, then falls back through the chain.
        """
        if not query or not query.strip():
            raise LLMError("Empty query provided")

        # Build list of providers to try: primary + fallback chain
        providers_to_try = [self.provider_name] + [
            p for p in self.fallback_chain if p != self.provider_name
        ]

        last_error = None

        for provider_name in providers_to_try:
            url = self._get_provider_url(provider_name)
            if not url:
                continue

            timeout = self._get_provider_timeout(provider_name)
            model = self._get_provider_model(provider_name)

            try:
                result = await self._try_provider(
                    url=url,
                    query=query,
                    timeout=timeout,
                    provider_name=provider_name,
                    model=model,
                )
                if provider_name != self.provider_name:
                    logger.info(
                        "fallback_success",
                        primary=self.provider_name,
                        fallback=provider_name,
                    )
                return result

            except LLMError as e:
                last_error = e
                logger.warning(
                    "provider_failed",
                    provider=provider_name,
                    error=str(e),
                    trying_next=provider_name != providers_to_try[-1],
                )
                continue

        raise LLMError(f"All providers failed. Last error: {last_error}")

    async def _try_provider(
        self,
        url: str,
        query: str,
        timeout: int,
        provider_name: str,
        model: str | None,
    ) -> str:
        """Try sending query to a specific provider."""
        log = logger.bind(
            provider=provider_name,
            url=url,
            model=model,
            query_length=len(query),
        )
        log.info("sending_query_to_llm")

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                payload = {"query": query}
                if model:
                    payload["model"] = model

                response = await client.post(
                    url,
                    json=payload,
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
            raise LLMError(f"Provider {provider_name} timeout after {timeout}s") from e
        except httpx.HTTPStatusError as e:
            log.error("llm_http_error", status=e.response.status_code, error=str(e))
            raise LLMError(f"Provider {provider_name} error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            log.error("llm_connection_error", error=str(e))
            raise LLMError(f"Cannot connect to provider {provider_name}") from e

    async def send_prompt_with_context(self, system_prompt: str, user_content: str) -> str:
        """Send a structured prompt with system context and user content."""
        combined = f"{system_prompt}\n\n---\n\n{user_content}"
        return await self.send_query(combined)


class AgentLLMClient:
    """
    LLM client bound to a specific agent with its configured provider and fallback.
    """

    def __init__(self, agent_name: str):
        """
        Initialize agent-specific LLM client.

        Args:
            agent_name: Name of the agent (matches key in config.json agents section)
        """
        agent_config = settings.get_agent_config(agent_name)
        provider = agent_config.get("provider") or settings.default_provider
        fallback = agent_config.get("fallback")

        fallback_chain = settings.fallback_chain
        if fallback:
            # Prioritize agent's specific fallback
            fallback_chain = [fallback] + [p for p in settings.fallback_chain if p != fallback]

        self._client = LLMClient(provider=provider, fallback_chain=fallback_chain)
        self.agent_name = agent_name

    async def send_query(self, query: str) -> str:
        """Send query using agent's configured provider."""
        return await self._client.send_query(query)

    async def send_prompt_with_context(self, system_prompt: str, user_content: str) -> str:
        """Send structured prompt using agent's configured provider."""
        return await self._client.send_prompt_with_context(system_prompt, user_content)
