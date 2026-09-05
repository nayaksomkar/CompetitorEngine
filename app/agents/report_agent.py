from datetime import datetime, timezone

from app.agents.base import BaseAgent
from app.schemas.analysis import (
    CompetitorCard,
    SWOTAnalysis,
    ComparisonTable,
    ChartData,
    Insight,
    Recommendation,
    ActionItem,
)
from app.schemas.business import BusinessProfile
from app.schemas.output import AnalysisResult, SourceReference, Metadata

import structlog

logger = structlog.get_logger(__name__)


class ReportAgent(BaseAgent):
    """
    Assembles all analysis outputs into a single frontend-ready AnalysisResult.
    Generates the final text report and collects all sources.
    """

    SYSTEM_PROMPT_REPORT = """You are a business report writer.
Given a complete competitive analysis, write a comprehensive executive report.

The report should include:
1. Executive Summary (2-3 sentences)
2. Business Overview
3. Competitive Landscape
4. Key Findings
5. Strategic Recommendations
6. Next Steps

Write in clear, professional business language.
Do NOT invent information not present in the data."""

    async def compile(
        self,
        profile: BusinessProfile,
        business_summary: str,
        competitors: list[CompetitorCard],
        swot: SWOTAnalysis,
        comparisons: list[ComparisonTable],
        charts: list[ChartData],
        insights: list[Insight],
        recommendations: list[Recommendation],
        action_plan: list[ActionItem],
        research_context: dict,
        processing_time_ms: int = 0,
    ) -> AnalysisResult:
        """Compile all analysis components into final output."""
        log = logger.bind(business=profile.business_name)
        log.info("compiling_final_report")

        # Generate full text report
        report = await self._generate_report(
            profile, business_summary, competitors, swot, recommendations
        )

        # Collect all sources
        sources = self._collect_sources(
            competitors, swot, insights, research_context
        )

        # Build metadata
        metadata = Metadata(
            generated_at=datetime.now(timezone.utc).isoformat(),
            model_used="llm-brain",
            confidence=self._calculate_confidence(competitors, swot, recommendations),
            processing_time_ms=processing_time_ms,
        )

        result = AnalysisResult(
            business_summary=business_summary,
            profile=profile,
            competitors=competitors,
            swot=swot,
            comparisons=comparisons,
            charts=charts,
            insights=insights,
            recommendations=recommendations,
            action_plan=action_plan,
            report=report,
            sources=sources,
            metadata=metadata,
        )

        log.info("report_compiled", sources=len(sources))
        return result

    async def _generate_report(
        self,
        profile: BusinessProfile,
        summary: str,
        competitors: list[CompetitorCard],
        swot: SWOTAnalysis,
        recommendations: list[Recommendation],
    ) -> str:
        """Generate the full text report."""
        comp_section = "\n".join(
            f"- {c.name}: {c.description} (Strengths: {', '.join(c.strengths[:2])})"
            for c in competitors[:5]
        )

        rec_section = "\n".join(
            f"{i+1}. [{r.priority.upper()}] {r.title}\n   {r.description}"
            for i, r in enumerate(recommendations[:5])
        )

        user_input = f"""
Business: {profile.business_name}
Summary: {summary}

Competitors Identified:
{comp_section}

SWOT:
Strengths: {', '.join(s.point for s in swot.strengths[:5])}
Weaknesses: {', '.join(w.point for w in swot.weaknesses[:5])}
Opportunities: {', '.join(o.point for o in swot.opportunities[:5])}
Threats: {', '.join(t.point for t in swot.threats[:5])}

Top Recommendations:
{rec_section}
"""
        try:
            prompt = self._format_prompt(self.SYSTEM_PROMPT_REPORT, user_input)
            report = await self._query_llm(prompt)
            return report.strip()
        except Exception:
            return self._fallback_report(profile, summary, competitors, recommendations)

    def _fallback_report(
        self,
        profile: BusinessProfile,
        summary: str,
        competitors: list[CompetitorCard],
        recommendations: list[Recommendation],
    ) -> str:
        """Generate a simple report if LLM fails."""
        lines = [
            f"# Competitive Analysis Report: {profile.business_name}",
            "",
            "## Executive Summary",
            summary,
            "",
            "## Competitive Landscape",
        ]
        for c in competitors:
            lines.append(f"- **{c.name}**: {c.description}")
        lines.extend(["", "## Recommendations"])
        for i, r in enumerate(recommendations, 1):
            lines.append(f"{i}. **{r.title}**: {r.description}")
        return "\n".join(lines)

    def _collect_sources(
        self,
        competitors: list[CompetitorCard],
        swot: SWOTAnalysis,
        insights: list[Insight],
        context: dict,
    ) -> list[SourceReference]:
        """Collect all source references from analysis components."""
        sources = []

        # Competitor sources
        for c in competitors:
            if c.source:
                sources.append(SourceReference(
                    source=c.source,
                    type="web",
                    relevance=f"Competitor data: {c.name}",
                ))

        # SWOT sources
        for items in [swot.strengths, swot.weaknesses, swot.opportunities, swot.threats]:
            for item in items:
                if item.source:
                    sources.append(SourceReference(
                        source=item.source,
                        type="analysis",
                        relevance=item.point,
                    ))

        # Insight sources
        for insight in insights:
            if insight.source:
                sources.append(SourceReference(
                    source=insight.source,
                    type="web",
                    relevance=insight.title,
                ))

        # Context sources (from scraper)
        for key, value in context.items():
            if isinstance(value, dict):
                raw = value.get("raw", {})
                if "source" in raw:
                    sources.append(SourceReference(
                        source=raw["source"],
                        type="web",
                        relevance=key,
                    ))

        # Deduplicate
        seen = set()
        unique_sources = []
        for s in sources:
            key = (s.source, s.relevance)
            if key not in seen:
                seen.add(key)
                unique_sources.append(s)

        return unique_sources

    def _calculate_confidence(
        self,
        competitors: list,
        swot: SWOTAnalysis,
        recommendations: list,
    ) -> float:
        """Calculate overall confidence score based on data completeness."""
        score = 0.0
        total = 0.0

        # Competitor coverage
        total += 1.0
        if len(competitors) >= 2:
            score += 1.0
        elif len(competitors) >= 1:
            score += 0.5

        # SWOT completeness
        swot_items = (
            len(swot.strengths) + len(swot.weaknesses) +
            len(swot.opportunities) + len(swot.threats)
        )
        total += 1.0
        if swot_items >= 4:
            score += 1.0
        elif swot_items >= 2:
            score += 0.6

        # Recommendations
        total += 1.0
        if len(recommendations) >= 3:
            score += 1.0
        elif len(recommendations) >= 1:
            score += 0.5

        return round(score / total, 2) if total > 0 else 0.0
