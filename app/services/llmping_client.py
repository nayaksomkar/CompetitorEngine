"""
LLMPing HTTP client.

CompetitorEngine is a pure orchestrator: it never reasons, prompts,
or generates text itself. All AI work is delegated to LLMPing via
HTTP. This client is the only seam to the LLM layer.

Resolution is lazy: when no `LLMPING_URL` is configured, the first
outbound call probes a candidate list (host.docker.internal:8000,
llmping:8000, localhost:8000, plus any user-supplied extras) and
caches whichever upstream answers first. See app/services/discovery.py.

Wire contract with LLMPing (observed from the live `ChatRequest`
schema and from sampling real responses):
  POST /chat
  body:   { "query": "<prompt>", "session_id": "<uuid or empty>" }
  reply:  { "answer": "<text>", "provider": "...", "model": "..." }

The orchestrator's internal contract is richer — it builds payloads
with `task`, `context`, `required_outputs`, etc. — but only the
`message` (or a synthesised prompt) and `session_id` matter on the
wire. The adapter below collapses the orchestrator payload into a
single `query` string per task type.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import structlog

from app.config import settings
from app.services import discovery

logger = structlog.get_logger(__name__)


class LLMPingError(Exception):
    """Raised when LLMPing returns an error or unreachable."""


def _build_query(payload: dict[str, Any]) -> str:
    """Collapse an orchestrator payload into a single LLMPing query.

    The orchestrator passes structured payloads describing the task
    (full_analysis, decide_research_need, answer_question) along
    with context and meta. LLMPing wants a single natural-language
    query, so we synthesise one per task type.
    """
    task = payload.get("task", "answer_question")
    if task == "full_analysis":
        business = (payload.get("context") or {}).get("business") or {}
        name = business.get("business_name") or "the company"
        industry = business.get("industry") or business.get("idea") or "the industry"
        comps = business.get("competitors") or []
        comp_line = (
            f" Include competitor profiling for: {', '.join(comps)}."
            if comps else ""
        )
        return (
            f"Perform a full competitive analysis of {name} "
            f"({industry}). Cover executive summary, market size and "
            f"growth, positioning, a SWOT, key competitors with "
            f"pricing and market position, market gaps, opportunities, "
            f"risks, recommendations and an action plan."
            f"{comp_line}"
        )

    if task == "decide_research_need":
        message = payload.get("message") or ""
        ctx = payload.get("current_context") or {}
        ctx_brief = json.dumps(ctx, default=str)[:1200]
        return (
            f"Given this conversation context, decide whether fresh "
            f"research is needed to answer the user's next question. "
            f"Reply with JSON only: "
            f'{{"needs_research": <bool>, "research_plan": [<strings>], '
            f'"wants_visualizations": <bool>}}.\n\n'
            f"User message: {message}\n\n"
            f"Context: {ctx_brief}"
        )

    if task == "extract_profile":
        # §5.1: extract a strict competitor profile from WebHunter
        # sources. The reply must be JSON matching the extraction
        # schema so the orchestrator can validate confidence/sourceCount.
        company = payload.get("company_name") or "the company"
        industry = payload.get("industry") or ""
        sources = payload.get("sources") or []
        source_lines = []
        for s in sources[:5]:  # token budget: top 5 sources only (§12)
            if isinstance(s, dict):
                title = s.get("title") or ""
                snippet = s.get("snippet") or ""
                url = s.get("url") or ""
                source_lines.append(f"- {title}: {snippet} ({url})")
            elif isinstance(s, str):
                source_lines.append(f"- {s}")
        source_block = "\n".join(source_lines) or "(no sources)"
        return (
            f"Extract a competitor profile for {company}"
            f" (industry: {industry}) from these web sources. "
            f"Reply with JSON only, no markdown, exactly this shape: "
            f'{{"name": "{company}", "description": "1-2 sentence summary", '
            f'"pricingTier": "Premium|Mid-range|Budget|Ultra-Premium|unknown", '
            f'"marketPosition": "Leader|Challenger|Niche|Emerging|unknown", '
            f'"marketShare": <number or null>, '
            f'"growthRate": <number or null>, '
            f'"funding": "string or null", "founded": "string or null", '
            f'"hq": "string or null", '
            f'"strengths": ["up to 3"], "weaknesses": ["up to 3"], '
            f'"confidence": <0-100>, "sourceCount": <int>}}.\n\n'
            f"Sources:\n{source_block}"
        )

    if task == "explain":
        question = payload.get("question") or ""
        ctx = payload.get("current_context") or {}
        ctx_brief = json.dumps(ctx, default=str)[:1500]
        return (
            f"Explain the answer to this question based on the context. "
            f"Reply with JSON only, no markdown, exactly this shape: "
            f'{{"explanation": "clear explanation", '
            f'"evidence": [{{"label": "short label", "detail": "supporting detail"}}]}}.\n\n'
            f"Question: {question}\n\n"
            f"Context: {ctx_brief}"
        )

    if task == "compare":
        entities = payload.get("entities") or []
        names = ", ".join(
            e.get("name", "") if isinstance(e, dict) else str(e)
            for e in entities
        )
        ctx = payload.get("current_context") or {}
        entity_block = json.dumps(entities, default=str)[:1500]
        return (
            f"Compare these competitors and write a short comparison "
            f"summary: {names}. Reply with plain text (2-4 sentences), "
            f"grounded in the context below, no invented facts.\n\n"
            f"Entities: {entity_block}\n\n"
            f"Context: {json.dumps(ctx, default=str)[:800]}"
        )

    # Default: answer_question. Prefer a user message, append any
    # current context inline so LLMPing has it.
    message = payload.get("message") or ""
    ctx = payload.get("current_context") or {}
    fresh = payload.get("fresh_research") or {}
    if ctx or fresh:
        ctx_brief = json.dumps(
            {"context": ctx, "fresh_research": fresh}, default=str
        )[:2000]
        return (
            f"{message}\n\n"
            f"Relevant context (use this to ground your answer, do "
            f"not invent):\n{ctx_brief}"
        )
    return message or "Please respond."


def _adapt_response(data: dict[str, Any]) -> dict[str, Any]:
    """Map LLMPing's reply shape into the keys the orchestrator reads.

    LLMPing returns `{answer, provider, model}`. The orchestrator
    `_build_analysis_result` looks for `executive_summary`,
    `business_summary`, `competitors`, `swot`, etc. We expose the
    raw answer under multiple keys so the existing orchestrator
    code keeps working regardless of which task fired.
    """
    answer = str(data.get("answer") or "")
    adapted: dict[str, Any] = {**data}
    if answer:
        adapted.setdefault("answer", answer)
        adapted.setdefault("business_summary", answer)
        adapted.setdefault("executive_summary", answer)
        adapted.setdefault("report", answer)
    return adapted


class LLMPingClient:
    """Thin async client for the LLMPing /chat endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        api_key: str | None = None,
    ):
        raw = base_url if base_url is not None else settings.llmping_url
        self.base_url = discovery.normalize_url(raw) if raw else ""
        self.timeout = timeout or settings.llmping_timeout
        self.api_key = api_key or settings.llmping_api_key

        self._client: httpx.AsyncClient | None = None
        self._resolve_lock = asyncio.Lock()

        if self.base_url:
            logger.info("llmping_url_configured", url=self.base_url)

    def _candidates(self) -> list[str]:
        """Build the candidate list for this client."""
        return discovery._normalize_candidates(
            settings.discovery_candidates_llmping,
            discovery.DEFAULT_LLMPING_CANDIDATES,
        )

    async def resolve(self) -> str | None:
        """Resolve and cache the upstream URL. Returns the URL or None.

        Safe to call multiple times — resolution is gated by a lock
        and only the first call performs probes.
        """
        async with self._resolve_lock:
            if self.base_url:
                return self.base_url
            url = await discovery.discover(
                self._candidates(),
                env_override=None,  # explicit URL already consumed in __init__
                timeout=settings.discovery_timeout_seconds,
            )
            if url:
                self.base_url = url
            return url

    async def _ensure_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        url = await self.resolve()
        if not url:
            raise LLMPingError(
                "LLMPing URL not configured and no candidate answered"
            )
        return url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout, headers=headers
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Send a chat task to LLMPing and return its adapted JSON reply.

        `payload` is an internal orchestrator contract. This client is
        responsible for translating it into the `{query, session_id}`
        shape LLMPing expects, sending it, and adapting the response
        back into the keys the orchestrator reads.

        Internal payload keys honoured:
          - task: str ("full_analysis" | "decide_research_need" |
                       "answer_question")
          - session_id: str
          - message: str (the primary user message)
          - context: dict (business + research)
          - current_context: dict (for follow-ups)
          - fresh_research: dict (for follow-ups)

        Raises LLMPingError on transport or HTTP failures.
        """
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/chat"
        log = logger.bind(url=url, task=payload.get("task"))

        body = {
            "query": _build_query(payload),
            "session_id": payload.get("session_id") or "",
        }
        log.info("llmping_request", query_chars=len(body["query"]))

        try:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json() if response.content else {}
            log.info("llmping_response", status=response.status_code)
            return _adapt_response(data)
        except httpx.TimeoutException as e:
            log.error("llmping_timeout", error=str(e))
            raise LLMPingError(f"LLMPing timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            log.error(
                "llmping_http_error",
                status=e.response.status_code,
                error=str(e),
            )
            raise LLMPingError(
                f"LLMPing returned {e.response.status_code}: {e}"
            ) from e
        except httpx.RequestError as e:
            # Reset cached URL — next call will re-discover in case
            # the upstream just came online or moved.
            self.base_url = ""
            log.error("llmping_connection_error", error=str(e))
            raise LLMPingError(f"Cannot reach LLMPing: {e}") from e
        except ValueError as e:
            log.error("llmping_invalid_json", error=str(e))
            raise LLMPingError(f"LLMPing returned non-JSON: {e}") from e

    # ── Structured task helpers (§5.1) ───────────────
    async def extract_profile(
        self,
        company_name: str,
        industry: str,
        sources: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Extract a strict competitor profile (§5.1 schema).

        Sends the WebHunter sources to LLMPing and parses the
        JSON profile from the reply. Raises LLMPingError when
        the reply cannot be parsed as the extraction schema.
        """
        reply = await self.chat(
            {
                "task": "extract_profile",
                "company_name": company_name,
                "industry": industry,
                "sources": sources,
                "session_id": session_id or "",
            }
        )
        return _parse_json_reply(
            reply.get("answer") or "", what="profile"
        )

    async def explain(
        self,
        question: str,
        current_context: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Explain a data point (ORCHESTRATOR.md §5.2 explain)."""
        reply = await self.chat(
            {
                "task": "explain",
                "question": question,
                "current_context": current_context or {},
                "session_id": session_id or "",
            }
        )
        return _parse_json_reply(
            reply.get("answer") or "", what="explanation"
        )

    async def compare(
        self,
        entities: list[dict[str, Any]],
        current_context: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Synthesize a comparison summary over resolved entities."""
        reply = await self.chat(
            {
                "task": "compare",
                "entities": entities,
                "current_context": current_context or {},
                "session_id": session_id or "",
            }
        )
        return str(reply.get("answer") or "").strip()


def _parse_json_reply(text: str, *, what: str) -> dict[str, Any]:
    """Parse a JSON object from an LLMPing free-text reply.

    LLMPing may wrap JSON in prose or markdown fences, so we
    try a direct parse first and fall back to the first
    balanced {...} block. Raises LLMPingError when no JSON
    object can be recovered — the orchestrator's retry and
    fallback policy then applies.
    """
    raw = (text or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
    raise LLMPingError(
        f"LLMPing reply did not contain a parseable {what} JSON object"
    )
