"""
CompetitorEngine — pure orchestrator.

This module does no reasoning, no prompting, no scraping. Its only
job is to:
  1. Decide what information the user's request needs.
  2. Call WebHunter for fresh external research when needed.
  3. Call LLMPing to reason over that context.
  4. Validate and normalize the response into a clean
      frontend-ready shape.

Parser-driven flow (PARSER.md):
  The orchestrator accepts structured parser output (intent,
  entities, constraints) and executes the required operations with
  fault-tolerance: validation, retries, partial results, and
  per-entity status tracking. Dynamic company data is capped at 3.
"""
import asyncio
import time
import uuid
from typing import Any

import structlog

from app.schemas.analysis import (
    ActionItem,
    ChartData,
    CompetitorCard,
    ContextUpdate,
    EntityStatus,
    MissingData,
    ParserInput,
    Recommendation,
    ResultCounts,
    SWOTAnalysis,
    SWOTItem,
)
from app.schemas.business import BusinessProfile, FormInput
from app.schemas.output import (
    AnalysisResult,
    ChatResponse,
    Metadata,
    MetricCard,
    ParserOutput,
    SourceReference,
)
from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import WebHunterClient, WebHunterError

logger = structlog.get_logger(__name__)


# Holds the most recent resolved URLs for visibility from outside the
# orchestrator (e.g. the FastAPI lifespan reports them on `/`).
_resolved_upstreams: dict[str, str | None] = {"llmping": None, "webhunter": None}


def get_resolved_upstreams() -> dict[str, str | None]:
    """Return the URLs most recently resolved by an Orchestrator instance.

    Updated by `Orchestrator.__init__` and by every successful upstream
    call (lazy resolution). `None` means discovery hasn't run yet or
    no candidate answered.
    """
    return dict(_resolved_upstreams)


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

# Retry policy (PARSER.md §4.2).
_BOOTSTRAP_MAX_RETRIES = 2
_BOOTSTRAP_RETRY_DELAY_S = 1.0
_SINGLE_OP_MAX_RETRIES = 1
_SINGLE_OP_RETRY_DELAY_S = 0.5

# Validation defaults (PARSER.md §4.1).
_VALID_MARKET_POSITIONS = {"Leader", "Challenger", "Niche", "Emerging"}
_VALID_PRIORITIES = {"P0", "P1", "P2"}
_VALID_IMPACT = {"High", "Medium", "Low"}
_VALID_HORIZON = {"Now", "Next", "Later"}
_VALID_EFFORT = {"Low", "Medium", "High"}
_VALID_MATURITY = {"GA", "Beta", "Roadmap", "Deprecated"}
_VALID_CHART_KINDS = {"bar", "line", "area", "radar", "pie"}

# Hard cap on dynamically generated competitors (PARSER.md §3.1).
_MAX_DYNAMIC_COMPANIES = 3


class Orchestrator:
    """Stateless orchestration layer. Safe to construct per request."""

    def __init__(
        self,
        llmping: LLMPingClient | None = None,
        webhunter: WebHunterClient | None = None,
    ):
        self.llmping = llmping or LLMPingClient()
        self.webhunter = webhunter or WebHunterClient()
        # If the caller supplied pre-configured clients with explicit
        # URLs, surface those immediately. Otherwise schedule a
        # non-blocking discovery pass so operators can see resolved
        # URLs in container logs without paying for them at request
        # time. Use getattr for tolerance to spec-based mocks in tests.
        llm_url = getattr(self.llmping, "base_url", "") or ""
        wh_url = getattr(self.webhunter, "base_url", "") or ""
        if llm_url:
            _resolved_upstreams["llmping"] = llm_url
        if wh_url:
            _resolved_upstreams["webhunter"] = wh_url
        if not llm_url or not wh_url:
            self._kickoff_resolution()

    def _kickoff_resolution(self) -> None:
        """Schedule a background resolution pass. Safe to call repeatedly."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No loop yet (e.g. constructed at import time); skip.
        loop.create_task(self._resolve_upstreams())

    async def _resolve_upstreams(self) -> None:
        """Resolve both upstreams in parallel; record results.

        Never raises — failure to resolve is logged but does not block
        request handling. Per-request calls will trigger lazy
        resolution and surface structured errors via the existing
        fault-tolerance paths.
        """
        try:
            llm_url, wh_url = await asyncio.gather(
                self.llmping.resolve(),
                self.webhunter.resolve(),
            )
            if llm_url:
                _resolved_upstreams["llmping"] = llm_url
                logger.info("upstream_resolved", service="llmping", url=llm_url)
            else:
                logger.warning("upstream_unresolved", service="llmping")
            if wh_url:
                _resolved_upstreams["webhunter"] = wh_url
                logger.info("upstream_resolved", service="webhunter", url=wh_url)
            else:
                logger.warning("upstream_unresolved", service="webhunter")
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("upstream_resolution_error", error=str(e))

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

    # ── Parser-driven execution (PARSER.md) ────────────────────
    async def execute(self, parser_input: ParserInput) -> ParserOutput:
        """Parser-driven entry point.

        Accepts structured parser output and executes the operations
        required by the intent. Adds fault-tolerance: validation,
        retries, partial results, and per-entity status tracking.
        """
        intent = parser_input.intent
        log = logger.bind(intent=intent)
        log.info("parser_execute_started")

        # Route to the appropriate handler based on intent.
        handlers = {
            "bootstrap": self._exec_bootstrap,
            "question": self._exec_question,
            "follow-up": self._exec_question,
            "refine": self._exec_refine,
            "compare": self._exec_compare,
            "explain": self._exec_explain,
            "regenerate": self._exec_regenerate,
        }
        handler = handlers.get(intent, self._exec_bootstrap)

        try:
            return await handler(parser_input)
        except Exception as e:
            log.error("parser_execute_failed", error=str(e))
            return ParserOutput(
                intent=intent,
                status="error",
                data=self._empty_result(),
                error=f"Execution failed: {e}",
                missing_data=[
                    MissingData(
                        field="execution",
                        reason=str(e),
                        severity="critical",
                    )
                ],
            )

    async def _exec_bootstrap(self, parser_input: ParserInput) -> ParserOutput:
        """Full competitive analysis with retry and fault-tolerance."""
        # Resolve form input — either provided directly or built
        # from the parser's extracted entities.
        form_input = parser_input.form_input
        if form_input is None:
            form_input = self._build_form_from_parser(parser_input)

        if not form_input or not form_input.business_name:
            return ParserOutput(
                intent="bootstrap",
                status="error",
                data=self._empty_result(),
                error="Business name required",
                missing_data=[
                    MissingData(
                        field="businessName",
                        reason="Business name is required to start analysis",
                        severity="critical",
                    )
                ],
            )

        # Enforce max 3 companies for dynamic data.
        requested_count = min(
            max(parser_input.requested_count, 1), _MAX_DYNAMIC_COMPANIES
        )
        # Pass the limit through to LLMPing so it can scope work.
        form_input.competitors = (form_input.competitors or [])[:requested_count]

        # Retry loop for the full bootstrap (PARSER.md §4.2).
        last_error: Exception | None = None
        for attempt in range(_BOOTSTRAP_MAX_RETRIES + 1):
            try:
                result = await self.run_overview(form_input)
                break
            except LLMPingError as e:
                last_error = e
                logger.warning(
                    "bootstrap_retry",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < _BOOTSTRAP_MAX_RETRIES:
                    await asyncio.sleep(_BOOTSTRAP_RETRY_DELAY_S)
        else:
            # All retries exhausted — return structured error.
            return ParserOutput(
                intent="bootstrap",
                status="error",
                data=self._empty_result(),
                error=(
                    f"Analysis failed after {_BOOTSTRAP_MAX_RETRIES + 1} "
                    f"attempts: {last_error}"
                ),
                missing_data=[
                    MissingData(
                        field="analysis",
                        reason=str(last_error),
                        severity="critical",
                    )
                ],
            )

        # Enforce company limit on results and track per-entity status.
        result = self._enforce_company_limit(result, requested_count)
        entity_statuses = self._track_entity_statuses(result)

        # Build missing-data report from parser gaps + validation.
        missing_data = self._build_missing_data(parser_input, result)
        missing_data.extend(
            self._validate_result_fields(result, requested_count)
        )

        # Determine overall status.
        status = self._determine_status(result, missing_data)

        # Build compact context update for the parser.
        context_update = self._build_context_update(
            form_input, result, requested_count
        )

        # Result counts for UI.
        retrieved = len(result.competitors)
        valid = sum(
            1
            for c in entity_statuses.get("competitors", [])
            if c.status in ("complete", "partial")
        )

        return ParserOutput(
            intent="bootstrap",
            status=status,
            data=result,
            missing_data=missing_data,
            context_update=context_update,
            result_counts=ResultCounts(
                requested=requested_count,
                retrieved=retrieved,
                valid=valid,
                displayed=valid,
            ),
            operations_performed=(
                parser_input.requested_operations
                or ["extract", "infer", "calculate", "normalize"]
            ),
            entity_statuses=entity_statuses,
        )

    async def _exec_question(self, parser_input: ParserInput) -> ParserOutput:
        """Answer a question using existing context or fresh research."""
        chat_result = await self.chat(
            session_id=parser_input.session_id,
            message=parser_input.message,
            current_analysis=parser_input.current_analysis,
            fresh_research=parser_input.fresh_research,
        )

        # Wrap chat answer as AnalysisResult for uniform output.
        result = AnalysisResult(
            business_summary=chat_result.answer,
            charts=chat_result.mini_charts,
            metric_cards=chat_result.metric_cards,
            sources=chat_result.sources,
        )

        return ParserOutput(
            intent=parser_input.intent,
            status="success" if chat_result.answer else "partial",
            data=result,
            missing_data=(
                []
                if chat_result.answer
                else [
                    MissingData(
                        field="answer",
                        reason="No answer generated",
                        severity="warning",
                    )
                ]
            ),
            context_update=None,
            operations_performed=["infer"],
        )

    async def _exec_refine(self, parser_input: ParserInput) -> ParserOutput:
        """Modify existing data (add/remove/update competitor)."""
        # For refine, re-run the analysis with updated context.
        # The parser provides the modified constraints/entities.
        current = parser_input.current_analysis or {}

        # If a competitor was added and we're under the limit, run
        # bootstrap again with the updated competitor list.
        competitors = (parser_input.entities.get("competitor_names", []))
        if len(competitors) <= _MAX_DYNAMIC_COMPANIES:
            form_input = parser_input.form_input
            if form_input is None:
                form_input = self._build_form_from_parser(parser_input)
            if form_input:
                form_input.competitors = competitors[:_MAX_DYNAMIC_COMPANIES]
                return await self._exec_bootstrap(
                    ParserInput(
                        intent="bootstrap",
                        form_input=form_input,
                        requested_count=len(competitors),
                    )
                )

        # Fallback: return current data unchanged with a note.
        result = self._analysis_from_dict(current)
        return ParserOutput(
            intent="refine",
            status="partial",
            data=result,
            missing_data=[
                MissingData(
                    field="refine",
                    reason="Could not apply refinement — limit reached or insufficient data",
                    severity="warning",
                )
            ],
            operations_performed=["normalize"],
        )

    async def _exec_compare(self, parser_input: ParserInput) -> ParserOutput:
        """Generate a comparison between entities."""
        # Delegate to chat with a compare-focused message.
        compare_msg = parser_input.message or "Compare the competitors"
        chat_result = await self.chat(
            session_id=parser_input.session_id,
            message=compare_msg,
            current_analysis=parser_input.current_analysis,
        )

        result = AnalysisResult(
            comparisons=chat_result.mini_charts and [] or [],
            business_summary=chat_result.answer,
            sources=chat_result.sources,
        )

        return ParserOutput(
            intent="compare",
            status="success" if chat_result.answer else "partial",
            data=result,
            operations_performed=["calculate", "normalize"],
        )

    async def _exec_explain(self, parser_input: ParserInput) -> ParserOutput:
        """Explain a specific data point or insight."""
        explain_msg = parser_input.message or "Explain the key findings"
        chat_result = await self.chat(
            session_id=parser_input.session_id,
            message=explain_msg,
            current_analysis=parser_input.current_analysis,
        )

        result = AnalysisResult(
            business_summary=chat_result.answer,
            sources=chat_result.sources,
        )

        return ParserOutput(
            intent="explain",
            status="success" if chat_result.answer else "partial",
            data=result,
            operations_performed=["infer"],
        )

    async def _exec_regenerate(self, parser_input: ParserInput) -> ParserOutput:
        """Regenerate a specific section (re-run bootstrap)."""
        # Regeneration is a fresh bootstrap with the same input.
        output = await self._exec_bootstrap(parser_input)
        # Preserve the original intent in the output.
        output.intent = "regenerate"
        return output

    # ── Validation & status tracking ───────────────────────────
    def _enforce_company_limit(
        self,
        result: AnalysisResult,
        limit: int,
    ) -> AnalysisResult:
        """Cap dynamically generated competitors at the limit.

        Never fabricates missing results to satisfy the limit.
        Preserves whatever was successfully retrieved.
        """
        effective_limit = min(max(limit, 1), _MAX_DYNAMIC_COMPANIES)
        if len(result.competitors) > effective_limit:
            result.competitors = result.competitors[:effective_limit]
        return result

    def _track_entity_statuses(
        self, result: AnalysisResult
    ) -> dict[str, list[EntityStatus]]:
        """Assign per-entity status based on field completeness."""
        statuses: dict[str, list[EntityStatus]] = {}

        comp_statuses: list[EntityStatus] = []
        for comp in result.competitors:
            missing = self._missing_competitor_fields(comp)
            status = (
                "failed" if "name" in missing
                else "partial" if missing
                else "complete"
            )
            comp_statuses.append(
                EntityStatus(
                    id=self._slugify(comp.name),
                    name=comp.name,
                    status=status,
                    missing_fields=missing,
                )
            )
        statuses["competitors"] = comp_statuses
        return statuses

    def _missing_competitor_fields(self, comp: CompetitorCard) -> list[str]:
        """List required fields that are missing or invalid."""
        missing: list[str] = []
        if not comp.name:
            missing.append("name")
        if not comp.description:
            missing.append("description")
        if comp.market_position not in _VALID_MARKET_POSITIONS:
            missing.append("market_position")
        return missing

    def _validate_result_fields(
        self,
        result: AnalysisResult,
        requested_count: int,
    ) -> list[MissingData]:
        """Validate result and build missing-data report."""
        missing: list[MissingData] = []

        # Check if we got fewer competitors than requested.
        if len(result.competitors) < requested_count:
            missing.append(
                MissingData(
                    field="competitors",
                    reason=(
                        f"Only {len(result.competitors)} of "
                        f"{requested_count} requested competitors "
                        f"could be retrieved"
                    ),
                    severity="warning",
                )
            )

        # Check for empty critical sections.
        if not result.swot.strengths and not result.swot.opportunities:
            missing.append(
                MissingData(
                    field="swot",
                    reason="SWOT analysis incomplete",
                    severity="info",
                )
            )

        if not result.competitors:
            missing.append(
                MissingData(
                    field="competitors",
                    reason="No competitor data available",
                    severity="warning",
                )
            )

        return missing

    def _determine_status(
        self,
        result: AnalysisResult,
        missing_data: list[MissingData],
    ) -> str:
        """Determine overall status from result and missing data."""
        has_critical = any(
            m.severity == "critical" for m in missing_data
        )
        has_warnings = any(
            m.severity in ("warning", "info") for m in missing_data
        )

        if has_critical or not result.competitors:
            return "error" if has_critical else "partial"
        if has_warnings:
            return "partial"
        return "success"

    def _build_missing_data(
        self,
        parser_input: ParserInput,
        result: AnalysisResult,
    ) -> list[MissingData]:
        """Report parser-identified missing information."""
        missing: list[MissingData] = []
        for field in parser_input.missing_information:
            # Only report if we also couldn't fill it.
            if not self._field_is_filled(field, result):
                missing.append(
                    MissingData(
                        field=field,
                        reason=f"Could not determine {field} from input or research",
                        severity="info",
                    )
                )
        return missing

    def _field_is_filled(self, field: str, result: AnalysisResult) -> bool:
        """Check if a parser-missing field was filled by the analysis."""
        field_lower = field.lower()
        if "competitor" in field_lower:
            return len(result.competitors) > 0
        if "pricing" in field_lower:
            return any(c.pricing for c in result.competitors)
        if "swot" in field_lower:
            return bool(result.swot.strengths or result.swot.opportunities)
        if "market" in field_lower:
            return bool(result.market_info)
        return False

    def _build_context_update(
        self,
        form_input: FormInput,
        result: AnalysisResult,
        requested_count: int,
    ) -> ContextUpdate:
        """Build compact context update for the parser to store."""
        competitor_ids = [
            self._slugify(c.name) for c in result.competitors if c.name
        ]
        keywords = list(set(
            [
                form_input.industry,
                form_input.business_model,
                form_input.geography,
            ]
            + (form_input.competitors or [])
        ))
        keywords = [k for k in keywords if k][:10]  # Cap at 10

        return ContextUpdate(
            business={
                "name": form_input.business_name,
                "industry": form_input.industry,
                "pricing": form_input.pricing,
                "model": form_input.business_model,
            },
            entities={
                "competitors": competitor_ids[:_MAX_DYNAMIC_COMPANIES],
                "focus": None,
            },
            result_meta={
                "requested_count": requested_count,
                "retrieved_count": len(result.competitors),
                "filters": [],
            },
            constraints={
                "included": form_input.differentiators and [form_input.differentiators] or [],
                "excluded": [],
            },
            keywords=keywords,
        )

    def _build_form_from_parser(
        self, parser_input: ParserInput
    ) -> FormInput | None:
        """Build FormInput from parser-extracted entities + facts."""
        entities = parser_input.entities
        facts = parser_input.facts

        name = (
            facts.get("business_name")
            or entities.get("business_names", [None])[0]
            or facts.get("product_type", "")
        )
        if not name:
            return None

        industry = (
            facts.get("industry")
            or entities.get("industries", [None])[0]
            or "Unknown"
        )
        idea = (
            facts.get("product_type")
            or entities.get("product_names", [None])[0]
            or name
        )

        return FormInput(
            business_name=str(name),
            idea=str(idea),
            industry=str(industry),
            products_services=entities.get("product_names", []),
            target_customers=facts.get("target_market", ""),
            geography=entities.get("geographic_mentions", [None])[0] or "",
            pricing=facts.get("pricing_tier", ""),
            business_model=facts.get("business_model", ""),
            competitors=entities.get("competitor_names", [])[
                :_MAX_DYNAMIC_COMPANIES
            ],
            differentiators="",
            research_goals=[],
            user_query=parser_input.message,
        )

    def _analysis_from_dict(self, data: dict[str, Any]) -> AnalysisResult:
        """Safely reconstruct AnalysisResult from a dict."""
        if not data:
            return self._empty_result()
        try:
            return AnalysisResult(**{
                k: v for k, v in data.items()
                if k in AnalysisResult.model_fields
            })
        except Exception:
            return self._empty_result()

    def _empty_result(self) -> AnalysisResult:
        """Return an empty AnalysisResult placeholder."""
        return AnalysisResult()

    @staticmethod
    def _slugify(name: str) -> str:
        """Generate URL-safe slug from a name."""
        return (
            name.lower().strip().replace(" ", "-")
            if name else ""
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
