import json

from app.agents.base import BaseAgent, AgentError
from app.schemas.business import BusinessProfile, FormInput

import structlog

logger = structlog.get_logger(__name__)


class ParsingValidationError(AgentError):
    """Raised when business profile parsing fails validation."""
    pass


class BusinessParserAgent(BaseAgent):
    """
    Step 1 + Step 2: Convert raw form input into a validated BusinessProfile.
    Step 1: Create clean human-readable summary.
    Step 2: Convert summary into structured JSON.
    """

    SYSTEM_PROMPT_STEP1 = """You are a business analyst. Given raw form data from a user,
create a clean, human-readable summary containing EVERY important piece of information provided.
Rules:
- Remove duplicates, empty fields, and irrelevant noise
- Do NOT invent or assume any information not present in the input
- Preserve all meaningful details the user provided
- Write in clear, professional prose
- Output ONLY the summary text, nothing else"""

    SYSTEM_PROMPT_STEP2 = """You are a data extraction specialist. Given a business summary,
convert it into a structured JSON object matching this schema:
{
  "business_name": "string",
  "idea": "string",
  "industry": "string",
  "products_services": ["string"],
  "target_customers": "string",
  "geography": "string",
  "pricing": "string",
  "business_model": "string",
  "competitors": ["string"],
  "differentiators": "string",
  "research_goals": ["string"],
  "user_query": "string",
  "summary": "string"
}
Rules:
- Extract ONLY information present in the summary
- Use empty string "" for missing fields
- Use empty list [] for missing array fields
- Output ONLY valid JSON, no markdown, no explanation"""

    async def parse(self, form_input: FormInput) -> BusinessProfile:
        """Parse form input into a validated BusinessProfile."""
        log = logger.bind(business=form_input.business_name)
        log.info("starting_business_parsing")

        # Step 1: Create human-readable summary
        raw_data = form_input.model_dump_json(indent=2)
        summary = await self._create_summary(raw_data)
        log.info("step1_summary_created", summary_length=len(summary))

        # Step 2: Convert summary to structured JSON
        profile = await self._structure_profile(summary)
        profile.summary = summary
        log.info("step2_profile_validated", profile_name=profile.business_name)

        return profile

    async def _create_summary(self, raw_data: str) -> str:
        """Step 1: Create clean human-readable summary from raw form data."""
        prompt = self._format_prompt(self.SYSTEM_PROMPT_STEP1, raw_data)
        response = await self._query_llm(prompt)
        summary = response.strip()

        if not summary or len(summary) < 10:
            raise ParsingValidationError("LLM returned empty or too-short summary")

        return summary

    async def _structure_profile(self, summary: str) -> BusinessProfile:
        """Step 2: Convert summary into structured BusinessProfile."""
        prompt = self._format_prompt(self.SYSTEM_PROMPT_STEP2, summary)
        response = await self._query_llm(prompt)

        # Extract JSON from response (handle markdown code blocks)
        json_str = self._extract_json(response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ParsingValidationError(f"LLM returned invalid JSON: {e}") from e

        # Add the summary to the data
        data["summary"] = summary

        try:
            return BusinessProfile(**data)
        except Exception as e:
            raise ParsingValidationError(f"Profile validation failed: {e}") from e

    def _extract_json(self, text: str) -> str:
        """Extract JSON string from LLM response, handling markdown blocks."""
        text = text.strip()

        # Try to find JSON in markdown code blocks
        if "```json" in text:
            start = text.index("```json") + 7
            try:
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                # No closing fence - take everything after opener
                return text[start:].strip()
        elif "```" in text:
            start = text.index("```") + 3
            try:
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                return text[start:].strip()

        # Try to find raw JSON object
        if "{" in text and "}" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            return text[start:end]

        return text
