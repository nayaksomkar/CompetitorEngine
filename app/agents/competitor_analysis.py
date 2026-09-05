import json

from app.agents.base import BaseAgent
from app.schemas.analysis import CompetitorCard
from app.schemas.business import BusinessProfile

import structlog

logger = structlog.get_logger(__name__)


class CompetitorAnalysisAgent(BaseAgent):
    """
    Performs deep competitor analysis using data from research.
    Produces competitor cards with strengths, weaknesses, positioning, and explanations.
    """

    SYSTEM_PROMPT = """You are a competitive intelligence analyst.
Given a business profile and research data about competitors, perform a deep analysis.

For each competitor, provide:
1. Clear description of their offering
2. Key strengths (with evidence from data)
3. Key weaknesses (with evidence from data)
4. Pricing analysis
5. Market positioning
6. Explanation of why they matter to the user's specific business

Return JSON array of competitor objects:
[
  {
    "name": "CompetitorName",
    "description": "Brief description",
    "strengths": ["strength1", "strength2"],
    "weaknesses": ["weakness1", "weakness2"],
    "pricing": "Pricing details",
    "market_position": "Their market position",
    "source": "Where this info came from",
    "explanation": "Why this competitor is relevant to the user's business"
  }
]

Rules:
- Base analysis ONLY on provided data
- Include explanations that connect to the user's specific situation
- Output ONLY valid JSON array"""

    async def analyze(
        self,
        profile: BusinessProfile,
        research_context: dict,
    ) -> list[CompetitorCard]:
        """Analyze competitors and produce structured competitor cards."""
        log = logger.bind(business=profile.business_name)
        log.info("analyzing_competitors")

        # Extract competitor data from context
        competitor_data = self._extract_competitor_data(research_context)

        if not competitor_data:
            log.info("no_competitor_data_available")
            return []

        # Prepare prompt
        user_input = f"""
Business: {profile.business_name}
Idea: {profile.idea}
Differentiators: {profile.differentiators}
User Query: {profile.user_query}

Competitor Research Data:
{json.dumps(competitor_data, indent=2)}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT, user_input)
        response = await self._query_llm(prompt)

        # Parse response
        cards = self._parse_competitor_cards(response, competitor_data)
        log.info("competitor_analysis_complete", count=len(cards))
        return cards

    def _extract_competitor_data(self, context: dict) -> list[dict]:
        """Extract competitor info from research context."""
        results = []

        # Look for competitor_research in context
        for key, value in context.items():
            if isinstance(value, dict):
                if value.get("research_type") == "competitor_research":
                    results.extend(value.get("raw", {}).get("competitors", []))
                elif "competitor" in key.lower() and "competitors" in value:
                    comps = value.get("competitors", [])
                    if isinstance(comps, list):
                        results.extend(comps)

        # Also check research context for pricing data
        for key, value in context.items():
            if isinstance(value, dict) and value.get("research_type") == "pricing_research":
                pricing = value.get("raw", {}).get("pricing_data", [])
                # Merge pricing into competitor results
                for p in pricing:
                    for r in results:
                        if r.get("name") == p.get("competitor"):
                            r["pricing_detail"] = p.get("plans", [])

        return results

    def _parse_competitor_cards(
        self, response: str, fallback_data: list[dict]
    ) -> list[CompetitorCard]:
        """Parse LLM response into CompetitorCard objects."""
        text = response.strip()

        # Extract JSON array
        try:
            if "```" in text:
                start = text.index("```") + 3
                if text[start:start+4] == "json":
                    start += 4
                end = text.index("```", start)
                text = text[start:end].strip()

            if "[" in text and "]" in text:
                start = text.index("[")
                end = text.rindex("]") + 1
                text = text[start:end]

            data = json.loads(text)
            if isinstance(data, list):
                return [CompetitorCard(**c) for c in data]
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: create cards from raw data
        return [
            CompetitorCard(
                name=c.get("name", "Unknown"),
                description=c.get("description", ""),
                strengths=c.get("strengths", []),
                weaknesses=c.get("weaknesses", []),
                pricing=c.get("pricing", c.get("pricing_range", "")),
                market_position=c.get("market_position", ""),
                source=c.get("source", "research_data"),
                explanation="Based on competitive research data",
            )
            for c in fallback_data
        ]
