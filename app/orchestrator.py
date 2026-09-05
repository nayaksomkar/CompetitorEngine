"""
CompetitorEngine — pure orchestrator.

This module does no reasoning, no prompting, no scraping. Its only
job is to:
  1. Decide what information the user's request needs.
  2. Call WebHunter for fresh external research when needed.
  3. Call LLMPing to reason over that context.
  4. Validate and normalize the response into a clean
     frontend-ready shape.
"""
import time
import uuid
from typing import Any

import structlog

from app.schemas.analysis import (
    ActionItem,
    ChartData,
    CompetitorCard,
    Recommendation,
    SWOTAnalysis,
    SWOTItem,
)
from app.schemas.business import BusinessProfile, FormInput
from app.schemas.output import (
    AnalysisResult,
    ChatResponse,
    Metadata,
    MetricCard,
    SourceReference,
)
from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import WebHunterClient, WebHunterError

logger = structlog.get_logger(__name__)


# What the Overview analysis is required to return from LLMPing.
OVERVIEW_REQUIRED_OUTPUTS = [
    "executive_summary",
    "market_info",
    "competitors",
    "positioning",
    "swot",
    "comparisons",
    "gaps",
    "opportunities",
    "risks",
    "recommendations",
    "action_plan",
    "visualizations",
]


class Orchestrator:
    """Stateless orchestration layer. Safe to construct per request."""

    def __init__(
        self,
        llmping: LLMPingClient | None = None,
        webhunter: WebHunterClient | None = None,
    ):
        self.llmping = llmping or LLMPingClient()
        self.webhunter = webhunter or WebHunterClient()

    # ── Overview ───────────────────────────────────────────────
    async def run_overview(self, form_input: FormInput) -> AnalysisResult:
        """
        Full competitive analysis pipeline:
          WebHunter (fresh research) → LLMPing (synthesis) →
          validate → AnalysisResult.
        """
        start = time.time()
        log = logger.bind(business=form_input.business_name)
        log.info("overview_started")

        business = form_input.model_dump()

        # Decide what research areas to request from WebHunter.
        research_types = self._plan_research(form_input)
        log.info("research_planned", types=research_types)

        # 1. External research.
        research: dict[str, Any] = {}
        if research_types:
            try:
                research = await self.webhunter.research(
                    business=business,
                    research_types=research_types,
                )
            except WebHunterError as e:
                # Never let a research failure kill the analysis —
                # LLMPing can still synthesize from context alone.
                log.warning("webhunter_failed_continuing", error=str(e))
                research = {}

        # 2. Reasoning over the gathered context.
        payload = {
            "task": "full_analysis",
            "session_id": str(uuid.uuid4()),
            "context": {
                "business": business,
                "research": research,
            },
            "required_outputs": OVERVIEW_REQUIRED_OUTPUTS,
        }

        try:
            llm_response = await self.llmping.chat(payload)
        except LLMPingError:
            log.error("llmping_failed")
            raise

        # 3. Validate + normalize → AnalysisResult.
        result = self._build_analysis_result(
            form_input=form_input,
            llm_response=llm_response,
            research=research,
            processing_time_ms=int((time.time() - start) * 1000),
        )

        log.info(
            "overview_complete",
            processing_time_ms=result.metadata.processing_time_ms,
            competitors=len(result.competitors),
            recommendations=len(result.recommendations),
        )
        return result

    # ── Chat follow-ups ────────────────────────────────────────
    async def chat(
        self,
        session_id: str | None,
        message: str,
        current_analysis: dict[str, Any] | None = None,
        fresh_research: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """
        Conversational follow-up. Decides whether to call WebHunter
        for fresh research or just hand the existing context to
        LLMPing. Sessions are owned by LLMPing — we just forward
        the session_id.
        """
        sid = session_id or str(uuid.uuid4())
        log = logger.bind(session_id=sid)
        log.info("chat_started")

        # 1. Ask LLMPing whether new research is required.
        decision_payload = {
            "task": "decide_research_need",
            "session_id": sid,
            "message": message,
            "current_context": current_analysis,
        }
        decision: dict[str, Any] = {}
        try:
            decision = await self.llmping.chat(decision_payload)
        except LLMPingError as e:
            log.warning("llmping_decide_failed", error=str(e))

        needs_research = bool(decision.get("needs_research"))
        research_types = decision.get("research_plan") or []
        include_viz = bool(decision.get("wants_visualizations"))

        # 2. Optionally call WebHunter.
        research_data = fresh_research or {}
        if needs_research and research_types and not fresh_research:
            try:
                research_data = await self.webhunter.research(
                    business=(current_analysis or {}).get("profile") or {},
                    research_types=research_types,
                )
            except WebHunterError as e:
                log.warning("webhunter_chat_failed", error=str(e))
                research_data = {}

        # 3. Get the answer from LLMPing.
        answer_payload = {
            "task": "answer_question",
            "session_id": sid,
            "message": message,
            "current_context": current_analysis,
            "fresh_research": research_data,
            "include_visualizations": include_viz,
        }
        llm_response = await self.llmping.chat(answer_payload)

        # 4. Normalize to ChatResponse.
        return ChatResponse(
            session_id=sid,
            answer=str(llm_response.get("answer", "")),
            mini_charts=self._sanitize_charts(
                llm_response.get("charts") or llm_response.get("visualizations")
            ),
            metric_cards=self._sanitize_metric_cards(
                llm_response.get("metric_cards")
            ),
            sources=self._sanitize_sources(
                llm_response.get("sources"), research_data
            ),
        )

    # ── Internals ──────────────────────────────────────────────
    def _plan_research(self, form_input: FormInput) -> list[str]:
        """Decide which research areas to request from WebHunter."""
        # Honor explicit user goals if present, otherwise fall back
        # to a sensible default set.
        explicit = [
            g.strip()
            for g in (form_input.research_goals or [])
            if g.strip()
        ]
        if explicit:
            return explicit

        default = [
            "competitor_research",
            "pricing_research",
            "customer_reviews",
            "market_gap",
        ]
        # If the user mentioned competitors, narrow to competitor +
        # pricing to save WebHunter calls.
        if form_input.competitors:
            return ["competitor_research", "pricing_research"]
        return default

    def _build_analysis_result(
        self,
        form_input: FormInput,
        llm_response: dict[str, Any],
        research: dict[str, Any],
        processing_time_ms: int,
    ) -> AnalysisResult:
        """Validate LLMPing's response and assemble AnalysisResult."""
        missing = [
            k for k in OVERVIEW_REQUIRED_OUTPUTS if k not in llm_response
        ]
        if missing:
            raise LLMPingError(
                f"LLMPing response missing required keys: {missing}"
            )

        # Build the BusinessProfile from the form (LLMPing is not
        # allowed to invent fields that contradict the user's input).
        profile = BusinessProfile(
            business_name=form_input.business_name,
            idea=form_input.idea,
            industry=form_input.industry,
            products_services=form_input.products_services or [],
            target_customers=form_input.target_customers or "",
            geography=form_input.geography or "",
            pricing=form_input.pricing or "",
            business_model=form_input.business_model or "",
            competitors=form_input.competitors or [],
            differentiators=form_input.differentiators or "",
            research_goals=form_input.research_goals or [],
            user_query=form_input.user_query or "",
            summary=str(llm_response.get("business_summary") or ""),
        )

        charts = self._sanitize_charts(llm_response.get("visualizations"))
        metric_cards = self._sanitize_metric_cards(
            llm_response.get("metric_cards")
        )

        # SWOT from LLMPing must be a dict with strengths/weaknesses/
        # opportunities/threats lists.
        swot_raw = llm_response.get("swot") or {}
        swot = SWOTAnalysis(
            strengths=[SWOTItem(**s) for s in swot_raw.get("strengths", []) if isinstance(s, dict)],
            weaknesses=[SWOTItem(**w) for w in swot_raw.get("weaknesses", []) if isinstance(w, dict)],
            opportunities=[SWOTItem(**o) for o in swot_raw.get("opportunities", []) if isinstance(o, dict)],
            threats=[SWOTItem(**t) for t in swot_raw.get("threats", []) if isinstance(t, dict)],
        )

        competitors = [
            CompetitorCard(**c)
            for c in llm_response.get("competitors", [])
            if isinstance(c, dict)
        ]
        recommendations = [
            Recommendation(**r)
            for r in llm_response.get("recommendations", [])
            if isinstance(r, dict)
        ]
        action_plan = [
            ActionItem(**a)
            for a in llm_response.get("action_plan", [])
            if isinstance(a, dict)
        ]

        return AnalysisResult(
            business_summary=str(llm_response.get("business_summary") or ""),
            profile=profile,
            executive_summary=str(llm_response.get("executive_summary") or ""),
            market_info=llm_response.get("market_info") or {},
            positioning=str(llm_response.get("positioning") or ""),
            gaps=[str(g) for g in llm_response.get("gaps", []) or []],
            opportunities=[str(o) for o in llm_response.get("opportunities", []) or []],
            risks=[str(r) for r in llm_response.get("risks", []) or []],
            competitors=competitors,
            swot=swot,
            charts=charts,
            metric_cards=metric_cards,
            recommendations=recommendations,
            action_plan=action_plan,
            report=str(llm_response.get("report") or ""),
            sources=self._sanitize_sources(
                llm_response.get("sources"), research
            ),
            metadata=Metadata(processing_time_ms=processing_time_ms),
        )

    # ── Sanitizers ─────────────────────────────────────────────
    def _sanitize_charts(self, raw: Any) -> list[ChartData]:
        """Drop charts whose data is empty. Never fabricate values."""
        if not isinstance(raw, list):
            return []
        out: list[ChartData] = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            try:
                chart = ChartData(**c)
            except Exception:
                continue
            # Must have data to be renderable.
            has_labels = bool(chart.labels)
            has_data = any(
                bool(ds.get("data")) for ds in chart.datasets
            )
            if not (has_labels and has_data):
                continue
            out.append(chart)
        return out

    def _sanitize_metric_cards(self, raw: Any) -> list[MetricCard]:
        """Drop metric cards missing a numeric value."""
        if not isinstance(raw, list):
            return []
        out: list[MetricCard] = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            value = c.get("value")
            if not isinstance(value, (int, float)):
                continue
            try:
                out.append(MetricCard(**c))
            except Exception:
                continue
        return out

    def _sanitize_sources(
        self,
        llm_sources: Any,
        research: dict[str, Any],
    ) -> list[SourceReference]:
        """Collect sources from LLMPing + every WebHunter result."""
        out: list[SourceReference] = []
        if isinstance(llm_sources, list):
            for s in llm_sources:
                if isinstance(s, dict) and s.get("source"):
                    try:
                        out.append(SourceReference(**s))
                    except Exception:
                        continue
                elif isinstance(s, str) and s:
                    out.append(SourceReference(source=s, type="web"))
        for research_type, payload in (research or {}).items():
            if not isinstance(payload, dict):
                continue
            sources = payload.get("sources") or []
            if isinstance(sources, list):
                for s in sources:
                    if isinstance(s, dict) and s.get("url"):
                        out.append(
                            SourceReference(
                                source=s["url"],
                                type="web",
                                relevance=research_type,
                            )
                        )
                    elif isinstance(s, str) and s:
                        out.append(
                            SourceReference(
                                source=s,
                                type="web",
                                relevance=research_type,
                            )
                        )
        return out
