import structlog

from app.services.llm_client import AgentLLMClient

logger = structlog.get_logger(__name__)


class AgentError(Exception):
    """Base exception for agent failures."""
    pass


class BaseAgent:
    """
    Base class for all logical agents.
    Provides LLM client access with agent-specific provider configuration.
    """

    def __init__(self, llm_client: AgentLLMClient | None = None):
        self.llm = llm_client or AgentLLMClient(agent_name=self.__class__.__name__)
        self.logger = logger.bind(agent=self.__class__.__name__)

    async def _query_llm(self, prompt: str) -> str:
        """Send a prompt to the LLM and return the response."""
        self.logger.debug("querying_llm", prompt_preview=prompt[:100])
        return await self.llm.send_query(prompt)

    def _format_prompt(self, system: str, user: str) -> str:
        """Format a two-part prompt for the LLM."""
        return f"{system}\n\n---\n\nUSER INPUT:\n{user}\n\n---\n\nRESPONSE:"
