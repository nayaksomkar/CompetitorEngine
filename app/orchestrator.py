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
  entities, constraints) and executes the required operations
  with fault-tolerance: validation, retries, partial results,
  and per-entity status tracking. Dynamic company data is
  capped at 3.

The "Ask About Any Company" flow (ORCHESTRATOR.md §5):
  For question/compare/explain intents that name a company not
  in the session context, the orchestrator searches WebHunter,
  asks LLMPing to extract a strict profile, and returns the
  merged result in an `answer` block — writing the new entity
  into the compact context (evicting the oldest active entity
  when the 3-company cap is hit).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.schemas.analysis import (
    ComparisonRow,
    ContextUpdate,
    EntityStatus,
    MissingData,
    ParserInput,
    ResultCounts,
)
from app.schemas.business import BusinessProfile, FormInput
from app.schemas.domain import (
    CHART_KINDS,
    EFFORT_LEVELS,
    ENTITY_STATUSES,
    HORIZONS,
    IMPACT_LEVELS,
    INSIGHT_CATEGORIES,
    MARKET_POSITIONS,
    MATURITY_LEVELS,
    PRIORITIES,
    REPORT_TYPES,
    ActionPlanItem,
    ChartData,
    ChartPoint,
    ChartSeries,
    Competitor,
    EvidenceItem,
    Explanation,
    InsightItem,
    MarketGap,
    MetricCard,
    PricingTier,
    Product,
    ProductFeature,
    Report,
    ReportSection,
    SWOT,
    clamp_score,
)
from app.schemas.output import (
    AnalysisResult,
    AnswerBlock,
    ChatResponse,
    ComparisonTable,
    DerivedData,
    EvictedEntity,
    LookupCompetitor,
    LookupProfile,
    Metadata,
    ParserOutput,
    Source,
    UIState,
)
from app.services.llmping_client import LLMPingClient, LLMPingError
from app.services.webhunter_client import (
    LOOKUP_HTTP_TIMEOUT_S,
    WebHunterClient,
    WebHunterError,
)

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


# 24h TTL cache for evicted entity profiles (ORCHESTRATOR.md §8.4).
# Bounded, in-memory, and keyed by slug — the orchestrator stays
# stateless with respect to sessions; this only avoids re-fetching
# a profile the user evicted minutes ago.
_evicted_profile_cache: dict[str, dict[str, Any]] = {}
_EVICTED_TTL_S = 24 * 3600


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

# Retry policy (PARSER.md §4.2, ORCHESTRATOR.md §7.1).
_BOOTSTRAP_MAX_RETRIES = 2
_BOOTSTRAP_RETRY_DELAY_S = 1.0
_SINGLE_OP_MAX_RETRIES = 1
_SINGLE_OP_RETRY_DELAY_S = 0.5

# Per-entity lookup retry policy (ORCHESTRATOR.md §7.1):
# WebHunter search 2 retries / 1s; LLMPing extraction 2 retries / 500ms.
_LOOKUP_WH_RETRIES = 2
_LOOKUP_WH_DELAY_S = 1.0
_LOOKUP_LLM_RETRIES = 2
_LOOKUP_LLM_DELAY_S = 0.5

# Total wall-time budget for the WebHunter retry loop. Measured
# legacy WebHunter latency: ~7s on fast transient failures,
# ~27-50s on successful crawls. Retrying fast failures is cheap
# and genuinely helps (upstream search flakiness is transient);
# retrying slow timeouts burns the request budget, so once the
# budget cannot fit another full attempt the remaining retries
# are skipped and the entity is reported partial instead of
# timing out the whole request. With LOOKUP_HTTP_TIMEOUT_S=60
# this admits at most 2 real attempts (~121s worst case).
_LOOKUP_WH_BUDGET_S = 130.0
_LOOKUP_OPERATION_TIMEOUT_S = 80.0

# Lookup budget: cap each request at 3 WebHunter searches so one
# user message naming 3 new companies stays under the cap (§12).
_MAX_LOOKUP_BUDGET = 3

# Validation sets (PARSER.md §4.1).
_VALID_MARKET_POSITIONS = {"Leader", "Challenger", "Niche", "Emerging"}

# Hard cap on dynamically generated competitors (PARSER.md §3.1).
_MAX_DYNAMIC_COMPANIES = 3

# Stopwords for proper-noun extraction from free-text messages.
_MESSAGE_STOPWORDS = {
    "about", "tell", "me", "compare", "comparing", "versus", "vs",
    "the", "and", "of", "is", "are", "was", "were", "do", "does",
    "did", "can", "could", "should", "would", "will", "what", "who",
    "how", "why", "explain", "as", "to", "other", "relevant",
    "companies", "company", "in", "this", "that", "these", "those",
    "it", "its", "list", "a", "an", "please", "show", "for", "with",
    "between", "my", "our", "your", "their", "i", "we", "you", "they",
    # Analysis-vocabulary words: request shape, not entity names
    # ("give me a SWOT analysis of X" must not look up "SWOT"
    # or "Analysis" as companies).
    "swot", "analysis", "overview", "profile", "comparison",
    "insights", "insight", "report", "reports", "summary",
    "breakdown", "benchmark", "benchmarks", "review", "data",
}

# Pure all-caps tokens at or below this length are treated as
# acronyms (SWOT, KPI, ROI), not company names, unless they are
# already known entities in the session context. Known all-caps
# companies (IBM, SAP) still resolve via the context/known-name
# extraction paths above.
_ACRONYM_MAX_LEN = 5

# Metric hints: "what is the market share of X" must research
# market share, not a generic profile (§7 question alignment).
_METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("market share", "market_share"),
    ("marketshare", "market_share"),
    ("pricing", "pricing"),
    ("price", "pricing"),
    ("cost", "pricing"),
    ("funding", "funding"),
    ("valuation", "funding"),
    ("headquarters", "funding"),
)


def _detect_metric(message: str) -> str:
    """Return the metric the user asked about, if any.

    Maps a free-text question onto one of the WebHunter §5.1
    query templates so the upstream research targets the
    requested data point. Defaults to a generic profile.
    """
    text = (message or "").lower()
    for needle, metric in _METRIC_PATTERNS:
        if needle in text:
            return metric
    return "profile"


@dataclass
class _ResolvedEntities:
    """Outcome of ORCHESTRATOR.md §5 entity resolution."""

    in_context: list[LookupCompetitor] = field(default_factory=list)
    looked_up: list[LookupCompetitor] = field(default_factory=list)
    missing_data: list[MissingData] = field(default_factory=list)
    evicted: list[EvictedEntity] = field(default_factory=list)
    context_update: dict[str, Any] | None = None
    mentions: list[str] = field(default_factory=list)
    # True when at least one named entity needed a fresh
    # WebHunter/LLMPing lookup (vs. pure context question).
    attempted: bool = False

    @property
    def entities(self) -> list[LookupCompetitor]:
        return self.in_context + self.looked_up


@dataclass
class _LookupOutcome:
    """One requested competitor lookup, including a classified failure."""

    status: str
    competitor: LookupCompetitor | None = None
    reason: str = ""


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
        result = self._normalize_result(result)

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
        if not isinstance(decision, dict):
            # Malformed upstream response — treat as "no
            # research needed" and answer from context alone.
            decision = {}

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
        if not isinstance(llm_response, dict):
            llm_response = {}

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

        # Structured dashboard actions bypass free-form chat planning.
        handlers = {
            "bootstrap": self._exec_bootstrap,
            "question": self._exec_question,
            "follow-up": self._exec_question,
            "refine": self._exec_refine,
            "compare": self._exec_compare,
            "explain": self._exec_explain,
            "regenerate": self._exec_regenerate,
        }
        handler = (
            self._exec_structured_action
            if parser_input.action is not None
            else handlers.get(intent, self._exec_bootstrap)
        )

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

        missing_data = self._build_missing_data(parser_input, result)
        lookup_statuses: list[EntityStatus] = []
        if form_input.competitors:
            requested_names = list(dict.fromkeys(
                name.strip()
                for name in form_input.competitors[:requested_count]
                if name.strip()
            ))
            existing_slugs = {
                self._slugify(competitor.name)
                for competitor in result.competitors
            }
            missing_names = [
                name for name in requested_names
                if self._slugify(name) not in existing_slugs
            ]
            outcomes = await asyncio.gather(
                *[
                    self._lookup_entity_outcome(
                        name, form_input.industry, "profile"
                    )
                    for name in missing_names
                ],
                return_exceptions=True,
            )
            for name, outcome in zip(missing_names, outcomes):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                slug = self._slugify(name)
                if isinstance(outcome, BaseException):
                    reason = f"Competitor lookup failed for {name}: {outcome}"
                    lookup_statuses.append(
                        EntityStatus(
                            id=slug,
                            name=name,
                            status="failed",
                            missing_fields=["provider lookup failed"],
                            source="web",
                        )
                    )
                elif outcome.competitor is None:
                    reason = outcome.reason
                    lookup_statuses.append(
                        EntityStatus(
                            id=slug,
                            name=name,
                            status="failed",
                            missing_fields=[outcome.status],
                            source="web",
                        )
                    )
                else:
                    retrieved = self._lookup_to_competitor(outcome.competitor)
                    retrieved.status = (
                        "partial" if outcome.status != "complete" else "complete"
                    )
                    result.competitors.append(retrieved)
                    if outcome.reason:
                        reason = outcome.reason
                        lookup_statuses.append(
                            EntityStatus(
                                id=retrieved.id,
                                name=retrieved.name,
                                status="partial",
                                missing_fields=[outcome.status, outcome.reason],
                                source="web",
                                lookupConfidence=outcome.competitor.lookupConfidence,
                            )
                        )
                    else:
                        reason = ""
                result.sources = self._merge_sources(
                    result.sources,
                    outcome.competitor.sources
                    if not isinstance(outcome, BaseException)
                    and outcome.competitor is not None
                    else [],
                )
                if reason:
                    missing_data.append(
                        MissingData(
                            field=f"competitors[{name}]",
                            reason=reason,
                            severity="warning",
                        )
                    )

        # Enforce company limit on results and track per-entity status.
        result = self._enforce_company_limit(result, requested_count)

        # Derive charts from the real data (PARSER.md §10).
        derived_charts, derived_names = self._derive_charts(result)
        if derived_charts:
            result.charts = result.charts + derived_charts

        entity_statuses = self._track_entity_statuses(result, form_input)
        for lookup_status in lookup_statuses:
            existing_status = next(
                (
                    item
                    for item in entity_statuses["competitors"]
                    if item.id == lookup_status.id
                ),
                None,
            )
            if existing_status is None:
                entity_statuses["competitors"].append(lookup_status)
                continue
            existing_status.status = lookup_status.status
            existing_status.missing_fields = list(dict.fromkeys(
                existing_status.missing_fields + lookup_status.missing_fields
            ))
            existing_status.source = lookup_status.source
            existing_status.lookupConfidence = lookup_status.lookupConfidence

        # Build missing-data report from parser gaps + validation.
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
        counts = ResultCounts(
            requested=requested_count,
            retrieved=retrieved,
            valid=valid,
            displayed=valid,
        )

        return ParserOutput(
            intent="bootstrap",
            status=status,
            data=result,
            missing_data=missing_data,
            context_update=context_update,
            result_counts=counts,
            operations_performed=(
                parser_input.requested_operations
                or ["extract", "infer", "calculate", "normalize"]
            ),
            entity_statuses=entity_statuses,
            derived_data=DerivedData(
                charts_generated=derived_names,
                calculations_performed=["marketShare_normalization"],
            ),
            ui_state=UIState(
                active_tab="overview",
                highlighted_entities=[],
                result_counts=counts,
            ),
        )

    async def _exec_question(self, parser_input: ParserInput) -> ParserOutput:
        """Answer a question — from context, or via §5 lookups.

        `follow-up` is an alias for this intent (§4.3).
        """
        resolved = await self._resolve_entities(parser_input)
        entities = resolved.entities
        current = parser_input.current_analysis
        message = parser_input.message or ""

        if not resolved.attempted:
            # Pure context question (§4.3: "If it references existing
            # context → answer from context"). No WebHunter calls.
            chat_result = await self.chat(
                session_id=parser_input.session_id,
                message=message,
                current_analysis=current,
            )
            answer = AnswerBlock(
                summary=chat_result.answer,
                competitors=entities,
                comparedTo=[],
                question=message or None,
                sources=chat_result.sources,
            )
            counts = ResultCounts(
                requested=len(resolved.mentions),
                retrieved=len(entities),
                valid=len(entities),
                displayed=len(entities),
            )
            return ParserOutput(
                intent=parser_input.intent,
                status="success" if chat_result.answer else "partial",
                data=self._analysis_from_dict(current),
                answer=answer,
                missing_data=resolved.missing_data,
                context_update=self._context_from_dict(resolved.context_update),
                result_counts=counts,
                operations_performed=["extract", "infer"],
                entity_statuses={
                    "competitors": self._lookup_entity_statuses(entities)
                },
                ui_state=UIState(result_counts=counts),
            )

        # Lookup question — build the §5.2 answer block.
        if parser_input.intent == "compare":
            primary, compared = entities[:1], entities[1:]
        else:
            primary, compared = entities, []

        summary = ""
        if len(entities) == 1:
            summary = entities[0].profile.description
        elif entities:
            summary = await self._compare_summary(
                entities, current, parser_input.session_id
            )

        all_sources: list[Source] = []
        for e in entities:
            all_sources.extend(e.sources)

        # If the user asked for a specific metric and the evidence
        # did not yield it, report the field as unavailable in the
        # existing missing_data contract — never invent a value
        # to make the response look complete (§11).
        metric_gap = self._requested_metric_gap(
            _detect_metric(message), entities
        )
        missing = list(resolved.missing_data)
        if metric_gap is not None:
            missing.append(metric_gap)

        counts = ResultCounts(
            requested=len(resolved.mentions),
            retrieved=len(entities),
            valid=sum(
                1
                for s in self._lookup_entity_statuses(entities)
                if s.status in ("complete", "partial")
            ),
            displayed=len(entities),
        )
        return ParserOutput(
            intent=parser_input.intent,
            status="partial" if missing else "success",
            data=self._analysis_from_dict(current),
            answer=AnswerBlock(
                summary=summary,
                competitors=primary,
                comparedTo=compared,
                question=message or None,
                sources=all_sources,
            ),
            missing_data=missing,
            context_update=self._context_from_dict(resolved.context_update),
            evicted_entities=resolved.evicted,
            result_counts=counts,
            operations_performed=["extract", "infer", "normalize"],
            entity_statuses={
                "competitors": self._lookup_entity_statuses(entities)
            },
            ui_state=UIState(
                active_tab="competitors", result_counts=counts
            ),
        )

    async def _exec_structured_action(
        self,
        parser_input: ParserInput,
    ) -> ParserOutput:
        """Retrieve only missing, competitor-specific action information."""
        action = parser_input.action
        current = parser_input.current_analysis or {}
        if action is None:
            raise ValueError("A structured action is required.")

        entity = action.entity.strip()
        target = (action.target or "").strip()
        requested_names = [entity]
        if action.action == "compare_competitors":
            if target:
                requested_names.append(target)
            else:
                return self._action_missing_result(
                    parser_input,
                    "comparison",
                    "Choose another competitor to compare.",
                )

        profile = current.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        business = (
            profile.get("business_name")
            or profile.get("businessName")
            or current.get("businessName")
            or ""
        )
        known_names = {
            self._slugify(str(name))
            for name in profile.get("competitors", []) or []
            if isinstance(name, str) and name.strip()
        }
        known_names.update(
            self._slugify(str(competitor.get("name")))
            for competitor in current.get("competitors", []) or []
            if isinstance(competitor, dict) and competitor.get("name")
        )
        if any(self._slugify(name) not in known_names for name in requested_names):
            unsupported = next(
                name
                for name in requested_names
                if self._slugify(name) not in known_names
            )
            return self._action_missing_result(
                parser_input,
                f"competitors[{unsupported}]",
                (
                    f"No saved competitor profile or source evidence exists for "
                    f"{unsupported}; this action did not search for the business "
                    f"itself ({business or 'unnamed business'})."
                ),
            )

        metric_by_action = {
            "show_pricing": "pricing",
            "show_market_share": "market_share",
            "show_growth": "profile",
        }
        metric = metric_by_action.get(action.action, "profile")
        outcomes = await asyncio.gather(
            *[
                self._lookup_entity_outcome(name, self._industry_from(
                    parser_input.context_update, current
                ), metric, parser_input.session_id)
                for name in requested_names
            ],
            return_exceptions=True,
        )

        retrieved: list[LookupCompetitor] = []
        missing: list[MissingData] = []
        statuses: list[EntityStatus] = []
        for name, outcome in zip(requested_names, outcomes):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                missing.append(
                    MissingData(
                        field=f"competitors[{name}]",
                        reason=f"Lookup failed for {name}: {outcome}",
                        severity="warning",
                    )
                )
                statuses.append(EntityStatus(
                    id=self._slugify(name),
                    name=name,
                    status="failed",
                    missing_fields=["provider_failed"],
                    source="web",
                ))
                continue
            if outcome.competitor is None:
                missing.append(
                    MissingData(
                        field=f"competitors[{name}]",
                        reason=outcome.reason,
                        severity="warning",
                    )
                )
                statuses.append(EntityStatus(
                    id=self._slugify(name),
                    name=name,
                    status="failed",
                    missing_fields=[outcome.status],
                    source="web",
                ))
                continue
            retrieved.append(outcome.competitor)
            if outcome.reason:
                missing.append(
                    MissingData(
                        field=f"competitors[{name}]",
                        reason=outcome.reason,
                        severity="warning",
                    )
                )
            statuses.append(EntityStatus(
                id=outcome.competitor.id,
                name=outcome.competitor.name,
                status="partial" if outcome.status != "complete" else "complete",
                missing_fields=[outcome.status] if outcome.reason else [],
                source="web",
                lookupConfidence=outcome.competitor.lookupConfidence,
            ))

        evidence: list[EvidenceItem] = []
        for competitor in retrieved:
            p = competitor.profile
            evidence_before = len(evidence)
            if action.action == "show_pricing":
                if p.pricingTier and p.pricingTier.lower() != "unknown":
                    evidence.append(EvidenceItem(
                        label=f"{competitor.name} pricing",
                        detail=p.pricingTier,
                    ))
                else:
                    missing.append(MissingData(
                        field=f"competitors[{competitor.name}].pricing",
                        reason="No verified pricing details were found in the retrieved sources.",
                        severity="info",
                    ))
            elif action.action == "show_market_position":
                if p.marketPosition:
                    evidence.append(EvidenceItem(
                        label=f"{competitor.name} position",
                        detail=p.marketPosition,
                    ))
                if p.marketShare is not None:
                    evidence.append(EvidenceItem(
                        label=f"{competitor.name} market share",
                        detail=f"{p.marketShare}%",
                    ))
                if p.growthRate is not None:
                    evidence.append(EvidenceItem(
                        label=f"{competitor.name} growth",
                        detail=f"{p.growthRate}%",
                    ))
            elif action.action == "show_weaknesses":
                evidence.extend(EvidenceItem(
                    label=f"{competitor.name} weakness",
                    detail=detail,
                ) for detail in p.weaknesses)
            elif action.action == "show_strengths":
                evidence.extend(EvidenceItem(
                    label=f"{competitor.name} strength",
                    detail=detail,
                ) for detail in p.strengths)
            elif p.description:
                evidence.append(EvidenceItem(
                    label=competitor.name,
                    detail=p.description,
                ))
            if len(evidence) == evidence_before and action.action in {
                "show_market_position",
                "show_market_share",
                "show_growth",
                "show_weaknesses",
                "show_strengths",
            }:
                missing.append(MissingData(
                    field=f"competitors[{competitor.name}].{action.action}",
                    reason=(
                        f"No verified {action.action.replace('_', ' ')} information "
                        "was found in the retrieved sources."
                    ),
                    severity="info",
                ))

        sources = self._merge_sources(
            [],
            [source for competitor in retrieved for source in competitor.sources],
        )
        if action.action == "show_sources" and not sources:
            missing.append(MissingData(
                field="sources",
                reason="No supporting sources were returned for this action.",
                severity="warning",
            ))
        if not retrieved and not missing:
            missing.append(MissingData(
                field=action.action,
                reason="No usable information was returned for this action.",
                severity="warning",
            ))

        answer_competitors = retrieved[:1] if action.action == "compare_competitors" else retrieved
        compared = retrieved[1:] if action.action == "compare_competitors" else []
        summary = (
            f"Retrieved {len(retrieved)} of {len(requested_names)} requested competitor profiles."
            if retrieved
            else f"No additional sourced information is available for {entity}."
        )
        counts = ResultCounts(
            requested=len(requested_names),
            retrieved=len(retrieved),
            valid=sum(
                1 for item in statuses if item.status in ("complete", "partial")
            ),
            displayed=len(retrieved),
        )
        return ParserOutput(
            intent=parser_input.intent,
            status="partial" if missing else "success",
            data=self._analysis_from_dict(current),
            answer=AnswerBlock(
                summary=summary,
                competitors=answer_competitors,
                comparedTo=compared,
                question=action.action,
                evidence=evidence,
                sources=sources,
            ),
            missing_data=missing,
            context_update=self._context_from_dict(parser_input.context_update),
            result_counts=counts,
            operations_performed=["retrieve", "normalize"],
            entity_statuses={"competitors": statuses},
            ui_state=UIState(result_counts=counts),
        )

    def _action_missing_result(
        self,
        parser_input: ParserInput,
        field: str,
        reason: str,
    ) -> ParserOutput:
        """Return a quick, explicit missing-data result without an LLM call."""
        counts = ResultCounts(requested=1)
        return ParserOutput(
            intent=parser_input.intent,
            status="partial",
            data=self._analysis_from_dict(parser_input.current_analysis),
            answer=AnswerBlock(
                summary="The requested action has no supporting data in the current analysis."
            ),
            missing_data=[MissingData(field=field, reason=reason, severity="warning")],
            context_update=self._context_from_dict(parser_input.context_update),
            result_counts=counts,
            operations_performed=["inspect_context"],
            ui_state=UIState(result_counts=counts),
        )

    async def _exec_refine(self, parser_input: ParserInput) -> ParserOutput:
        """Modify existing data (add/remove competitor)."""
        message = (parser_input.message or "").lower()
        current = parser_input.current_analysis
        context = parser_input.context_update

        mentions = self._extract_company_mentions(
            parser_input.message or "", context, current
        )
        is_removal = any(
            w in message
            for w in ("remove", "delete", "drop", "exclude", "without")
        )

        if is_removal:
            # Remove named entities from the active set and the data.
            removed: list[str] = []
            data = self._analysis_from_dict(current)
            for company in mentions:
                slug = self._slugify(company)
                data.competitors = [
                    c
                    for c in data.competitors
                    if (c.id or self._slugify(c.name)) != slug
                ]
                removed.append(slug)
            ctx = self._mutable_context(context)
            entities = dict(ctx.get("entities") or {})
            active = [
                str(s)
                for s in entities.get("competitors") or []
                if s not in removed
            ]
            entities["competitors"] = active
            ctx["entities"] = entities
            counts = ResultCounts(
                requested=len(mentions),
                retrieved=len(active),
                valid=len(data.competitors),
                displayed=len(data.competitors),
            )
            return ParserOutput(
                intent="refine",
                status="success" if removed else "partial",
                data=data,
                missing_data=(
                    []
                    if removed
                    else [
                        MissingData(
                            field="refine",
                            reason="No named competitors found to remove",
                            severity="warning",
                        )
                    ]
                ),
                context_update=self._context_from_dict(ctx),
                result_counts=counts,
                operations_performed=["extract", "normalize"],
                entity_statuses=self._track_entity_statuses(data),
            )

        # Addition (or update): look up new companies (§5 flow),
        # merge into the current data, enforce the 3-company cap.
        resolved = await self._resolve_entities(parser_input)
        data = self._analysis_from_dict(current)
        for lc in resolved.looked_up:
            comp = self._lookup_to_competitor(lc)
            if all(
                (c.id or self._slugify(c.name)) != (comp.id or self._slugify(comp.name))
                for c in data.competitors
            ):
                data.competitors.append(comp)

        # _resolve_entities already capped the active set (and
        # recorded evictions) in its context write-back. Mirror
        # that same set onto the merged data instead of evicting
        # a second time.
        active_slugs = {
            str(s)
            for s in (
                (resolved.context_update or {}).get("entities")
                or {}
            ).get("competitors")
            or []
        }
        if active_slugs:
            data.competitors = [
                c
                for c in data.competitors
                if (c.id or self._slugify(c.name)) in active_slugs
            ]
        evicted = resolved.evicted

        counts = ResultCounts(
            requested=len(resolved.mentions),
            retrieved=len(data.competitors),
            valid=sum(
                1
                for s in self._track_entity_statuses(data)["competitors"]
                if s.status in ("complete", "partial")
            ),
            displayed=len(data.competitors),
        )
        return ParserOutput(
            intent="refine",
            status="partial" if resolved.missing_data else "success",
            data=data,
            answer=AnswerBlock(
                summary=(
                    "Added "
                    + ", ".join(e.name for e in resolved.looked_up)
                    if resolved.looked_up
                    else ""
                ),
                competitors=resolved.looked_up,
                sources=[
                    s for e in resolved.looked_up for s in e.sources
                ],
            )
            if resolved.looked_up
            else None,
            missing_data=resolved.missing_data,
            context_update=self._context_from_dict(resolved.context_update),
            evicted_entities=evicted,
            result_counts=counts,
            operations_performed=["extract", "infer", "normalize"],
            entity_statuses=self._track_entity_statuses(data),
        )

    async def _exec_compare(self, parser_input: ParserInput) -> ParserOutput:
        """Generate a side-by-side comparison (§4.3, §5.2)."""
        resolved = await self._resolve_entities(parser_input)
        entities = resolved.entities
        current = parser_input.current_analysis

        summary = ""
        if entities:
            summary = await self._compare_summary(
                entities, current, parser_input.session_id
            )

        all_sources: list[Source] = []
        for e in entities:
            all_sources.extend(e.sources)

        counts = ResultCounts(
            requested=len(resolved.mentions),
            retrieved=len(entities),
            valid=len(entities),
            displayed=len(entities),
        )
        return ParserOutput(
            intent="compare",
            status="partial" if resolved.missing_data else "success",
            data=self._analysis_from_dict(current),
            answer=AnswerBlock(
                summary=summary,
                competitors=entities[:1],
                comparedTo=entities[1:],
                sources=all_sources,
            ),
            missing_data=resolved.missing_data,
            context_update=self._context_from_dict(resolved.context_update),
            evicted_entities=resolved.evicted,
            result_counts=counts,
            operations_performed=["extract", "calculate", "normalize"],
            entity_statuses={
                "competitors": self._lookup_entity_statuses(entities)
            },
        )

    async def _exec_explain(self, parser_input: ParserInput) -> ParserOutput:
        """Drill into a single data point (§4.3, §5.2)."""
        message = parser_input.message or ""
        current = parser_input.current_analysis
        try:
            expl = await self.llmping.explain(
                message, current, session_id=parser_input.session_id
            )
        except LLMPingError as e:
            logger.warning("explain_failed", error=str(e))
            expl = {}

        evidence = [
            EvidenceItem(
                label=str(e.get("label") or ""),
                detail=str(e.get("detail") or ""),
            )
            for e in expl.get("evidence") or []
            if isinstance(e, dict)
        ]
        explanation = str(expl.get("explanation") or "")
        counts = ResultCounts(requested=1, retrieved=1 if explanation else 0,
                              valid=1 if explanation else 0,
                              displayed=1 if explanation else 0)
        return ParserOutput(
            intent="explain",
            status="success" if explanation else "partial",
            data=self._analysis_from_dict(current),
            answer=AnswerBlock(
                question=message or None,
                explanation=explanation or None,
                evidence=evidence,
            ),
            missing_data=(
                []
                if explanation
                else [
                    MissingData(
                        field="explanation",
                        reason="No explanation generated",
                        severity="warning",
                    )
                ]
            ),
            context_update=self._context_from_dict(parser_input.context_update),
            result_counts=counts,
            operations_performed=["infer"],
        )

    async def _exec_regenerate(self, parser_input: ParserInput) -> ParserOutput:
        """Regenerate a specific section (re-run bootstrap)."""
        # Regeneration is a fresh bootstrap with the same input.
        output = await self._exec_bootstrap(parser_input)
        # Preserve the original intent in the output.
        output.intent = "regenerate"
        return output

    # ── §5 "Ask About Any Company" flow ────────────────────────
    async def _resolve_entities(
        self, parser_input: ParserInput
    ) -> _ResolvedEntities:
        """Resolve every company named in the message.

        Each named entity is either `in_context` (cached profile
        from the current analysis or the 24h evicted-profile cache)
        or `needs_lookup` (fresh WebHunter search → LLMPing
        extraction). Lookups run in parallel, capped at
        `_MAX_LOOKUP_BUDGET` WebHunter searches per request (§12).
        """
        message = parser_input.message or ""
        context = parser_input.context_update
        current = parser_input.current_analysis
        industry = self._industry_from(context, current)

        resolved = _ResolvedEntities(context_update=context)

        # 1. Resolve entities from the message (§5.1 step 1).
        mentions = self._extract_company_mentions(message, context, current)
        reference = self._resolve_reference(message, context, current)
        if reference and reference not in mentions:
            mentions.append(reference)
        resolved.mentions = mentions

        # 2. Classify each entity as in-context or needs-lookup.
        to_lookup: list[str] = []
        seen_slugs: set[str] = set()
        for company in mentions:
            slug = self._slugify(company)
            # The same company can surface as a raw name, a
            # quoted name and a slug-reconstructed name —
            # classify each slug once.
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            profile = self._profile_from_current(current, slug)
            if profile is None:
                cached = self._evicted_cache_get(slug)
                if cached is not None:
                    profile = cached
            if profile is not None:
                resolved.in_context.append(
                    self._to_lookup_competitor(slug, company, profile, "context")
                )
            else:
                to_lookup.append(company)

        if not to_lookup:
            return resolved
        resolved.attempted = True

        # Preserve what the user actually asked for: a market-share
        # question must drive a market-share WebHunter query, not a
        # generic profile search (§7 entity/question alignment).
        metric = _detect_metric(message)
        if metric != "profile":
            log = logger.bind(metric=metric)
            log.info("lookup_metric_detected")

        # 3. Look up each needs-lookup entity (§5.1 steps 2-3),
        #    in parallel, under the lookup budget.
        budget = to_lookup[:_MAX_LOOKUP_BUDGET]
        results = await asyncio.gather(
            *[
                self._lookup_entity(
                    c, industry, metric, parser_input.session_id
                )
                for c in budget
            ],
            return_exceptions=True,
        )
        for company, res in zip(budget, results):
            if isinstance(res, BaseException):
                logger.warning(
                    "lookup_failed", company=company, error=str(res)
                )
                resolved.missing_data.append(
                    MissingData(
                        field=f"competitors[{company}]",
                        reason=f"Lookup failed for {company}: {res}",
                        severity="warning",
                    )
                )
                continue
            if res is None:
                # WebHunter never returned usable sources — the
                # entity failed. Skip it and report (§7.2 failure
                # isolation). Cause is genuinely ambiguous
                # (transport timeout, upstream-reported failure,
                # or zero results — WebHunter search flakiness was
                # observed to be transient), so the report names
                # all three instead of guessing "timeout".
                resolved.missing_data.append(
                    MissingData(
                        field=f"competitors[{company}]",
                        reason=(
                            f"No sources found for {company} after "
                            f"{_LOOKUP_WH_RETRIES + 1} WebHunter "
                            f"attempts (timeout, upstream error, or "
                            f"no results)"
                        ),
                        severity="warning",
                    )
                )
                continue
            if (res.lookupConfidence is not None and res.lookupConfidence < 40) or len(
                res.sources
            ) < 2:
                # §5.1 step 3: low confidence or thin sourcing →
                # partial entity, reported in missing_data.
                resolved.missing_data.append(
                    MissingData(
                        field=f"competitors[{company}]",
                        reason=(
                            f"Partial data for {company} "
                            f"(confidence={res.lookupConfidence}, "
                            f"sources={len(res.sources)})"
                        ),
                        severity="warning",
                    )
                )
            resolved.looked_up.append(res)

        # 4. Write new entities back into the context, evicting
        #    the oldest active entity when the cap is hit (§5.1
        #    step 5, §8.4).
        if resolved.looked_up:
            resolved.context_update, resolved.evicted = (
                self._update_active_entities(
                    context, current, resolved.looked_up, parser_input
                )
            )
        return resolved

    async def _lookup_entity(
        self,
        company: str,
        industry: str,
        metric: str = "profile",
        session_id: str | None = None,
    ) -> LookupCompetitor | None:
        """Compatibility wrapper for normal question lookups."""
        outcome = await self._lookup_entity_outcome(
            company, industry, metric, session_id
        )
        return outcome.competitor

    async def _lookup_entity_outcome(
        self,
        company: str,
        industry: str,
        metric: str = "profile",
        session_id: str | None = None,
    ) -> _LookupOutcome:
        try:
            async with asyncio.timeout(_LOOKUP_OPERATION_TIMEOUT_S):
                return await self._run_lookup_entity(
                    company, industry, metric, session_id
                )
        except TimeoutError:
            return _LookupOutcome(
                status="timed_out",
                reason=f"Competitor lookup timed out for {company}.",
            )

    async def _run_lookup_entity(
        self,
        company: str,
        industry: str,
        metric: str = "profile",
        session_id: str | None = None,
    ) -> _LookupOutcome:
        """Look up a single company (§5.1 steps 2-3).

        WebHunter search (2 retries / 1s) → LLMPing extraction
        (2 retries / 500ms, fallback: raw sources only). The outcome
        distinguishes an empty search from provider, timeout, and
        malformed-response failures while retaining usable evidence.
        """
        log = logger.bind(company=company, metric=metric)

        sources: list[dict[str, Any]] = []
        search_succeeded = False
        search_errors: list[str] = []
        loop_start = time.monotonic()
        for attempt in range(_LOOKUP_WH_RETRIES + 1):
            if attempt > 0 and (
                time.monotonic() - loop_start + LOOKUP_HTTP_TIMEOUT_S
                > _LOOKUP_WH_BUDGET_S
            ):
                # Budget cannot fit another full attempt — remaining
                # retries would push the request past platform
                # limits. Report what failed instead.
                log.warning(
                    "lookup_webhunter_budget_exhausted",
                    attempts_left=_LOOKUP_WH_RETRIES - attempt + 1,
                )
                break
            try:
                sources = await self.webhunter.search_company(
                    company, industry, metric
                )
                search_succeeded = True
                break
            except WebHunterError as e:
                search_errors.append(str(e))
                log.warning(
                    "lookup_webhunter_failed",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < _LOOKUP_WH_RETRIES:
                    await asyncio.sleep(_LOOKUP_WH_DELAY_S)
        if not sources:
            if search_succeeded:
                return _LookupOutcome(
                    status="not_found",
                    reason=f"No usable sources found for {company}.",
                )
            timed_out = bool(search_errors) and all(
                "timeout" in error.lower() or "timed out" in error.lower()
                for error in search_errors
            )
            status = "timed_out" if timed_out else "provider_failed"
            reason = (
                f"WebHunter timed out while researching {company}."
                if timed_out
                else f"WebHunter failed while researching {company}: "
                f"{search_errors[-1] if search_errors else 'no usable response'}"
            )
            return _LookupOutcome(status=status, reason=reason)
        if not isinstance(sources, list):
            return _LookupOutcome(
                status="malformed",
                reason=f"WebHunter returned malformed sources for {company}.",
            )
        valid_sources = [
            source for source in sources
            if isinstance(source, dict) and isinstance(source.get("url"), str) and source["url"]
        ]
        if not valid_sources:
            return _LookupOutcome(
                status="malformed",
                reason=f"WebHunter returned unusable sources for {company}.",
            )
        sources = valid_sources

        profile: dict[str, Any] | None = None
        extraction_errors: list[str] = []
        malformed_profile = False
        for attempt in range(_LOOKUP_LLM_RETRIES + 1):
            try:
                extracted = await self.llmping.extract_profile(
                    company, industry, sources, session_id=session_id
                )
                if not isinstance(extracted, dict):
                    malformed_profile = True
                    break
                profile = extracted
                break
            except LLMPingError as e:
                extraction_errors.append(str(e))
                log.warning(
                    "lookup_extract_failed",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < _LOOKUP_LLM_RETRIES:
                    await asyncio.sleep(_LOOKUP_LLM_DELAY_S)
        extraction_failed = profile is None and bool(extraction_errors)
        if profile is None:
            # §7.1 fallback: raw sources only — partial entity.
            profile = {
                "name": company,
                "description": (
                    sources[0].get("snippet", "") if sources else ""
                ),
                "confidence": 0,
                "sourceCount": len(sources),
            }

        if malformed_profile or extraction_failed:
            profile = {
                "name": company,
                "description": sources[0].get("snippet", ""),
                "confidence": 0,
                "sourceCount": len(sources),
            }
        malformed_fields: list[str] = []
        for key in (
            "name", "description", "pricingTier", "pricing_tier",
            "marketPosition", "market_position", "funding", "founded", "hq",
        ):
            value = profile.get(key)
            if value is not None and not isinstance(value, str):
                malformed_fields.append(key)
                if key == "description":
                    profile[key] = sources[0].get("snippet", "")
                elif key != "name":
                    profile[key] = ""
                else:
                    profile[key] = company
        for key in ("strengths", "weaknesses"):
            values = profile.get(key)
            if values is None:
                profile[key] = []
            elif not isinstance(values, list):
                malformed_fields.append(key)
                profile[key] = []
            else:
                valid_values = [value for value in values if isinstance(value, str)]
                if len(valid_values) != len(values):
                    malformed_fields.append(key)
                profile[key] = valid_values
        for key in ("marketShare", "market_share", "growthRate", "growth_rate"):
            value = profile.get(key)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float, str))
                or isinstance(value, str) and not value.strip()
            ):
                malformed_fields.append(key)
                profile[key] = None
        profile["sources"] = sources
        profile["confidence"] = clamp_score(profile.get("confidence"))
        profile["sourceCount"] = int(
            self._to_float(profile.get("sourceCount"), len(sources))
        )
        name = str(profile.get("name") or company)
        slug = self._slugify(name) or self._slugify(company)
        competitor = self._to_lookup_competitor(slug, name, profile, "web")
        if malformed_profile:
            return _LookupOutcome(
                status="malformed",
                competitor=competitor,
                reason=(
                    f"LLMPing returned malformed profile data for {company}; "
                    "source evidence was retained."
                ),
            )
        if malformed_fields:
            return _LookupOutcome(
                status="malformed",
                competitor=competitor,
                reason=(
                    f"LLMPing returned malformed fields for {company}: "
                    f"{', '.join(sorted(set(malformed_fields)))}; "
                    "valid profile fields and sources were retained."
                ),
            )
        if extraction_failed:
            return _LookupOutcome(
                status="provider_failed",
                competitor=competitor,
                reason=(
                    f"LLMPing profile extraction failed for {company}; "
                    f"retained available source evidence. {extraction_errors[-1]}"
                ),
            )
        if not competitor.profile.description:
            return _LookupOutcome(
                status="partial",
                competitor=competitor,
                reason=f"Profile details for {company} were incomplete.",
            )
        if (
            competitor.lookupConfidence is not None
            and competitor.lookupConfidence < 40
        ) or len(sources) < 2 or not competitor.profile.marketPosition:
            return _LookupOutcome(
                status="partial",
                competitor=competitor,
                reason=(
                    f"Partial data for {company} "
                    f"(confidence={competitor.lookupConfidence}, "
                    f"sources={len(sources)}, "
                    f"marketPosition={bool(competitor.profile.marketPosition)})."
                ),
            )
        return _LookupOutcome(status="complete", competitor=competitor)

    # ── Entity extraction & reference resolution ───────────────
    @staticmethod
    def _requested_metric_gap(
        metric: str, entities: list[LookupCompetitor]
    ) -> MissingData | None:
        """Report a user-requested metric as unavailable when the
        resolved entities carry no evidence-backed value for it.

        Mechanical check on the resolved profiles — the orchestrator
        neither guesses a value nor silently drops the question's
        data point (PARSER.md §11, ORCHESTRATOR.md §7).
        """
        if metric == "profile" or not entities:
            return None
        names = ", ".join(e.name for e in entities)
        if metric == "market_share":
            found = any(
                e.profile.marketShare is not None for e in entities
            )
            field, label = "marketShare", "market-share"
        elif metric == "pricing":
            # "unknown" is the extraction schema's explicit
            # no-data value (§5.1) — not real pricing evidence.
            found = any(
                e.profile.pricingTier
                and e.profile.pricingTier.lower() != "unknown"
                for e in entities
            )
            field, label = "pricingTier", "pricing"
        elif metric == "funding":
            found = any(e.profile.funding for e in entities)
            field, label = "funding", "funding"
        else:
            return None
        if found:
            return None
        return MissingData(
            field=field,
            reason=(
                f"No reliable {label} data found in sources "
                f"for {names}"
            ),
            severity="info",
        )

    def _extract_company_mentions(
        self,
        message: str,
        context_update: dict[str, Any] | None,
        current_analysis: dict[str, Any] | None,
    ) -> list[str]:
        """Extract company/brand mentions from a free-text message.

        Heuristic, in priority order:
          1. Quoted phrases ("tell me about \\"Fragante\\"")
          2. Names already known from the current analysis
          3. Context slugs matched against the slugified message
          4. Proper-noun sequences (capitalized word runs)
        """
        text = (message or "").strip()
        if not text:
            return []
        mentions: list[str] = []

        # 1. Quoted names.
        for quoted in re.findall(r'["\']([^"\']+)["\']', text):
            quoted = quoted.strip()
            if quoted and quoted not in mentions:
                mentions.append(quoted)

        # 2. Known competitor names from the current analysis.
        for name in self._known_competitor_names(current_analysis):
            if (
                len(name) > 1
                and name.lower() in text.lower()
                and name not in mentions
            ):
                mentions.append(name)

        # 3. Context slugs matched against the slugified message.
        slug_text = self._slugify(text)
        for slug in self._context_slugs(context_update):
            if slug and slug in slug_text:
                name = slug.replace("-", " ").title()
                if name not in mentions:
                    mentions.append(name)

        # 4. Proper-noun sequences: runs of capitalized words that
        #    are not sentence-initial (avoids "Tell"/"What" false
        #    positives) and not stopwords.
        known = {
            n.lower()
            for n in self._known_competitor_names(current_analysis)
        } | {
            s.lower()
            for s in self._context_slugs(context_update)
        }

        def _is_company_token(token: str) -> bool:
            """A capitalized token names an entity unless it is a
            stopword or a short all-caps acronym (SWOT, KPI) that
            is not a known entity."""
            lowered = token.lower().rstrip(".,;:!?")
            if lowered in _MESSAGE_STOPWORDS:
                return False
            if (
                token.isupper()
                and len(token.rstrip(".,;:!?")) <= _ACRONYM_MAX_LEN
                and lowered not in known
            ):
                return False
            return True

        tokens = re.findall(r"[A-Za-z][A-Za-z0-9&.'-]*", text)
        i = 0
        while i < len(tokens):
            if tokens[i][0].isupper() and _is_company_token(tokens[i]):
                j = i
                while (
                    j + 1 < len(tokens)
                    and tokens[j + 1][0].isupper()
                    and _is_company_token(tokens[j + 1])
                ):
                    j += 1
                if i > 0:  # skip sentence-initial words
                    # Strip sentence punctuation riding on the
                    # last token ("Spotify." → "Spotify") so the
                    # WebHunter query and relevance needle stay
                    # clean.
                    candidate = " ".join(tokens[i : j + 1]).rstrip(
                        ".,;:!?"
                    )
                    if 2 <= len(candidate) <= 60 and candidate not in mentions:
                        mentions.append(candidate)
                i = j + 1
            else:
                i += 1

        return mentions

    def _resolve_reference(
        self,
        message: str,
        context_update: dict[str, Any] | None,
        current_analysis: dict[str, Any] | None,
    ) -> str | None:
        """Resolve pronouns and relative references (§8.3).

        Returns the referenced company name, or None.
        """
        text = (message or "").lower()

        # "it" / "that one" → context.entities.focus
        if re.search(r"\b(it|that one|this one)\b", text):
            if isinstance(context_update, dict):
                focus = (context_update.get("entities") or {}).get(
                    "focus"
                )
                if focus:
                    return self._display_name(
                        str(focus), current_analysis
                    )

        # "the cheaper one" → lowest priceMonthly in the entity set
        if "cheaper" in text or "cheapest" in text:
            cheapest = self._cheapest_competitor(current_analysis)
            if cheapest:
                return cheapest

        # "the leader" → competitor with marketPosition == Leader
        if "leader" in text:
            leader = self._leader_competitor(current_analysis)
            if leader:
                return leader

        return None

    def _known_competitor_names(
        self, current_analysis: dict[str, Any] | None
    ) -> list[str]:
        if not isinstance(current_analysis, dict):
            return []
        names: list[str] = []
        for c in current_analysis.get("competitors") or []:
            if isinstance(c, dict):
                name = str(c.get("name") or "")
                if name and name not in names:
                    names.append(name)
        return names

    def _context_slugs(
        self, context_update: dict[str, Any] | None
    ) -> list[str]:
        if not isinstance(context_update, dict):
            return []
        entities = context_update.get("entities") or {}
        if not isinstance(entities, dict):
            return []
        return [str(s) for s in entities.get("competitors") or []]

    def _profile_from_current(
        self, current_analysis: dict[str, Any] | None, slug: str
    ) -> dict[str, Any] | None:
        """Find a competitor profile in the current analysis by slug."""
        if not isinstance(current_analysis, dict) or not slug:
            return None
        for c in current_analysis.get("competitors") or []:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "")
            cname = str(c.get("name") or "")
            if cid == slug or self._slugify(cname) == slug:
                return c
        return None

    def _cheapest_competitor(
        self, current_analysis: dict[str, Any] | None
    ) -> str | None:
        """Company with the lowest numeric priceMonthly (§8.3)."""
        if not isinstance(current_analysis, dict):
            return None
        best_name: str | None = None
        best_price: float | None = None
        for pt in current_analysis.get("pricingTiers") or []:
            if not isinstance(pt, dict):
                continue
            price = pt.get("priceMonthly")
            if not isinstance(price, (int, float)):
                continue
            if best_price is None or float(price) < best_price:
                best_price = float(price)
                cid = str(pt.get("competitorId") or "")
                best_name = self._name_for_slug(cid, current_analysis)
        return best_name

    def _leader_competitor(
        self, current_analysis: dict[str, Any] | None
    ) -> str | None:
        """Company with marketPosition == Leader (§8.3)."""
        if not isinstance(current_analysis, dict):
            return None
        for c in current_analysis.get("competitors") or []:
            if isinstance(c, dict) and c.get("marketPosition") == "Leader":
                return str(c.get("name") or None)
        return None

    def _name_for_slug(
        self, slug: str, current_analysis: dict[str, Any]
    ) -> str | None:
        for c in current_analysis.get("competitors") or []:
            if isinstance(c, dict):
                if str(c.get("id") or "") == slug or self._slugify(
                    str(c.get("name") or "")
                ) == slug:
                    return str(c.get("name") or "")
        return slug.replace("-", " ").title() if slug else None

    def _display_name(
        self, slug: str, current_analysis: dict[str, Any] | None
    ) -> str:
        if isinstance(current_analysis, dict):
            for c in current_analysis.get("competitors") or []:
                if isinstance(c, dict):
                    if str(c.get("id") or "") == slug or self._slugify(
                        str(c.get("name") or "")
                    ) == slug:
                        return str(c.get("name") or slug)
        return slug.replace("-", " ").title()

    def _industry_from(
        self,
        context_update: dict[str, Any] | None,
        current_analysis: dict[str, Any] | None,
    ) -> str:
        if isinstance(context_update, dict):
            industry = (context_update.get("business") or {}).get("industry")
            if industry:
                return str(industry)
        if isinstance(current_analysis, dict):
            profile = current_analysis.get("profile")
            if isinstance(profile, dict) and profile.get("industry"):
                return str(profile["industry"])
            if current_analysis.get("industry"):
                return str(current_analysis["industry"])
        return ""

    # ── Context management (§8) ────────────────────────────────
    def _update_active_entities(
        self,
        context: dict[str, Any] | None,
        current_analysis: dict[str, Any] | None,
        looked_up: list[LookupCompetitor],
        parser_input: ParserInput,
    ) -> tuple[dict[str, Any], list[EvictedEntity]]:
        """Write looked-up entities into the compact context.

        The active set is capped at 3 competitors; when a 4th is
        added the oldest active entity is evicted, reported back,
        and its profile cached for 24h (§8.4).
        """
        ctx = self._mutable_context(context)
        entities = dict(ctx.get("entities") or {})
        active = [str(s) for s in entities.get("competitors") or []]
        evicted: list[EvictedEntity] = []

        profiles_by_slug: dict[str, dict[str, Any]] = {}
        for lc in looked_up:
            profiles_by_slug[lc.id] = self._profile_dict_from_lookup(lc)

        for lc in looked_up:
            if lc.id in active:
                continue
            active.append(lc.id)
            while len(active) > _MAX_DYNAMIC_COMPANIES:
                oldest = active.pop(0)
                evicted.append(
                    EvictedEntity(
                        id=oldest,
                        name=self._display_name(oldest, current_analysis),
                    )
                )
                # §8.4: evicted profiles stay cached for 24h.
                cached = self._profile_from_current(
                    current_analysis, oldest
                ) or profiles_by_slug.get(oldest)
                if cached is not None:
                    self._evicted_cache_set(oldest, cached)

        entities["competitors"] = active
        ctx["entities"] = entities

        business = dict(ctx.get("business") or {})
        if parser_input.form_input is not None:
            business["name"] = parser_input.form_input.business_name
            business["industry"] = parser_input.form_input.industry
            if parser_input.form_input.pricing:
                business["pricing"] = parser_input.form_input.pricing
            if parser_input.form_input.business_model:
                business["model"] = parser_input.form_input.business_model
        ctx["business"] = business

        result_meta = dict(ctx.get("result_meta") or {})
        result_meta["requested_count"] = parser_input.requested_count
        result_meta["retrieved_count"] = len(active)
        ctx["result_meta"] = result_meta

        return ctx, evicted

    @staticmethod
    def _mutable_context(
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(context, dict):
            return {
                "version": 1,
                "business": {},
                "entities": {"competitors": [], "focus": None},
                "result_meta": {"requested_count": 3, "retrieved_count": 0, "filters": []},
                "constraints": {"included": [], "excluded": []},
                "keywords": [],
            }
        return dict(context)

    def _profile_dict_from_lookup(
        self, lc: LookupCompetitor
    ) -> dict[str, Any]:
        """Flatten a LookupCompetitor back into a competitor-shaped
        dict so it can be cached / merged like an in-context profile."""
        p = lc.profile
        return {
            "id": lc.id,
            "name": lc.name,
            "description": p.description,
            "pricingTier": p.pricingTier,
            "marketPosition": p.marketPosition,
            "marketShare": p.marketShare,
            "growthRate": p.growthRate,
            "strengths": p.strengths,
            "weaknesses": p.weaknesses,
            "funding": p.funding,
            "founded": p.founded,
            "hq": p.hq,
            "sources": [s.model_dump() for s in lc.sources],
        }

    def _evicted_cache_get(self, slug: str) -> dict[str, Any] | None:
        entry = _evicted_profile_cache.get(slug)
        if not entry:
            return None
        if time.time() - entry["cached_at"] > _EVICTED_TTL_S:
            _evicted_profile_cache.pop(slug, None)
            return None
        return entry["profile"]

    @staticmethod
    def _evicted_cache_set(slug: str, profile: dict[str, Any]) -> None:
        _evicted_profile_cache[slug] = {
            "profile": profile,
            "cached_at": time.time(),
        }

    # ── Validation & normalization (PARSER.md §4.1) ────────────
    def _normalize_result(self, result: AnalysisResult) -> AnalysisResult:
        """Apply §4.1 validation defaults and clamps in place."""
        self._normalize_market_share(result.competitors)
        for c in result.competitors:
            c.marketPosition = self._canon(
                c.marketPosition, MARKET_POSITIONS, "unknown"
            )
            if c.marketShare is not None:
                c.marketShare = max(0.0, min(100.0, c.marketShare))
            if c.growthRate is not None:
                c.growthRate = self._to_float(c.growthRate)
            if c.status not in ENTITY_STATUSES:
                c.status = "complete"
        for p in result.products:
            for f in p.features:
                f.maturity = self._canon(
                    f.maturity, MATURITY_LEVELS, "GA"
                )
                f.adoption = max(0.0, min(100.0, f.adoption))
        for mg in result.market_gaps:
            mg.opportunityScore = max(0.0, min(100.0, mg.opportunityScore))
            mg.difficultyScore = max(0.0, min(100.0, mg.difficultyScore))
        for ins in result.insights + result.recommendations:
            ins.category = self._canon(
                ins.category, INSIGHT_CATEGORIES, "Insight"
            )
            ins.impact = self._canon(ins.impact, IMPACT_LEVELS, "Medium")
            ins.confidence = max(0.0, min(100.0, ins.confidence))
        for ap in result.action_plan:
            ap.priority = self._canon(ap.priority, PRIORITIES, "P1")
            ap.horizon = self._canon(ap.horizon, HORIZONS, "Next")
            ap.effort = self._canon(ap.effort, EFFORT_LEVELS, "Medium")
            ap.impact = self._canon(ap.impact, IMPACT_LEVELS, "Medium")
        for rep in result.reports:
            rep.type = self._canon(rep.type, REPORT_TYPES, "Executive Summary")
        for ch in result.charts:
            ch.kind = self._canon(ch.kind, CHART_KINDS, "bar")
        return result

    def _normalize_market_share(
        self, competitors: list[Competitor]
    ) -> None:
        """Clamp shares to 0-100 and redistribute so they sum to
        100 (PARSER.md §4.1 'calculate', ±1 rounding tolerance).

        Redistribution only applies when at least two competitors
        carry a non-zero share — a real distribution. A single
        lone value (e.g. one 30% among unknowns) is evidence of
        that company's share, not of the whole market; scaling it
        to 100% would fabricate a number the sources never gave.
        """
        if not competitors:
            return
        known_shares = [c for c in competitors if c.marketShare is not None]
        for competitor in known_shares:
            competitor.marketShare = max(
                0.0, min(100.0, competitor.marketShare)
            )
        if len(known_shares) != len(competitors):
            return
        total = sum(c.marketShare for c in known_shares)
        if total <= 0:
            # No share data at all — leave the zeros. An equal
            # split would fabricate values the upstream never
            # provided (PARSER.md §11: no hallucinated filler).
            return
        non_zero = sum(1 for c in known_shares if c.marketShare > 0)
        if non_zero < 2:
            # Lone partial evidence — keep the raw clamped value.
            return
        if abs(total - 100.0) <= 1.0:
            return
        scale = 100.0 / total
        for c in known_shares:
            c.marketShare = round(c.marketShare * scale, 1)
        # Repair rounding drift on the largest share so the
        # final sum is exactly 100.
        drift = round(100.0 - sum(c.marketShare for c in competitors), 1)
        if drift:
            largest = max(known_shares, key=lambda c: c.marketShare)
            largest.marketShare = round(largest.marketShare + drift, 1)

    def _derive_charts(
        self, result: AnalysisResult
    ) -> tuple[list[ChartData], list[str]]:
        """Derive charts from real data (PARSER.md §10).

        marketShare / growthRate / pricing / featureAdoption each
        produce a bar chart and a pie chart. Entity colors are
        consistent across charts (§11.4). Empty data produces no
        chart — values are never fabricated.
        """
        charts: list[ChartData] = []
        names: list[str] = []
        colors = {c.id: c.logoColor for c in result.competitors}

        def entity_color(cid: str, fallback: str) -> str:
            return colors.get(cid) or self._color_for(fallback or cid)

        # marketShare → bar + pie
        if result.competitors and any(
            c.marketShare is not None and c.marketShare > 0
            for c in result.competitors
        ):
            points = [
                ChartPoint(
                    label=c.name,
                    value=c.marketShare,
                    color=entity_color(c.id, c.name),
                )
                for c in result.competitors
                if c.marketShare is not None
            ]
            charts.append(
                ChartData(
                    title="Market Share",
                    kind="bar",
                    xLabel="Competitor",
                    yLabel="Share (%)",
                    series=[
                        ChartSeries(
                            id="market-share",
                            name="Market Share",
                            color="#10a37f",
                            points=points,
                        )
                    ],
                )
            )
            charts.append(
                ChartData(
                    title="Market Share",
                    kind="pie",
                    xLabel="Competitor",
                    yLabel="Share (%)",
                    series=[
                        ChartSeries(
                            id="market-share-pie",
                            name="Market Share",
                            color="#10a37f",
                            points=points,
                        )
                    ],
                )
            )
            names.extend(["marketShare", "marketSharePie"])

        # growthRate → bar + pie
        if result.competitors and any(
            c.growthRate is not None and c.growthRate != 0
            for c in result.competitors
        ):
            points = [
                ChartPoint(
                    label=c.name,
                    value=c.growthRate,
                    color=entity_color(c.id, c.name),
                )
                for c in result.competitors
                if c.growthRate is not None
            ]
            charts.append(
                ChartData(
                    title="Growth Rate",
                    kind="bar",
                    xLabel="Competitor",
                    yLabel="Growth (%)",
                    series=[
                        ChartSeries(
                            id="growth",
                            name="Growth Rate",
                            color="#3b82f6",
                            points=points,
                        )
                    ],
                )
            )
            charts.append(
                ChartData(
                    title="Growth Rate",
                    kind="pie",
                    xLabel="Competitor",
                    yLabel="Growth (%)",
                    series=[
                        ChartSeries(
                            id="growth-pie",
                            name="Growth Rate",
                            color="#3b82f6",
                            points=points,
                        )
                    ],
                )
            )
            names.extend(["growth", "growthPie"])

        # pricingTiers[].priceMonthly → bar + pie
        priced = [
            pt
            for pt in result.pricing_tiers
            if isinstance(pt.priceMonthly, (int, float))
        ]
        if priced:
            points = [
                ChartPoint(
                    label=pt.name,
                    value=float(pt.priceMonthly),
                    color=entity_color(pt.competitorId, pt.name),
                )
                for pt in priced
            ]
            charts.append(
                ChartData(
                    title="Pricing",
                    kind="bar",
                    xLabel="Tier",
                    yLabel="Monthly (₹)",
                    series=[
                        ChartSeries(
                            id="pricing",
                            name="Monthly Price",
                            color="#8b5cf6",
                            points=points,
                        )
                    ],
                )
            )
            charts.append(
                ChartData(
                    title="Pricing",
                    kind="pie",
                    xLabel="Tier",
                    yLabel="Monthly (₹)",
                    series=[
                        ChartSeries(
                            id="pricing-pie",
                            name="Monthly Price",
                            color="#8b5cf6",
                            points=points,
                        )
                    ],
                )
            )
            names.extend(["pricing", "pricingPie"])

        # products[].features[].adoption → bar + pie
        feature_points: list[ChartPoint] = []
        for p in result.products:
            for f in p.features:
                if f.adoption > 0:
                    feature_points.append(
                        ChartPoint(
                            label=f.name,
                            value=f.adoption,
                            color=entity_color(p.competitorId, f.name),
                        )
                    )
        if feature_points:
            charts.append(
                ChartData(
                    title="Feature Adoption",
                    kind="bar",
                    xLabel="Feature",
                    yLabel="Adoption (%)",
                    series=[
                        ChartSeries(
                            id="feature-adoption",
                            name="Adoption",
                            color="#f59e0b",
                            points=feature_points,
                        )
                    ],
                )
            )
            charts.append(
                ChartData(
                    title="Feature Adoption",
                    kind="pie",
                    xLabel="Feature",
                    yLabel="Adoption (%)",
                    series=[
                        ChartSeries(
                            id="feature-adoption-pie",
                            name="Adoption",
                            color="#f59e0b",
                            points=feature_points,
                        )
                    ],
                )
            )
            names.extend(["featureAdoption", "featureAdoptionPie"])

        return charts, names

    # ── Status tracking ────────────────────────────────────────
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
        self,
        result: AnalysisResult,
        form_input: FormInput | None = None,
    ) -> dict[str, list[EntityStatus]]:
        """Assign per-entity status based on field completeness."""
        form_competitors = [
            self._slugify(n) for n in (form_input.competitors if form_input else []) or []
        ]
        comp_statuses: list[EntityStatus] = []
        for comp in result.competitors:
            missing = self._missing_competitor_fields(comp)
            status = (
                "failed" if "name" in missing
                else "partial" if missing
                else "complete"
            )
            slug = comp.id or self._slugify(comp.name)
            comp_statuses.append(
                EntityStatus(
                    id=slug,
                    name=comp.name,
                    status=status,
                    missing_fields=missing,
                    source=(
                        "web"
                        if comp.explanation and comp.explanation.sources
                        else "context" if slug in form_competitors else "web"
                    ),
                    lookupConfidence=None,
                )
            )
        return {"competitors": comp_statuses}

    def _lookup_entity_statuses(
        self, entities: list[LookupCompetitor]
    ) -> list[EntityStatus]:
        """Per-entity statuses for looked-up / in-context entities."""
        statuses: list[EntityStatus] = []
        for e in entities:
            missing: list[str] = []
            p = e.profile
            if not p.description:
                missing.append("description")
            if not p.pricingTier:
                missing.append("pricingTier")
            if not p.marketPosition:
                missing.append("marketPosition")
            statuses.append(
                EntityStatus(
                    id=e.id,
                    name=e.name,
                    status="partial" if missing else "complete",
                    missing_fields=missing,
                    source=e.source,
                    lookupConfidence=e.lookupConfidence,
                )
            )
        return statuses

    def _missing_competitor_fields(self, comp: Competitor) -> list[str]:
        """List required fields that are missing or invalid."""
        missing: list[str] = []
        if not comp.name:
            missing.append("name")
        if not comp.description:
            missing.append("description")
        if comp.marketPosition not in _VALID_MARKET_POSITIONS:
            missing.append("marketPosition")
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

        if not result.sources:
            missing.append(
                MissingData(
                    field="sources",
                    reason="No supporting sources were returned for this analysis.",
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
        has_critical = any(m.severity == "critical" for m in missing_data)
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
            return any(c.pricingTier for c in result.competitors)
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
        keywords = list(
            set(
                [
                    form_input.industry,
                    form_input.business_model,
                    form_input.geography,
                ]
                + (form_input.competitors or [])
            )
        )
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
                "products": [p.id for p in result.products][:5],
                "focus": None,
            },
            result_meta={
                "requested_count": requested_count,
                "retrieved_count": len(result.competitors),
                "filters": [],
            },
            constraints={
                "included": (
                    [form_input.differentiators]
                    if form_input.differentiators
                    else []
                ),
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

    def _analysis_from_dict(
        self, data: dict[str, Any] | None
    ) -> AnalysisResult:
        """Safely reconstruct AnalysisResult from a dict."""
        if not isinstance(data, dict) or not data:
            return self._empty_result()
        try:
            return AnalysisResult(
                **{
                    k: v
                    for k, v in data.items()
                    if k in AnalysisResult.model_fields
                }
            )
        except Exception:
            logger.warning("analysis_rebuild_failed")
            return self._empty_result()

    def _context_from_dict(
        self, context: dict[str, Any] | None
    ) -> ContextUpdate | None:
        """Safely reconstruct ContextUpdate from a dict."""
        if not isinstance(context, dict):
            return None
        try:
            return ContextUpdate(
                **{
                    k: v
                    for k, v in context.items()
                    if k in ContextUpdate.model_fields
                }
            )
        except Exception:
            return None

    def _empty_result(self) -> AnalysisResult:
        """Return an empty AnalysisResult placeholder."""
        return AnalysisResult()

    @staticmethod
    def _slugify(name: str) -> str:
        """Generate a URL-safe slug from a name (PARSER.md §4.1:
        `name.toLowerCase().replace(/\\s+/g, '-')`), with accents
        stripped (Café → cafe) so slugs stay ASCII."""
        if not name:
            return ""
        ascii_name = (
            unicodedata.normalize("NFKD", name.lower().strip())
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")

    @staticmethod
    def _color_for(name: str) -> str:
        """Deterministic logo color for an entity name — stable
        across charts for the same entity (PARSER.md §11.4)."""
        if not name:
            return "#10a37f"
        digest = hashlib.md5(name.lower().encode()).hexdigest()
        return f"#{digest[:6]}"

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        """Float or None — unlike _to_float, missing/invalid stays
        None so an absent metric is reported as unavailable rather
        than fabricated as 0 (PARSER.md §11: no hallucinated filler)."""
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _canon(value: Any, valid: set[str], default: str) -> str:
        """Canonicalize an enum-ish string; unknown → default."""
        if not value:
            return default
        s = str(value).strip()
        if s in valid:
            return s
        for v in valid:  # case-insensitive match
            if v.lower() == s.lower():
                return v
        return default

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
        """Validate LLMPing's response and assemble AnalysisResult.

        Tolerates partial responses: if structured keys (`competitors`,
        `swot`, etc.) are absent, the orchestrator falls back to using
        the `business_summary` / `answer` text and returns a result
        the UI can still render. Transport-level failures (timeouts,
        connection errors) still raise `LLMPingError` upstream.

        Accepts both the legacy LLMPing key style (`market_position`,
        `pricing`, `visualizations`) and the current domain style
        (`marketPosition`, `pricingTier`, `series`) — the mappers
        below normalize both into the PARSER.md §2 shapes.
        """
        missing = [k for k in OVERVIEW_REQUIRED_OUTPUTS if k not in llm_response]
        if missing:
            # Don't fail the whole call — surface as a warning. The
            # downstream ResultCounts / status logic will mark this as
            # `partial` when key sections are empty.
            log = logger.bind(missing=missing)
            log.warning("llmping_response_partial")

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

        # LLMPing output hardening: a scalar where a list/dict is
        # expected must degrade to empty, not crash response
        # construction (a string "gaps" would otherwise iterate
        # into single characters) — task: never trust LLM shape.
        market_info_raw = llm_response.get("market_info")
        market_info = (
            market_info_raw
            if isinstance(market_info_raw, dict)
            else {}
        )

        return AnalysisResult(
            business_summary=(
                str(llm_response.get("business_summary") or "")
                or str(llm_response.get("answer") or "")
            ),
            profile=profile,
            executive_summary=str(llm_response.get("executive_summary") or ""),
            market_info=market_info,
            positioning=str(llm_response.get("positioning") or ""),
            gaps=self._string_list(llm_response.get("gaps")),
            opportunities=self._string_list(
                llm_response.get("opportunities")
            ),
            risks=self._string_list(llm_response.get("risks")),
            competitors=[
                self._to_competitor(c)
                for c in llm_response.get("competitors", []) or []
                if isinstance(c, dict)
                and isinstance(c.get("name"), str)
                and c["name"].strip()
            ],
            products=[
                self._to_product(p)
                for p in llm_response.get("products", []) or []
                if isinstance(p, dict)
            ],
            pricing_tiers=[
                self._to_pricing_tier(p)
                for p in (
                    llm_response.get("pricingTiers")
                    or llm_response.get("pricing_tiers")
                    or []
                )
                if isinstance(p, dict)
            ],
            market_gaps=[
                self._to_market_gap(g)
                for g in (
                    llm_response.get("marketGaps")
                    or llm_response.get("market_gaps")
                    or []
                )
                if isinstance(g, dict)
            ],
            insights=[
                self._to_insight(i)
                for i in llm_response.get("insights", []) or []
                if isinstance(i, dict)
            ],
            recommendations=[
                self._to_insight(r, category="Recommendation")
                for r in llm_response.get("recommendations", []) or []
                if isinstance(r, dict)
            ],
            action_plan=[
                self._to_action_item(a)
                for a in llm_response.get("action_plan", []) or []
                if isinstance(a, dict)
            ],
            reports=[
                self._to_report(r)
                for r in llm_response.get("reports", []) or []
                if isinstance(r, dict)
            ],
            report=str(llm_response.get("report") or ""),
            charts=self._sanitize_charts(
                llm_response.get("visualizations")
                or llm_response.get("charts")
            ),
            metric_cards=self._sanitize_metric_cards(
                llm_response.get("metric_cards")
            ),
            swot=self._to_swot(llm_response.get("swot")),
            comparisons=[
                self._to_comparison(c)
                for c in llm_response.get("comparisons", []) or []
                if isinstance(c, dict)
            ],
            sources=self._sanitize_sources(
                llm_response.get("sources"), research
            ),
            metadata=Metadata(processing_time_ms=processing_time_ms),
            businessName=form_input.business_name,
            industry=form_input.industry,
            idea=form_input.idea,
            targetCustomers=form_input.target_customers,
            geography=form_input.geography,
            pricing=form_input.pricing,
            businessModel=form_input.business_model,
            differentiators=form_input.differentiators,
            researchGoals=form_input.research_goals or [],
        )

    # ── LLM-response mappers (old + new key styles) ────────────
    def _to_competitor(self, c: dict[str, Any]) -> Competitor:
        name = str(c.get("name") or "")
        return Competitor(
            id=str(c.get("id") or "") or self._slugify(name),
            name=name,
            logoColor=(
                str(c.get("logoColor") or c.get("logo_color") or "")
                or self._color_for(name)
            ),
            description=str(c.get("description") or ""),
            funding=(
                str(c["funding"]) if c.get("funding") is not None else None
            ),
            founded=(
                str(c["founded"]) if c.get("founded") is not None else None
            ),
            hq=(
                str(c.get("hq") or c.get("headquarters"))
                if (c.get("hq") is not None or c.get("headquarters") is not None)
                else None
            ),
            marketShare=self._optional_float(
                c.get("marketShare", c.get("market_share"))
            ),
            growthRate=self._optional_float(
                c.get("growthRate", c.get("growth_rate"))
            ),
            pricingTier=str(
                c.get("pricingTier")
                or c.get("pricing_tier")
                or c.get("pricing")
                or ""
            ),
            marketPosition=str(
                c.get("marketPosition") or c.get("market_position") or ""
            ),
            strengths=[
                str(s) for s in c.get("strengths") or []
            ],
            weaknesses=[
                str(s) for s in c.get("weaknesses") or []
            ],
            swot=self._to_swot(c.get("swot")),
            explanation=self._to_explanation(c.get("explanation")),
            status=str(c.get("status") or "complete"),
        )

    def _to_product(self, p: dict[str, Any]) -> Product:
        features = [
            ProductFeature(
                id=str(f.get("id") or ""),
                name=str(f.get("name") or ""),
                description=str(f.get("description") or ""),
                maturity=str(f.get("maturity") or ""),
                adoption=self._to_float(f.get("adoption")),
            )
            for f in p.get("features") or []
            if isinstance(f, dict)
        ]
        starting = p.get("startingPrice", p.get("starting_price"))
        return Product(
            id=str(p.get("id") or ""),
            competitorId=str(p.get("competitorId") or p.get("competitor_id") or ""),
            name=str(p.get("name") or ""),
            tagline=str(p.get("tagline") or ""),
            category=str(p.get("category") or ""),
            pricingModel=str(p.get("pricingModel") or p.get("pricing_model") or ""),
            startingPrice=(
                float(starting)
                if isinstance(starting, (int, float))
                else None
            ),
            features=features,
            status=str(p.get("status") or "complete"),
        )

    def _to_pricing_tier(self, p: dict[str, Any]) -> PricingTier:
        price = p.get("priceMonthly", p.get("price_monthly", "Custom"))
        if price is None:
            price = "Custom"
        elif isinstance(price, bool) or not isinstance(price, (int, float)):
            price = str(price)
        else:
            price = float(price)
        return PricingTier(
            id=str(p.get("id") or ""),
            competitorId=str(p.get("competitorId") or p.get("competitor_id") or ""),
            name=str(p.get("name") or ""),
            priceMonthly=price,
            billing=str(p.get("billing") or "monthly"),
            features=[str(f) for f in p.get("features") or []],
            bestFor=str(p.get("bestFor") or p.get("best_for") or ""),
            pricingModel=str(p.get("pricingModel") or p.get("pricing_model") or ""),
            highlighted=bool(p.get("highlighted", False)),
        )

    def _to_market_gap(self, g: dict[str, Any]) -> MarketGap:
        return MarketGap(
            id=str(g.get("id") or ""),
            title=str(g.get("title") or ""),
            description=str(g.get("description") or ""),
            opportunityScore=self._to_float(g.get("opportunityScore", g.get("opportunity_score"))),
            difficultyScore=self._to_float(g.get("difficultyScore", g.get("difficulty_score"))),
            estimatedRevenue=str(g.get("estimatedRevenue") or g.get("estimated_revenue") or ""),
            affectedSegments=[
                str(s) for s in g.get("affectedSegments") or g.get("affected_segments") or []
            ],
        )

    def _to_insight(
        self, i: dict[str, Any], category: str = ""
    ) -> InsightItem:
        return InsightItem(
            id=str(i.get("id") or "") or self._slugify(str(i.get("title") or "")),
            title=str(i.get("title") or ""),
            summary=str(i.get("summary") or i.get("description") or ""),
            detail=str(i.get("detail") or i.get("description") or ""),
            category=self._canon(
                category or i.get("category"), INSIGHT_CATEGORIES, "Insight"
            ),
            impact=self._canon(
                i.get("impact") or i.get("importance") or i.get("priority"),
                IMPACT_LEVELS,
                "Medium",
            ),
            confidence=self._to_float(i.get("confidence"), 50.0),
            relatedCompetitors=[
                str(s)
                for s in (
                    i.get("relatedCompetitors")
                    or i.get("related_competitors")
                    or []
                )
            ],
            explanation=str(i.get("explanation") or ""),
        )

    def _to_action_item(self, a: dict[str, Any]) -> ActionPlanItem:
        raw_priority = str(a.get("priority") or "medium").strip()
        if raw_priority in PRIORITIES:
            priority = raw_priority
        else:
            priority = {
                "high": "P0",
                "medium": "P1",
                "low": "P2",
            }.get(raw_priority.lower(), "P1")
        return ActionPlanItem(
            id=str(a.get("id") or "") or self._slugify(str(a.get("title") or a.get("action") or "")),
            title=str(a.get("title") or a.get("action") or ""),
            description=str(a.get("description") or a.get("expected_outcome") or ""),
            rationale=str(a.get("rationale") or ""),
            owner=str(a.get("owner") or ""),
            priority=priority,
            horizon=str(a.get("horizon") or ""),
            effort=str(a.get("effort") or ""),
            impact=str(a.get("impact") or a.get("priority") or ""),
        )

    def _to_report(self, r: dict[str, Any]) -> Report:
        sections = [
            ReportSection(
                heading=str(s.get("heading") or ""),
                body=str(s.get("body") or ""),
            )
            for s in r.get("sections") or []
            if isinstance(s, dict)
        ]
        pages = r.get("pages")
        return Report(
            id=str(r.get("id") or ""),
            title=str(r.get("title") or ""),
            summary=str(r.get("summary") or ""),
            type=str(r.get("type") or ""),
            date=str(r.get("date") or ""),
            pages=int(self._to_float(pages)) if pages is not None else 0,
            sections=sections,
        )

    def _to_swot(self, raw: Any) -> SWOT:
        """Accept both string arrays (new) and SWOTItem lists (old)."""
        if not isinstance(raw, dict):
            return SWOT()
        return SWOT(
            strengths=self._swot_list(raw.get("strengths")),
            weaknesses=self._swot_list(raw.get("weaknesses")),
            opportunities=self._swot_list(raw.get("opportunities")),
            threats=self._swot_list(raw.get("threats")),
        )

    @staticmethod
    def _swot_list(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                if item.strip():
                    out.append(item)
            elif isinstance(item, dict) and item.get("point"):
                out.append(str(item["point"]))
        return out

    @staticmethod
    def _string_list(raw: Any) -> list[str]:
        """String-list coercion for LLMPing fields.

        Only real lists are accepted; a scalar (e.g. the string
        "big market" where a list was expected) degrades to empty
        rather than iterating into single characters.
        """
        if not isinstance(raw, list):
            return []
        return [str(x) for x in raw if x is not None]

    def _to_explanation(self, raw: Any) -> Explanation | None:
        """Accept an Explanation dict or a plain string."""
        if raw is None or raw == "":
            return None
        if isinstance(raw, dict):
            sources = [
                self._to_source(s, idx)
                for idx, s in enumerate(raw.get("sources") or [])
            ]
            return Explanation(
                summary=str(raw.get("summary") or ""),
                whyItMatters=[
                    str(s)
                    for s in (
                        raw.get("whyItMatters")
                        or raw.get("why_it_matters")
                        or []
                    )
                ],
                evidence=[
                    EvidenceItem(
                        label=str(e.get("label") or ""),
                        detail=str(e.get("detail") or ""),
                    )
                    for e in raw.get("evidence") or []
                    if isinstance(e, dict)
                ],
                sources=[s for s in sources if s is not None],
            )
        return Explanation(summary=str(raw))

    def _to_comparison(self, c: dict[str, Any]) -> ComparisonTable:
        return ComparisonTable(
            title=str(c.get("title") or ""),
            entities=[str(e) for e in c.get("entities") or []],
            rows=[
                ComparisonRow(
                    feature=str(r.get("feature") or ""),
                    values={
                        str(k): str(v)
                        for k, v in (r.get("values") or {}).items()
                    },
                )
                for r in c.get("rows") or []
                if isinstance(r, dict)
            ],
            explanation=str(c.get("explanation") or ""),
        )

    # ── Lookup helpers ─────────────────────────────────────────
    def _to_lookup_competitor(
        self,
        slug: str,
        name: str,
        profile: dict[str, Any],
        source: str,
    ) -> LookupCompetitor:
        """Build a §5.2 LookupCompetitor from a profile dict.

        `profile` may come from the current analysis (camelCase
        competitor shape) or from LLMPing extraction (§5.1 schema)
        — both share the same field names.
        """
        lookup_confidence = None
        if source == "web":
            raw_conf = profile.get(
                "confidence", profile.get("lookupConfidence")
            )
            if raw_conf is not None:
                lookup_confidence = clamp_score(raw_conf)
        sources = [
            self._to_source(s, idx)
            for idx, s in enumerate(profile.get("sources") or [])
        ]
        return LookupCompetitor(
            id=slug,
            name=str(profile.get("name") or name),
            source=source,
            lookupConfidence=lookup_confidence,
            profile=LookupProfile(
                description=str(profile.get("description") or ""),
                pricingTier=str(
                    profile.get("pricingTier")
                    or profile.get("pricing_tier")
                    or ""
                ),
                marketPosition=str(
                    profile.get("marketPosition")
                    or profile.get("market_position")
                    or ""
                ),
                # Evidence-backed metrics: None when the extraction
                # did not find a value — never defaulted to 0.
                marketShare=self._optional_float(
                    profile.get("marketShare")
                    if "marketShare" in profile
                    else profile.get("market_share")
                ),
                growthRate=self._optional_float(
                    profile.get("growthRate")
                    if "growthRate" in profile
                    else profile.get("growth_rate")
                ),
                strengths=[
                    str(s) for s in profile.get("strengths") or []
                ][:3],
                weaknesses=[
                    str(s) for s in profile.get("weaknesses") or []
                ][:3],
                funding=(
                    str(profile["funding"])
                    if profile.get("funding") is not None
                    else None
                ),
                founded=(
                    str(profile["founded"])
                    if profile.get("founded") is not None
                    else None
                ),
                hq=(
                    str(profile.get("hq"))
                    if profile.get("hq") is not None
                    else None
                ),
            ),
            sources=[s for s in sources if s is not None],
        )

    def _lookup_to_competitor(self, lc: LookupCompetitor) -> Competitor:
        """Convert a looked-up entity into a full Competitor so a
        `refine` can merge it into the analysis data."""
        p = lc.profile
        return Competitor(
            id=lc.id,
            name=lc.name,
            logoColor=self._color_for(lc.name),
            description=p.description,
            funding=p.funding,
            founded=p.founded,
            hq=p.hq,
            # Only overwrite with evidence-backed values; the
            # Competitor contract defaults unknown shares to 0
            # (PARSER.md §4.1) but we must not turn None into 0
            # when a real value exists upstream.
            marketShare=p.marketShare,
            growthRate=p.growthRate,
            pricingTier=p.pricingTier,
            marketPosition=p.marketPosition or "unknown",
            strengths=p.strengths,
            weaknesses=p.weaknesses,
            status=(
                "complete"
                if lc.lookupConfidence is None or lc.lookupConfidence >= 40
                else "partial"
            ),
            explanation=(
                Explanation(summary=p.description, sources=lc.sources)
                if p.description or lc.sources
                else None
            ),
        )

    async def _compare_summary(
        self,
        entities: list[LookupCompetitor],
        current_analysis: dict[str, Any] | None,
        session_id: str | None,
    ) -> str:
        """Ask LLMPing to synthesize a comparison summary."""
        try:
            summary = await self.llmping.compare(
                [
                    {
                        "id": e.id,
                        "name": e.name,
                        "source": e.source,
                        "profile": e.profile.model_dump(),
                    }
                    for e in entities
                ],
                current_context=current_analysis or {},
                session_id=session_id,
            )
            if summary:
                return summary
        except LLMPingError as e:
            logger.warning("compare_summary_failed", error=str(e))
        # Mechanical fallback — data plumbing, not reasoning.
        return "Comparison of " + " vs ".join(e.name for e in entities)

    # ── Sanitizers ─────────────────────────────────────────────
    def _sanitize_charts(self, raw: Any) -> list[ChartData]:
        """Drop charts whose data is empty. Never fabricate values."""
        if not isinstance(raw, list):
            return []
        out: list[ChartData] = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            chart = self._to_chart(c)
            if chart is None:
                continue
            # Must have data to be renderable.
            has_data = any(
                bool(s.points) for s in chart.series
            )
            if not has_data:
                continue
            out.append(chart)
        return out

    def _to_chart(self, c: dict[str, Any]) -> ChartData | None:
        """Map an LLMPing chart into the PARSER.md §2.9 ChartData.

        Accepts the current shape (`kind` + `series[].points`) and
        the legacy shape (`chart_type` + `labels` + `datasets`).
        """
        if "series" in c or "kind" in c:
            series = [
                self._to_chart_series(s)
                for s in c.get("series") or []
                if isinstance(s, dict)
            ]
            return ChartData(
                title=str(c.get("title") or ""),
                kind=str(c.get("kind") or ""),
                xLabel=str(c.get("xLabel") or c.get("x_label") or ""),
                yLabel=str(c.get("yLabel") or c.get("y_label") or ""),
                series=[s for s in series if s is not None],
            )

        # Legacy shape: chart_type, labels, datasets.
        kind = str(c.get("chart_type") or "")
        labels = [str(x) for x in c.get("labels") or []]
        series: list[ChartSeries] = []
        for i, ds in enumerate(c.get("datasets") or []):
            if not isinstance(ds, dict):
                continue
            data = ds.get("data")
            if data is None:
                data = ds.get("values") or []
            name = str(ds.get("name") or ds.get("label") or f"series-{i + 1}")
            points = [
                ChartPoint(
                    label=labels[j] if j < len(labels) else f"point-{j + 1}",
                    value=float(v),
                )
                for j, v in enumerate(data)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            series.append(
                ChartSeries(
                    id=self._slugify(name) or f"series-{i + 1}",
                    name=name,
                    color=str(ds.get("color") or self._color_for(name)),
                    points=points,
                )
            )
        return ChartData(
            title=str(c.get("title") or ""),
            kind=kind,
            xLabel=str(c.get("xLabel") or c.get("x_label") or ""),
            yLabel=str(c.get("yLabel") or c.get("y_label") or ""),
            series=series,
        )

    @staticmethod
    def _to_chart_series(s: dict[str, Any]) -> ChartSeries | None:
        points = [
            ChartPoint(
                label=str(p.get("label") or ""),
                value=float(
                    Orchestrator._to_float(p.get("value"))
                ),
                color=p.get("color") or None,
            )
            for p in s.get("points") or []
            if isinstance(p, dict)
        ]
        return ChartSeries(
            id=str(s.get("id") or ""),
            name=str(s.get("name") or ""),
            color=str(s.get("color") or ""),
            points=points,
        )

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
                out.append(
                    MetricCard(
                        label=str(c.get("label") or ""),
                        value=float(value),
                        unit=str(c.get("unit") or ""),
                        change=(
                            float(c["change"])
                            if isinstance(c.get("change"), (int, float))
                            else None
                        ),
                        explanation=str(c.get("explanation") or ""),
                    )
                )
            except Exception:
                continue
        return out

    def _sanitize_sources(
        self,
        llm_sources: Any,
        research: dict[str, Any],
    ) -> list[Source]:
        """Collect sources from LLMPing + every WebHunter result."""
        out: list[Source] = []
        seen: set[str] = set()

        def add(source: Source | None) -> None:
            if source is None:
                return
            key = source.url or source.title
            if key:
                if key in seen:
                    return
                seen.add(key)
            out.append(source)

        if isinstance(llm_sources, list):
            for i, s in enumerate(llm_sources):
                add(self._to_source(s, i))
        for payload in (research or {}).values():
            if not isinstance(payload, dict):
                continue
            for i, s in enumerate(payload.get("sources") or []):
                add(self._to_source(s, i))
        return out

    @staticmethod
    def _merge_sources(
        existing: list[Source],
        additional: list[Source],
    ) -> list[Source]:
        merged: list[Source] = []
        seen: set[str] = set()
        for source in existing + additional:
            key = source.url or source.title or source.id
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(source)
        return merged

    def _to_source(self, raw: Any, idx: int) -> Source | None:
        """Map an LLMPing/WebHunter source into PARSER.md §2.10."""
        if isinstance(raw, str):
            if not raw.strip():
                return None
            return Source(
                id=f"source-{idx + 1}",
                title=raw.strip(),
                url=raw.strip(),
            )
        if not isinstance(raw, dict):
            return None
        url = str(raw.get("url") or raw.get("source") or "")
        title = str(raw.get("title") or raw.get("source") or "")
        if not url and not title:
            return None
        return Source(
            id=(
                str(raw.get("id") or "")
                or self._slugify(title)
                or f"source-{idx + 1}"
            ),
            title=title,
            url=url,
            publisher=str(raw.get("publisher") or ""),
            date=str(raw.get("date") or ""),
            snippet=str(raw.get("snippet") or ""),
        )
