import json

from app.agents.base import BaseAgent
from app.schemas.analysis import (
    SWOTAnalysis,
    SWOTItem,
    Recommendation,
    ActionItem,
)
from app.schemas.business import BusinessProfile

import structlog

logger = structlog.get_logger(__name__)


class StrategyAgent(BaseAgent):
    """
    Generates strategic insights: SWOT analysis, recommendations, action plans.
    Each output includes explanation fields for frontend "Explain This" feature.
    """

    SYSTEM_PROMPT_SWOT = """You are a strategic business analyst.
Given a business profile and competitive research, generate a SWOT analysis.

Return JSON:
{
  "strengths": [{"point": "...", "explanation": "Why this is a strength", "source": "..."}],
  "weaknesses": [{"point": "...", "explanation": "Why this is a weakness", "source": "..."}],
  "opportunities": [{"point": "...", "explanation": "Why this is an opportunity", "source": "..."}],
  "threats": [{"point": "...", "explanation": "Why this is a threat", "source": "..."}]
}

Rules:
- Each point must have a clear explanation connecting to the user's specific situation
- Base analysis on provided data, not assumptions
- Be honest about weaknesses and threats
- Output ONLY valid JSON"""

    SYSTEM_PROMPT_RECOMMENDATIONS = """You are a business strategy advisor.
Given a business profile, competitor analysis, and SWOT, generate actionable recommendations.

Return JSON array:
[
  {
    "title": "Recommendation title",
    "description": "Detailed description of what to do",
    "priority": "high|medium|low",
    "rationale": "Why this recommendation makes sense",
    "explanation": "Plain English explanation of expected outcome and why it matters"
  }
]

Rules:
- Prioritize high-impact, achievable recommendations
- Each must connect to SWOT findings or competitive gaps
- Output ONLY valid JSON array"""

    SYSTEM_PROMPT_ACTION_PLAN = """You are a business consultant creating an action plan.
Given recommendations, create a prioritized timeline of specific actions.

Return JSON array:
[
  {
    "action": "Specific action to take",
    "timeline": "Week 1-2|Month 1|Month 2-3|etc",
    "priority": "high|medium|low",
    "expected_outcome": "What success looks like",
    "explanation": "Why this action matters and how it connects to strategy"
  }
]

Rules:
- Actions should be concrete and measurable
- Timeline should be realistic
- Order by priority
- Output ONLY valid JSON array"""

    async def generate_strategy(
        self,
        profile: BusinessProfile,
        research_context: dict,
        competitors: list,
    ) -> tuple[SWOTAnalysis, list[Recommendation], list[ActionItem]]:
        """Generate complete strategy output."""
        log = logger.bind(business=profile.business_name)
        log.info("generating_strategy")

        # Generate SWOT
        swot = await self._generate_swot(profile, competitors, research_context)
        log.info("swot_generated")

        # Generate recommendations
        recommendations = await self._generate_recommendations(profile, swot, competitors)
        log.info("recommendations_generated", count=len(recommendations))

        # Generate action plan
        action_plan = await self._generate_action_plan(recommendations, profile)
        log.info("action_plan_generated", count=len(action_plan))

        return swot, recommendations, action_plan

    async def _generate_swot(
        self,
        profile: BusinessProfile,
        competitors: list,
        context: dict,
    ) -> SWOTAnalysis:
        """Generate SWOT analysis."""
        user_input = f"""
Business: {profile.business_name}
Idea: {profile.idea}
Differentiators: {profile.differentiators}
Target Customers: {profile.target_customers}

Competitors: {[c.name if hasattr(c, 'name') else c.get('name', 'Unknown') for c in competitors]}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT_SWOT, user_input)
        response = await self._query_llm(prompt)

        try:
            data = self._extract_json_dict(response)
            return SWOTAnalysis(
                strengths=[SWOTItem(**s) for s in data.get("strengths", [])],
                weaknesses=[SWOTItem(**w) for w in data.get("weaknesses", [])],
                opportunities=[SWOTItem(**o) for o in data.get("opportunities", [])],
                threats=[SWOTItem(**t) for t in data.get("threats", [])],
            )
        except Exception:
            return self._default_swot(profile)

    async def _generate_recommendations(
        self,
        profile: BusinessProfile,
        swot: SWOTAnalysis,
        competitors: list,
    ) -> list[Recommendation]:
        """Generate strategic recommendations."""
        user_input = f"""
Business: {profile.business_name}
User Query: {profile.user_query}

SWOT Summary:
Strengths: {[s.point for s in swot.strengths]}
Weaknesses: {[w.point for w in swot.weaknesses]}
Opportunities: {[o.point for o in swot.opportunities]}
Threats: {[t.point for t in swot.threats]}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT_RECOMMENDATIONS, user_input)
        response = await self._query_llm(prompt)

        try:
            data = self._extract_json_list(response)
            return [Recommendation(**r) for r in data]
        except Exception:
            return self._default_recommendations(profile)

    async def _generate_action_plan(
        self,
        recommendations: list[Recommendation],
        profile: BusinessProfile,
    ) -> list[ActionItem]:
        """Generate action plan from recommendations."""
        recs_summary = "\n".join(
            f"- [{r.priority}] {r.title}: {r.description}" for r in recommendations[:5]
        )

        user_input = f"""
Business: {profile.business_name}

Top Recommendations:
{recs_summary}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT_ACTION_PLAN, user_input)
        response = await self._query_llm(prompt)

        try:
            data = self._extract_json_list(response)
            return [ActionItem(**a) for a in data]
        except Exception:
            return self._default_action_plan(recommendations)

    def _extract_json_dict(self, text: str) -> dict:
        """Extract JSON object from text."""
        text = text.strip()
        if "```" in text:
            start = text.index("```") + 3
            if text[start:start+4] == "json":
                start += 4
            try:
                end = text.index("```", start)
                text = text[start:end].strip()
            except ValueError:
                text = text[start:].strip()
        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            text = text[start:end]
        return json.loads(text)

    def _extract_json_list(self, text: str) -> list:
        """Extract JSON array from text."""
        text = text.strip()
        if "```" in text:
            start = text.index("```") + 3
            if text[start:start+4] == "json":
                start += 4
            try:
                end = text.index("```", start)
                text = text[start:end].strip()
            except ValueError:
                text = text[start:].strip()
        if "[" in text:
            start = text.index("[")
            end = text.rindex("]") + 1
            text = text[start:end]
        return json.loads(text)

    def _default_swot(self, profile: BusinessProfile) -> SWOTAnalysis:
        """Generate default SWOT when LLM fails."""
        return SWOTAnalysis(
            strengths=[SWOTItem(
                point="Clear differentiators identified",
                explanation="Business has defined what sets it apart from competitors",
                source="user_input",
            )],
            opportunities=[SWOTItem(
                point="Market gaps exist in current landscape",
                explanation="Competitor analysis reveals unmet customer needs",
                source="market_research",
            )],
        )

    def _default_recommendations(self, profile: BusinessProfile) -> list[Recommendation]:
        """Generate default recommendations."""
        return [Recommendation(
            title="Validate differentiators with target customers",
            description="Conduct customer interviews to confirm your differentiators resonate",
            priority="high",
            explanation="Ensures your unique value aligns with what customers actually want",
        )]

    def _default_action_plan(self, recommendations: list[Recommendation]) -> list[ActionItem]:
        """Generate default action plan."""
        return [ActionItem(
            action=f"Pursue: {rec.title}",
            timeline="Month 1",
            priority=rec.priority,
            expected_outcome="Progress on key strategic initiative",
            explanation=rec.explanation,
        ) for rec in recommendations[:3]]
