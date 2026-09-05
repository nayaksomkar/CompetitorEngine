import json

from app.agents.base import BaseAgent
from app.schemas.analysis import ChartData, ComparisonTable, ComparisonRow

import structlog

logger = structlog.get_logger(__name__)


class VisualizationAgent(BaseAgent):
    """
    Decides which charts and comparisons to generate based on analysis data.
    Returns structured visualization specs the frontend can render.
    """

    SYSTEM_PROMPT = """You are a data visualization specialist.
Given competitive analysis data, determine the most useful charts and comparisons
to help the user understand the competitive landscape.

Generate output as JSON with two sections:
{
  "charts": [
    {
      "chart_type": "bar|pie|radar",
      "title": "Chart title",
      "labels": ["label1", "label2"],
      "datasets": [{"label": "Series", "data": [1, 2, 3]}],
      "explanation": "What this chart shows and why it matters"
    }
  ],
  "comparisons": [
    {
      "title": "Comparison title",
      "entities": ["Entity1", "Entity2"],
      "rows": [
        {"feature": "Feature Name", "values": {"Entity1": "val1", "Entity2": "val2"}}
      ],
      "explanation": "Why this comparison is useful"
    }
  ]
}

Chart types available: bar, pie, radar
Rules:
- Only suggest visualizations that the data actually supports
- Each visualization must have a clear purpose and explanation
- Output ONLY valid JSON"""

    async def create_visualizations(
        self,
        competitors: list,
        research_context: dict,
        profile_summary: str = "",
    ) -> tuple[list[ChartData], list[ComparisonTable]]:
        """Generate chart and comparison specs from analysis data."""
        log = logger.bind(competitor_count=len(competitors))
        log.info("creating_visualizations")

        if not competitors:
            return [], []

        user_input = f"""
Business: {profile_summary}

Competitors:
{json.dumps([c.model_dump() if hasattr(c, 'model_dump') else c for c in competitors], indent=2)}

Research Data Keys: {list(research_context.keys())}
"""
        prompt = self._format_prompt(self.SYSTEM_PROMPT, user_input)
        response = await self._query_llm(prompt)

        charts, comparisons = self._parse_visualization_response(response, competitors)

        # If LLM failed, generate default visualizations
        if not charts and not comparisons:
            charts, comparisons = self._generate_defaults(competitors)

        log.info("visualizations_created", charts=len(charts), comparisons=len(comparisons))
        return charts, comparisons

    def _parse_visualization_response(
        self, response: str, competitors: list
    ) -> tuple[list[ChartData], list[ComparisonTable]]:
        """Parse LLM response into chart and comparison objects."""
        text = response.strip()

        try:
            if "```" in text:
                start = text.index("```") + 3
                if text[start:start+4] == "json":
                    start += 4
                end = text.index("```", start)
                text = text[start:end].strip()

            if "{" in text and "}" in text:
                start = text.index("{")
                end = text.rindex("}") + 1
                text = text[start:end]

            data = json.loads(text)

            charts = [ChartData(**c) for c in data.get("charts", [])]
            comparisons = [ComparisonTable(**c) for c in data.get("comparisons", [])]
            return charts, comparisons

        except (json.JSONDecodeError, ValueError):
            return [], []

    def _generate_defaults(
        self, competitors: list
    ) -> tuple[list[ChartData], list[ComparisonTable]]:
        """Generate default visualizations when LLM fails."""
        names = []
        for c in competitors:
            if hasattr(c, "name"):
                names.append(c.name)
            elif isinstance(c, dict):
                names.append(c.get("name", "Unknown"))

        if not names:
            return [], []

        # Default comparison table
        comparison = ComparisonTable(
            title="Competitor Comparison",
            entities=names,
            rows=[
                ComparisonRow(feature="Market Position", values={n: "Active" for n in names}),
            ],
            explanation="Basic competitive landscape overview",
        )

        # Default bar chart
        chart = ChartData(
            chart_type="bar",
            title="Competitor Strengths Count",
            labels=names,
            datasets=[{
                "label": "Key Strengths",
                "data": [
                    len(c.strengths) if hasattr(c, "strengths") and isinstance(c.strengths, list) else 1
                    for c in competitors
                ],
            }],
            explanation="Number of identified strengths per competitor",
        )

        return [chart], [comparison]
