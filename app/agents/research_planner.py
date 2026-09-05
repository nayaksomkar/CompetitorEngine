from app.agents.base import BaseAgent
from app.schemas.analysis import ResearchPlan, ResearchStep
from app.schemas.business import BusinessProfile

import structlog

logger = structlog.get_logger(__name__)

# Mapping of research goal keywords to research types
GOAL_TO_RESEARCH = {
    "competitor": "competitor_research",
    "competitor_research": "competitor_research",
    "pricing": "pricing_research",
    "pricing_research": "pricing_research",
    "review": "customer_reviews",
    "customer_reviews": "customer_reviews",
    "gap": "market_gap",
    "market_gap": "market_gap",
    "market": "market_gap",
    "product": "competitor_research",
    "strategy": "competitor_research",
}


class ResearchPlannerAgent(BaseAgent):
    """
    Analyzes the business profile and user query to create an execution plan.
    Determines which research steps are needed and their order.
    """

    SYSTEM_PROMPT = """You are a research planning specialist. Given a business profile and research goals,
determine the optimal research plan. Return a JSON object with this structure:
{
  "steps": [
    {
      "name": "step_name",
      "research_type": "competitor_research|pricing_research|customer_reviews|market_gap",
      "requires_research": true,
      "description": "What this step accomplishes"
    }
  ],
  "reasoning": "Brief explanation of why these steps were chosen"
}

Available research types:
- competitor_research: Deep analysis of competitor offerings, positioning, strengths/weaknesses
- pricing_research: Analysis of competitor pricing, market rates, pricing strategies
- customer_reviews: Analysis of customer feedback, satisfaction, common complaints
- market_gap: Identification of unmet needs, underserved segments, opportunities

Rules:
- Only include steps relevant to the user's stated goals
- Order steps logically (competitor research usually comes first)
- Output ONLY valid JSON"""

    async def create_plan(self, profile: BusinessProfile) -> ResearchPlan:
        """Create a research execution plan based on the business profile."""
        log = logger.bind(business=profile.business_name)
        log.info("creating_research_plan", goals=profile.research_goals)

        # First, try rule-based planning from explicit goals
        rule_based_steps = self._rule_based_plan(profile)

        if rule_based_steps:
            return ResearchPlan(
                steps=rule_based_steps,
                reasoning="Plan derived from explicit research goals in user submission",
            )

        # Fall back to LLM-based planning using the user query
        return await self._llm_plan(profile)

    def _rule_based_plan(self, profile: BusinessProfile) -> list[ResearchStep]:
        """Generate plan from explicit research goals."""
        if not profile.research_goals:
            return []

        steps = []
        seen_types = set()

        for goal in profile.research_goals:
            goal_lower = goal.lower().strip()
            for keyword, research_type in GOAL_TO_RESEARCH.items():
                if keyword in goal_lower and research_type not in seen_types:
                    seen_types.add(research_type)
                    steps.append(
                        ResearchStep(
                            name=research_type,
                            research_type=research_type,
                            requires_research=True,
                            description=self._get_step_description(research_type),
                        )
                    )
                    break

        return steps

    async def _llm_plan(self, profile: BusinessProfile) -> ResearchPlan:
        """Use LLM to create a plan when goals aren't explicit."""
        import json

        user_input = f"""
Business: {profile.business_name}
Idea: {profile.idea}
Industry: {profile.industry}
Query: {profile.user_query or "General competitive analysis"}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT, user_input)
        response = await self._query_llm(prompt)

        try:
            # Extract JSON
            text = response.strip()
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

            data = json.loads(text)
            steps = [ResearchStep(**s) for s in data.get("steps", [])]
            return ResearchPlan(steps=steps, reasoning=data.get("reasoning", ""))
        except Exception:
            # Default plan if LLM fails
            return self._default_plan()

    def _default_plan(self) -> ResearchPlan:
        """Default research plan when no goals specified."""
        return ResearchPlan(
            steps=[
                ResearchStep(
                    name="competitor_research",
                    research_type="competitor_research",
                    requires_research=True,
                    description="Research key competitors and their positioning",
                ),
            ],
            reasoning="Default plan: general competitor analysis",
        )

    def _get_step_description(self, research_type: str) -> str:
        descriptions = {
            "competitor_research": "Analyze competitor offerings, strengths, and market positioning",
            "pricing_research": "Analyze competitor pricing and market rate benchmarks",
            "customer_reviews": "Analyze customer feedback and satisfaction patterns",
            "market_gap": "Identify unmet needs and market opportunities",
        }
        return descriptions.get(research_type, f"Research: {research_type}")
