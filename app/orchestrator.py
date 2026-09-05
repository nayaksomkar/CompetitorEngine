import time

import structlog

from app.agents.business_parser import BusinessParserAgent, ParsingValidationError
from app.agents.competitor_analysis import CompetitorAnalysisAgent
from app.agents.data_summary_agent import DataSummaryAgent
from app.agents.report_agent import ReportAgent
from app.agents.research_planner import ResearchPlannerAgent
from app.agents.strategy_agent import StrategyAgent
from app.agents.visualization_agent import VisualizationAgent
from app.schemas.analysis import Insight
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult
from app.services.llm_client import AgentLLMClient
from app.services.scraper_client import get_scraper_provider

logger = structlog.get_logger(__name__)


class Orchestrator:
    """
    Central coordinator for the competitive analysis workflow.
    Manages agent execution and data flow through the pipeline.
    Each agent gets its own LLM client with provider configured from config.json.
    """

    def __init__(
        self,
        llm_client: AgentLLMClient | None = None,
    ):
        # Use injected client for all agents when provided (enables test mocking)
        # Fall back to agent-specific clients from config
        if llm_client:
            self.llm = llm_client
            self.business_parser = BusinessParserAgent(llm_client)
            self.research_planner = ResearchPlannerAgent(llm_client)
            self.data_summarizer = DataSummaryAgent(llm_client)
            self.competitor_analyzer = CompetitorAnalysisAgent(llm_client)
            self.visualizer = VisualizationAgent(llm_client)
            self.strategist = StrategyAgent(llm_client)
            self.reporter = ReportAgent(llm_client)
        else:
            self.llm = AgentLLMClient("default")
            self.business_parser = BusinessParserAgent(
                AgentLLMClient("business_parser")
            )
            self.research_planner = ResearchPlannerAgent(
                AgentLLMClient("research_planner")
            )
            self.data_summarizer = DataSummaryAgent(
                AgentLLMClient("research_planner")
            )
            self.competitor_analyzer = CompetitorAnalysisAgent(
                AgentLLMClient("competitor_analysis")
            )
            self.visualizer = VisualizationAgent(
                AgentLLMClient("visualization")
            )
            self.strategist = StrategyAgent(
                AgentLLMClient("strategy")
            )
            self.reporter = ReportAgent(
                AgentLLMClient("report")
            )

        # Scraper service
        self.scraper = get_scraper_provider()

    async def run_analysis(self, form_input: FormInput) -> AnalysisResult:
        """
        Execute the complete analysis pipeline.

        Pipeline:
        1. Parse business form -> BusinessProfile
        2. Create research plan
        3. Execute research steps (scraper -> summarize)
        4. Competitor analysis
        5. Strategy generation (SWOT, recommendations, action plan)
        6. Visualization generation
        7. Compile final report
        """
        start_time = time.time()
        log = logger.bind(business=form_input.business_name)
        log.info("analysis_started")

        try:
            # Step 1 + 2: Parse business form into structured profile
            profile = await self.business_parser.parse(form_input)
            business_summary = profile.summary
            log.info("business_parsed", profile_name=profile.business_name)

            # Step 3: Create research execution plan
            plan = await self.research_planner.create_plan(profile)
            log.info("research_plan_created", steps=len(plan.steps))

            # Step 4: Execute research steps
            context: dict = {"profile": profile, "plan": plan}
            for step in plan.steps:
                if step.requires_research:
                    log.info("executing_research_step", step=step.name)
                    raw_data = await self.scraper.fetch(
                        step.research_type,
                        profile.model_dump(),
                    )
                    structured = await self.data_summarizer.summarize(raw_data)
                    context[step.name] = structured

            # Step 5: Competitor analysis
            competitors = await self.competitor_analyzer.analyze(profile, context)
            log.info("competitor_analysis_done", count=len(competitors))

            # Step 6: Strategy generation
            swot, recommendations, action_plan = await self.strategist.generate_strategy(
                profile, context, competitors
            )
            log.info("strategy_generated")

            # Step 7: Visualization generation
            charts, comparisons = await self.visualizer.create_visualizations(
                competitors, context, business_summary
            )
            log.info("visualizations_created")

            # Step 8: Generate insights from all data
            insights = self._generate_insights(profile, competitors, swot, context)

            # Step 9: Compile final report
            processing_time_ms = int((time.time() - start_time) * 1000)
            result = await self.reporter.compile(
                profile=profile,
                business_summary=business_summary,
                competitors=competitors,
                swot=swot,
                comparisons=comparisons,
                charts=charts,
                insights=insights,
                recommendations=recommendations,
                action_plan=action_plan,
                research_context=context,
                processing_time_ms=processing_time_ms,
            )

            log.info(
                "analysis_complete",
                processing_time_ms=processing_time_ms,
                competitors=len(competitors),
                recommendations=len(recommendations),
            )
            return result

        except ParsingValidationError:
            raise
        except Exception as e:
            log.error("analysis_failed", error=str(e))
            raise

    def _generate_insights(
        self,
        profile,
        competitors,
        swot,
        context,
    ) -> list[Insight]:
        """Generate key insights from all analysis data."""
        insights = []

        # Market gap insights
        if "market_gap" in context:
            gap_data = context["market_gap"]
            structured = gap_data.get("structured", {})
            gaps = structured.get("gaps_identified", [])
            for gap in gaps[:3]:
                if isinstance(gap, dict):
                    insights.append(Insight(
                        title=f"Market Gap: {gap.get('gap', 'Unknown')[:60]}",
                        description=gap.get("gap", ""),
                        importance="high" if gap.get("opportunity_size") == "high" else "medium",
                        source=gap.get("source", "market_research"),
                        explanation="This gap represents an unmet need your business could address",
                    ))

        # Competitive insights
        if competitors:
            insights.append(Insight(
                title=f"Identified {len(competitors)} key competitors",
                description="Direct competitors analyzed for positioning, strengths, and weaknesses",
                importance="high",
                source="competitor_research",
                explanation="Understanding competitor landscape is essential for differentiation",
            ))

        # SWOT-based insights
        if swot.opportunities:
            for opp in swot.opportunities[:2]:
                insights.append(Insight(
                    title=f"Opportunity: {opp.point[:60]}",
                    description=opp.point,
                    importance="high",
                    source=opp.source or "swot_analysis",
                    explanation=opp.explanation or "Strategic opportunity identified from analysis",
                ))

        return insights


async def run_analysis(form_input: FormInput) -> AnalysisResult:
    """Convenience function to run analysis with default orchestrator."""
    orchestrator = Orchestrator()
    return await orchestrator.run_analysis(form_input)
