import json

from app.agents.base import BaseAgent, AgentError

import structlog

logger = structlog.get_logger(__name__)


class DataSummaryAgent(BaseAgent):
    """
    Summarizes raw scraped data into structured, analysis-ready format.
    Called after receiving data from the scraper provider.
    """

    SYSTEM_PROMPT = """You are a data analyst specializing in competitive intelligence.
Given raw web research data, create a concise, structured summary that extracts
the most relevant insights for competitive analysis.

Rules:
- Focus on actionable insights, not raw data dumps
- Remove irrelevant or duplicate information
- Structure the output clearly
- Include source references where available
- Output as structured JSON with clear sections"""

    async def summarize(self, raw_data: dict) -> dict:
        """Summarize raw scraped data into analysis-ready structure."""
        research_type = raw_data.get("research_type", "unknown")
        log = logger.bind(research_type=research_type)
        log.info("summarizing_raw_data")

        data_json = json.dumps(raw_data, indent=2)

        prompt = self._format_prompt(
            self.SYSTEM_PROMPT,
            f"Research type: {research_type}\n\nRaw data:\n{data_json}",
        )

        response = await self._query_llm(prompt)

        # Try to parse as JSON, fall back to structured dict
        try:
            json_str = self._extract_json(response)
            structured = json.loads(json_str)
            return {
                "research_type": research_type,
                "structured": structured,
                "raw": raw_data,
            }
        except (json.JSONDecodeError, ValueError):
            return {
                "research_type": research_type,
                "structured": {"summary": response},
                "raw": raw_data,
            }

    def _extract_json(self, text: str) -> str:
        """Extract JSON from response."""
        text = text.strip()
        if "```json" in text:
            start = text.index("```json") + 7
            try:
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                return text[start:].strip()
        elif "```" in text:
            start = text.index("```") + 3
            try:
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                return text[start:].strip()
        if "{" in text and "}" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            return text[start:end]
        return text
