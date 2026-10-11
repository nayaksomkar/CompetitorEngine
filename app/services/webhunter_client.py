"""
WebHunter HTTP client.

CompetitorEngine never crawls or searches the web itself. All
external research is delegated to WebHunter via HTTP. This client is
the only seam to the research layer.

Resolution is lazy: when no `WEBHUNTER_URL` is configured, the first
outbound call probes a candidate list (host.docker.internal:8765,
webhunter:8000, localhost:8765, plus any user-supplied extras) and
caches whichever upstream answers first. See app/services/discovery.py.

Wire contract with WebHunter (observed from the live `ResearchRequest`
schema and from sampling real responses):
  POST /research/sync
  body: { "query": "<text>", "max_results": <int|null> }
  reply: {
    "status": "completed" | "running" | "failed",
    "query": "...",
    "search_queries": [...],
    "search_results": [{ "sub_query", "url", "title", "snippet" }],
    "crawled_contents": [{ "url", "title", "content", "sub_query" }],
    "stats": { ... },
    "errors": [...],
    "error": null | "..."
  }

The orchestrator passes an internal `business` dict and a list of
`research_types`. We collapse them into a single query string and
use the synchronous endpoint so the orchestrator can return a result
in a single request. The reply is mapped into a `{results: {<topic>:
{sources: [...], content: [...]}}}` shape so the orchestrator's
existing `_sanitize_sources()` and downstream code keeps working.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.services import discovery

logger = structlog.get_logger(__name__)

_BOOTSTRAP_RESEARCH_ATTEMPTS = 2
_BOOTSTRAP_RESEARCH_RETRY_DELAY_S = 0.25
_MAX_RESEARCH_CONCURRENCY = 4
_RESEARCH_BATCH_TIMEOUT_S = 90
_RESEARCH_REQUEST_TIMEOUT_S = 45
_RESEARCH_RESULT_LIMIT = 8
_RESEARCH_PAGE_LIMIT = 2


class WebHunterError(Exception):
    """Raised when WebHunter returns an error or unreachable."""


# Maps orchestrator-internal research topics onto a phrase the
# natural-language query can carry.
_RESEARCH_TYPE_PHRASES = {
    "competitor_research": "relevant competitor companies and verified company profiles",
    "product_research": "published products, features, and capabilities",
    "pricing_research": "published plans, pricing pages, and commercial terms",
    "customer_needs": "customer needs, workflow problems, and unmet needs",
    "market_gap": "market gaps, underserved segments, and opportunities",
    "market_research": "industry trends and independently sourced market data",
    "source_verification": "official product and pricing pages that verify findings",
    "customer_reviews": "customer reviews, sentiment, and common complaints",
    "swot_research": "evidence relevant to competitor strengths, weaknesses, opportunities, and threats",
}

_GENERIC_CONTEXT_WORDS = {
    "a", "an", "and", "the", "for", "to", "of", "in", "with", "by",
    "business", "businesses", "company", "companies", "platform", "software",
    "technology", "tech", "cloud", "service", "services", "solution",
    "solutions", "system", "systems", "saas", "b2b", "small", "medium",
    "sized", "operator", "operators", "customer", "customers", "management",
}
_ADULT_TERMS = re.compile(
    r"\b(?:adult|porn(?:ography)?|xxx|escort|sex\s+cam|webcam\s+sex)\b",
    re.IGNORECASE,
)
_LOGISTICS_TERMS = {
    "logistic", "logistics", "freight", "fleet", "trucking", "truck",
    "transport", "transportation", "delivery", "deliveries", "shipment",
    "shipping", "dispatch", "courier", "carrier", "parcel", "route",
    "routing", "supply chain", "last mile", "driver", "warehouse",
    "telematics", "tms",
}
_LOGISTICS_CONTEXT_RE = re.compile(
    r"\b(?:logistics?|freight|fleet|trucking|trucks?|transport(?:ation)?|"
    r"deliver(?:y|ies)|shipment|shipping|dispatch|courier|carrier|parcels?|"
    r"routes?|routing|supply[\s-]+chain|last[\s-]+mile|drivers?|warehouse|"
    r"telematics|tms)\b",
    re.IGNORECASE,
)
_TASK_CONTEXT_RE = {
    "competitor_research": re.compile(
        r"\b(?:competitor|competitors|alternative|alternatives|vendor|vendors|"
        r"provider|providers|versus|compare|comparison)\b",
        re.IGNORECASE,
    ),
    "product_research": re.compile(
        r"\b(?:product|products|feature|features|capabilit(?:y|ies)|"
        r"platform|service|services|software|tool|tools)\b",
        re.IGNORECASE,
    ),
    "pricing_research": re.compile(
        r"\b(?:pricing|price|prices|plan|plans|cost|costs|fee|fees|"
        r"subscription|billing|pricing page)\b",
        re.IGNORECASE,
    ),
    "customer_needs": re.compile(
        r"\b(?:customer|customers|workflow|workflows|need|needs|problem|"
        r"problems|challenge|challenges|pain point|unmet)\b",
        re.IGNORECASE,
    ),
    "market_gap": re.compile(
        r"\b(?:market gap|gaps|underserved|unmet|opportunity|opportunities|"
        r"segment|segments)\b",
        re.IGNORECASE,
    ),
    "market_research": re.compile(
        r"\b(?:market|industry|trend|trends|report|research|statistics|"
        r"data|forecast|growth)\b",
        re.IGNORECASE,
    ),
    "source_verification": re.compile(
        r"\b(?:official|product|pricing|features|about|company|vendor)\b",
        re.IGNORECASE,
    ),
    "customer_reviews": re.compile(
        r"\b(?:review|reviews|rating|ratings|customer|customers|feedback|"
        r"complaint|complaints)\b",
        re.IGNORECASE,
    ),
    "swot_research": re.compile(
        r"\b(?:strength|strengths|weakness|weaknesses|threat|threats|"
        r"opportunity|opportunities|differentiator|differentiators|limitation)\b",
        re.IGNORECASE,
    ),
}


def _build_query(
    business: dict[str, Any],
    research_types: list[str],
    *,
    attempt: int = 0,
) -> str:
    """Build a task-focused query from the complete submitted profile."""
    name = business.get("business_name") or "the company"
    industry = business.get("industry") or ""
    idea = str(business.get("idea") or "").strip()[:240]
    geos = business.get("geography") or ""
    model = business.get("business_model") or ""
    raw_products = business.get("products_services")
    products = [
        product.strip()[:100]
        for product in raw_products[:6]
        if isinstance(product, str) and product.strip()
    ] if isinstance(raw_products, list) else []
    target_customers = str(business.get("target_customers") or "").strip()[:200]
    competitors = business.get("competitors")
    raw_goals = business.get("research_goals")
    goals = [
        goal.strip()[:200]
        for goal in raw_goals[:5]
        if isinstance(goal, str) and goal.strip()
    ] if isinstance(raw_goals, list) else []
    competitor_names = (
        [name.strip() for name in competitors if isinstance(name, str) and name.strip()]
        if isinstance(competitors, list)
        else []
    )
    goal_clause = (
        f" User research priorities: {'; '.join(goals)}."
        if goals else ""
    )
    competitor_clause = (
        f" Requested competitors: {', '.join(competitor_names)}."
        if competitor_names else ""
    )

    topics = research_types or list(_RESEARCH_TYPE_PHRASES.keys())
    phrases = [
        _RESEARCH_TYPE_PHRASES.get(t, t.replace("_", " ")) for t in topics
    ]
    topic_clause = "; ".join(phrases)

    facts = " ".join(
        f for f in [f"industry: {industry}" if industry else "",
                    f"business: {idea}" if idea else "",
                    f"products/services: {', '.join(products)}" if products else "",
                    f"target customers: {target_customers}" if target_customers else "",
                    f"geography: {geos}" if geos else "",
                    f"business model: {model}" if model else ""]
    )
    if facts:
        facts = f" ({facts})"
    topic = topics[0] if len(topics) == 1 else ""
    focus = " ".join(products[:3]) or industry or idea[:100] or name
    location = f" {geos}" if geos else ""
    audience = f" for {target_customers}" if target_customers else ""
    variant_prefix = f"{focus}{audience}{location}"
    variant_phrases = {
        "competitor_research": (
            f"{variant_prefix} competitors and alternatives",
            f"companies providing {variant_prefix}",
        ),
        "product_research": (
            f"official {variant_prefix} product features",
            f"published capabilities and use cases for {variant_prefix}",
        ),
        "pricing_research": (
            f"{variant_prefix} published pricing and plans",
            f"official vendor pricing pages for {variant_prefix}",
        ),
        "customer_needs": (
            f"{variant_prefix} customer workflow challenges",
            f"customer needs and reviews for {variant_prefix}",
        ),
        "market_gap": (
            f"{variant_prefix} underserved segments and unmet needs",
            f"market opportunities for {variant_prefix}",
        ),
        "market_research": (
            f"{variant_prefix} industry trends and market reports",
            f"independent market data for {variant_prefix}",
        ),
        "source_verification": (
            f"official company product pages for {variant_prefix}",
            f"vendor websites and pricing pages for {variant_prefix}",
        ),
        "customer_reviews": (
            f"{variant_prefix} customer reviews and complaints",
            f"verified customer feedback about {variant_prefix}",
        ),
        "swot_research": (
            f"{variant_prefix} competitor strengths and limitations",
            f"evidence on differentiators for {variant_prefix}",
        ),
    }.get(topic, (f"{variant_prefix} {topic.replace('_', ' ')}",))
    variant = (
        variant_phrases[attempt - 1]
        if 0 < attempt <= len(variant_phrases)
        else ""
    )
    return (
        f"Research {name}{facts}.{competitor_clause}{goal_clause} "
        f"Focus on: {topic_clause}. "
        f"{variant}. "
        f"Return URLs, titles, snippets, and any crawled content."
    )


def _normalized_research_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{path}"


def _context_terms(business: dict[str, Any]) -> set[str]:
    profile_text = " ".join(
        str(business.get(key) or "")
        for key in ("industry", "idea", "target_customers")
    )
    profile_text += " " + " ".join(
        str(value)
        for value in business.get("products_services", [])
        if isinstance(value, str)
    )
    if _LOGISTICS_CONTEXT_RE.search(profile_text):
        return set(_LOGISTICS_TERMS)

    return {
        word.casefold()
        for word in re.findall(r"[a-z0-9]+", profile_text)
        if len(word) > 2 and word.casefold() not in _GENERIC_CONTEXT_WORDS
    }


def _reject_reason(
    item: dict[str, Any],
    content: str,
    context_terms: set[str],
    research_type: str = "",
) -> str | None:
    url = _normalized_research_url(item.get("url"))
    if not url:
        return "invalid_url"
    host = urlparse(url).hostname or ""
    text = " ".join(
        str(value or "")
        for value in (item.get("title"), item.get("snippet"), content, host)
    )
    if _ADULT_TERMS.search(host) or _ADULT_TERMS.search(text):
        return "adult_or_explicit_content"
    if context_terms and not any(
        re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE)
        for term in context_terms
    ):
        return "no_business_context_match"
    task_terms = _TASK_CONTEXT_RE.get(research_type)
    if task_terms and not task_terms.search(text):
        return "no_research_task_match"
    return None


def _adapt_response(
    business: dict[str, Any],
    research_types: list[str],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Retain only valid, context-relevant provider pages and their content."""
    search_results = data.get("search_results")
    crawled_contents = data.get("crawled_contents")
    if not isinstance(search_results, list) or not isinstance(crawled_contents, list):
        raise WebHunterError("WebHunter returned malformed result lists")

    context_terms = _context_terms(business)
    pages_by_url: dict[str, dict[str, Any]] = {}
    rejected: dict[str, int] = {}

    def note_rejection(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for raw in [*search_results, *crawled_contents]:
        if not isinstance(raw, dict):
            note_rejection("malformed_record")
            continue
        normalized_url = _normalized_research_url(raw.get("url"))
        if not normalized_url:
            note_rejection("invalid_url")
            continue
        page = pages_by_url.setdefault(
            normalized_url,
            {
                "source": {
                    "url": str(raw.get("url") or ""),
                    "title": str(raw.get("title") or ""),
                    "publisher": str(raw.get("publisher") or ""),
                    "date": str(raw.get("date") or ""),
                    "snippet": str(raw.get("snippet") or ""),
                    "sub_query": str(raw.get("sub_query") or ""),
                    "source": str(raw.get("url") or ""),
                    "type": "web",
                },
                "content": "",
            },
        )
        for field in ("title", "publisher", "date", "snippet", "sub_query"):
            current = page["source"].get(field) or ""
            incoming = str(raw.get(field) or "")
            if incoming and (not current or len(incoming) > len(current)):
                page["source"][field] = incoming
        content = str(raw.get("content") or "")
        if len(content) > len(page["content"]):
            page["content"] = content

    accepted_sources: list[dict[str, Any]] = []
    accepted_crawled: list[dict[str, Any]] = []
    for page in pages_by_url.values():
        source = page["source"]
        reason = _reject_reason(
            source,
            page["content"],
            context_terms,
            research_types[0] if len(research_types) == 1 else "",
        )
        if reason:
            note_rejection(reason)
            continue
        source["snippet"] = source["snippet"] or page["content"][:1200]
        accepted_sources.append(source)
        if page["content"]:
            accepted_crawled.append(
                {
                    "url": source["url"],
                    "title": source["title"],
                    "content": page["content"],
                    "sub_query": source["sub_query"],
                    "source": source["url"],
                    "type": "web",
                }
            )

    sources: list[dict[str, Any]] = []
    by_normalized_url = {
        _normalized_research_url(source["url"]): source
        for source in accepted_sources
    }
    sources.extend(by_normalized_url.values())
    results = {
        topic: {
            "sources": sources,
            "crawled": accepted_crawled,
            "query": data.get("query", ""),
            "stats": data.get("stats", {}),
        }
        for topic in research_types
    }
    return {
        "results": results,
        "raw": {
            "status": data.get("status"),
            "search_results": accepted_sources,
            "crawled_contents": accepted_crawled,
        },
        "accepted_count": len(accepted_sources),
        "rejected_count": sum(rejected.values()),
        "rejected_reasons": rejected,
        "provider_status": data.get("status"),
    }


class WebHunterClient:
    """Thin async client for the WebHunter research endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
    ):
        raw = base_url if base_url is not None else settings.webhunter_url
        self.base_url = discovery.normalize_url(raw) if raw else ""
        self.timeout = timeout or settings.webhunter_timeout

        self._client: httpx.AsyncClient | None = None
        self._resolve_lock = asyncio.Lock()

        if self.base_url:
            logger.info("webhunter_url_configured", url=self.base_url)

    def _candidates(self) -> list[str]:
        return discovery._normalize_candidates(
            settings.discovery_candidates_webhunter,
            discovery.DEFAULT_WEBHUNTER_CANDIDATES,
        )

    async def resolve(self) -> str | None:
        """Resolve and cache the upstream URL. Returns the URL or None."""
        async with self._resolve_lock:
            if self.base_url:
                return self.base_url
            url = await discovery.discover(
                self._candidates(),
                env_override=None,
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
            raise WebHunterError(
                "WebHunter URL not configured and no candidate answered"
            )
        return url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def research(
        self,
        business: dict[str, Any],
        research_types: list[str],
    ) -> dict[str, Any]:
        """Run independent, bounded research tasks and preserve partial success."""
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/research/sync"
        supported_types = list(dict.fromkeys(
            topic for topic in research_types
            if isinstance(topic, str) and topic in _RESEARCH_TYPE_PHRASES
        ))
        if not supported_types:
            supported_types = [
                "competitor_research",
                "product_research",
                "pricing_research",
                "customer_needs",
                "market_research",
                "source_verification",
            ]

        semaphore = asyncio.Semaphore(_MAX_RESEARCH_CONCURRENCY)

        async def run_limited(topic: str) -> dict[str, Any]:
            async with semaphore:
                return await self._research_topic(
                    client=client,
                    url=url,
                    business=business,
                    topic=topic,
                )

        task_map = {
            topic: asyncio.create_task(run_limited(topic))
            for topic in supported_types
        }
        started = time.monotonic()
        done, pending = await asyncio.wait(
            task_map.values(),
            timeout=_RESEARCH_BATCH_TIMEOUT_S,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        results: dict[str, dict[str, Any]] = {}
        statuses: dict[str, dict[str, Any]] = {}
        for topic, task in task_map.items():
            if task in pending:
                statuses[topic] = {
                    "status": "timed_out",
                    "duration_ms": _RESEARCH_BATCH_TIMEOUT_S * 1000,
                    "accepted": 0,
                    "rejected": 0,
                    "error": "research batch time budget exceeded",
                }
                results[topic] = {
                    "sources": [],
                    "crawled": [],
                    "query": "",
                    "stats": {},
                }
                continue
            outcome = task.result()
            statuses[topic] = {
                key: outcome[key]
                for key in (
                    "status", "duration_ms", "accepted", "rejected",
                    "error", "attempts", "rejected_reasons",
                )
            }
            results[topic] = outcome["result"]

        unique_sources: dict[str, dict[str, Any]] = {}
        unique_crawled: dict[str, dict[str, Any]] = {}
        for topic_result in results.values():
            for source in topic_result["sources"]:
                key = _normalized_research_url(source.get("url"))
                if key:
                    unique_sources.setdefault(key, source)
            for page in topic_result["crawled"]:
                key = _normalized_research_url(page.get("url"))
                if key:
                    unique_crawled.setdefault(key, page)

        accepted_count = len(unique_sources)
        rejected_count = sum(status.get("rejected", 0) for status in statuses.values())
        logger.info(
            "webhunter_research_batches_complete",
            task_count=len(supported_types),
            succeeded=sum(status["status"] == "completed" for status in statuses.values()),
            failed=sum(status["status"] != "completed" for status in statuses.values()),
            accepted_sources=accepted_count,
            rejected_results=rejected_count,
            rejection_reason_counts={
                reason: sum(
                    status.get("rejected_reasons", {}).get(reason, 0)
                    for status in statuses.values()
                )
                for reason in {
                    reason
                    for status in statuses.values()
                    for reason in status.get("rejected_reasons", {})
                }
            },
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return {
            "results": results,
            "raw": {
                "search_results": list(unique_sources.values()),
                "crawled_contents": list(unique_crawled.values()),
            },
            "batch_statuses": statuses,
        }

    async def _research_topic(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        business: dict[str, Any],
        topic: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        log = logger.bind(url=url, topic=topic)
        rejected = 0
        rejected_reasons: dict[str, int] = {}
        attempts: list[dict[str, Any]] = []
        last_error = ""

        for attempt in range(_BOOTSTRAP_RESEARCH_ATTEMPTS):
            attempt_started = time.monotonic()
            query = _build_query(business, [topic], attempt=attempt)
            body = {
                "query": query,
                "max_results": _RESEARCH_RESULT_LIMIT,
                "max_pages": _RESEARCH_PAGE_LIMIT,
                "timeout_ms": min(_RESEARCH_REQUEST_TIMEOUT_S * 1000, 180000),
            }
            log.info(
                "webhunter_research_request",
                attempt=attempt + 1,
                query_chars=len(query),
            )
            try:
                response = await client.post(
                    url,
                    json=body,
                    timeout=_RESEARCH_REQUEST_TIMEOUT_S,
                )
                response.raise_for_status()
                http_status = response.status_code
                data = response.json() if response.content else {}
                if not isinstance(data, dict):
                    raise WebHunterError("WebHunter returned a non-object response")
                provider_status = data.get("status")
                if provider_status not in {"completed", "failed", "running"}:
                    raise WebHunterError("WebHunter returned an unknown status")
                if provider_status != "completed" or data.get("error"):
                    last_error = "provider reported research failure"
                    attempts.append({
                        "attempt": attempt + 1,
                        "provider_status": str(provider_status or "unknown"),
                        "http_status": http_status,
                        "duration_ms": round(
                            (time.monotonic() - attempt_started) * 1000
                        ),
                        "accepted": 0,
                        "rejected": 0,
                        "error": last_error,
                    })
                else:
                    normalized = _adapt_response(business, [topic], data)
                    rejected += normalized["rejected_count"]
                    for reason, count in normalized["rejected_reasons"].items():
                        rejected_reasons[reason] = (
                            rejected_reasons.get(reason, 0) + count
                        )
                    accepted = normalized["accepted_count"]
                    attempts.append({
                        "attempt": attempt + 1,
                        "provider_status": "completed",
                        "http_status": http_status,
                        "duration_ms": round(
                            (time.monotonic() - attempt_started) * 1000
                        ),
                        "accepted": accepted,
                        "rejected": normalized["rejected_count"],
                        "rejected_reasons": normalized["rejected_reasons"],
                        "error": "",
                    })
                    if accepted:
                        duration_ms = round((time.monotonic() - started) * 1000)
                        log.info(
                            "webhunter_research_task_complete",
                            attempt=attempt + 1,
                            accepted_sources=accepted,
                            rejected_results=normalized["rejected_count"],
                            rejection_reason_counts=normalized["rejected_reasons"],
                            duration_ms=duration_ms,
                        )
                        return {
                            "result": normalized["results"][topic],
                            "status": "completed",
                            "duration_ms": duration_ms,
                            "accepted": accepted,
                            "rejected": normalized["rejected_count"],
                            "error": "",
                            "attempts": attempts,
                            "rejected_reasons": rejected_reasons,
                        }
                    last_error = "no relevant provider results"

                if attempt + 1 < _BOOTSTRAP_RESEARCH_ATTEMPTS:
                    log.warning(
                        "webhunter_research_retry",
                        attempt=attempt + 1,
                        reason=last_error[:180],
                    )
                    await asyncio.sleep(_BOOTSTRAP_RESEARCH_RETRY_DELAY_S)
            except httpx.TimeoutException:
                last_error = "provider request timed out"
                attempts.append({
                    "attempt": attempt + 1,
                    "provider_status": "timeout",
                    "http_status": None,
                    "duration_ms": round(
                        (time.monotonic() - attempt_started) * 1000
                    ),
                    "accepted": 0,
                    "rejected": 0,
                    "error": last_error,
                })
                if attempt + 1 < _BOOTSTRAP_RESEARCH_ATTEMPTS:
                    log.warning(
                        "webhunter_research_retry",
                        attempt=attempt + 1,
                        reason=last_error,
                    )
                    await asyncio.sleep(_BOOTSTRAP_RESEARCH_RETRY_DELAY_S)
            except httpx.HTTPStatusError as error:
                last_error = f"provider returned HTTP {error.response.status_code}"
                attempts.append({
                    "attempt": attempt + 1,
                    "provider_status": "http_error",
                    "http_status": error.response.status_code,
                    "duration_ms": round(
                        (time.monotonic() - attempt_started) * 1000
                    ),
                    "accepted": 0,
                    "rejected": 0,
                    "error": last_error,
                })
                if attempt + 1 < _BOOTSTRAP_RESEARCH_ATTEMPTS:
                    log.warning(
                        "webhunter_research_retry",
                        attempt=attempt + 1,
                        reason=last_error,
                    )
                    await asyncio.sleep(_BOOTSTRAP_RESEARCH_RETRY_DELAY_S)
            except httpx.RequestError:
                self.base_url = ""
                last_error = "provider connection failed"
                attempts.append({
                    "attempt": attempt + 1,
                    "provider_status": "connection_error",
                    "http_status": None,
                    "duration_ms": round(
                        (time.monotonic() - attempt_started) * 1000
                    ),
                    "accepted": 0,
                    "rejected": 0,
                    "error": last_error,
                })
                break
            except (ValueError, WebHunterError) as error:
                last_error = str(error)[:180]
                attempts.append({
                    "attempt": attempt + 1,
                    "provider_status": "invalid_response",
                    "accepted": 0,
                    "rejected": 0,
                    "error": last_error,
                })
                if attempt + 1 < _BOOTSTRAP_RESEARCH_ATTEMPTS:
                    log.warning(
                        "webhunter_research_retry",
                        attempt=attempt + 1,
                        reason=last_error,
                    )
                    await asyncio.sleep(_BOOTSTRAP_RESEARCH_RETRY_DELAY_S)

        duration_ms = round((time.monotonic() - started) * 1000)
        log.warning(
            "webhunter_research_task_failed",
            attempts=len(attempts),
            accepted_sources=0,
            rejected_results=rejected,
            rejection_reason_counts=rejected_reasons,
            duration_ms=duration_ms,
            reason=last_error,
        )
        return {
            "result": {
                "sources": [],
                "crawled": [],
                "query": "",
                "stats": {},
            },
            "status": "failed",
            "duration_ms": duration_ms,
            "accepted": 0,
            "rejected": rejected,
            "error": last_error,
            "attempts": attempts,
            "rejected_reasons": rejected_reasons,
        }

    # ── Per-company lookup (ORCHESTRATOR.md §5.1) ──
    async def search_company(
        self,
        company: str,
        industry: str = "",
        metric: str = "profile",
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        """Search the web for a single company profile.

        Uses the §5.1 query templates and returns the top
        sources (up to 5, deduplicated by domain, entity-
        relevant results first) as `{url, title, snippet}`
        dicts. `metric` steers the query toward what the user
        actually asked for (market share, pricing, funding).
        Raises WebHunterError on transport failures AND on
        upstream-reported failures so the orchestrator's retry
        policy applies; an empty list means "completed with no
        results".
        """
        base_url = await self._ensure_base_url()
        client = await self._get_client()
        url = f"{base_url}/research/sync"

        query = _company_query(company, industry, metric)
        log = logger.bind(url=url, company=company, metric=metric)
        # Documented WebHunter request contract (FORbacked.md §8):
        # max_results 8, max_pages 5, timeout_ms 30000. Passing
        # timeout_ms bounds a runaway crawl inside WebHunter —
        # without it an entity with thin results was observed to
        # block for >90s; with it the same query returns in ≤45s.
        body = {
            "query": query,
            "max_results": max_results,
            "max_pages": 5,
            "timeout_ms": 30000,
        }
        log.info("webhunter_company_search", query_chars=len(query))

        # Successful lookups were measured at 27-50s end to end
        # (WebHunter crawls synchronously; latency drifts between
        # runs). The default 30s client timeout truncated real
        # successful responses, so the lookup path allows 60s per
        # attempt; total retries stay bounded by the orchestrator's
        # §7.1 budget.
        try:
            response = await client.post(
                url, json=body, timeout=LOOKUP_HTTP_TIMEOUT_S
            )
            response.raise_for_status()
            data = response.json() if response.content else {}
            log.info(
                "webhunter_company_response",
                status=response.status_code,
            )
            if data.get("status") == "failed" or data.get("error"):
                # Upstream-reported failure (e.g. "no search
                # results"). Raise — not return [] — so the
                # orchestrator's retry policy applies and the
                # eventual missing_data carries the real cause.
                raise WebHunterError(
                    f"WebHunter lookup failed: "
                    f"{data.get('error') or data.get('errors')}"
                )
            return _dedupe_sources(
                data.get("search_results") or [], company=company
            )
        except httpx.TimeoutException as e:
            log.error("webhunter_company_timeout", error=str(e))
            raise WebHunterError(f"WebHunter timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            log.error(
                "webhunter_company_http_error",
                status=e.response.status_code,
                error=str(e),
            )
            raise WebHunterError(
                f"WebHunter returned {e.response.status_code}: {e}"
            ) from e
        except httpx.RequestError as e:
            self.base_url = ""
            log.error("webhunter_company_error", error=str(e))
            raise WebHunterError(f"Cannot reach WebHunter: {e}") from e
        except ValueError as e:
            log.error("webhunter_company_invalid_json", error=str(e))
            raise WebHunterError(
                f"WebHunter returned non-JSON: {e}"
            ) from e


# Per-attempt HTTP timeout for company lookups (see search_company).
# Measured successful-crawl latencies: 27-50s; 60s covers the
# observed maximum with margin without unbounding the request.
LOOKUP_HTTP_TIMEOUT_S = 60

# §5.1 query templates — the orchestrator picks the one that
# matches the user's requested metric so the question ("what is
# the market share of X") survives into the research request.
_QUERY_TEMPLATES: dict[str, str] = {
    "profile": "{company} company profile {industry}",
    "market_share": "{company} competitors market share {industry}",
    "pricing": "{company} pricing {industry}",
    "funding": "{company} funding headquarters",
}


def _company_query(
    company: str, industry: str = "", metric: str = "profile"
) -> str:
    """Build the §5.1 lookup query for a company + requested metric."""
    template = _QUERY_TEMPLATES.get(metric) or _QUERY_TEMPLATES["profile"]
    return template.format(
        company=company, industry=industry or "the industry"
    ).strip()


def _dedupe_sources(
    search_results: list[Any],
    company: str = "",
    keep: int = 5,
) -> list[dict[str, Any]]:
    """Keep the top `keep` results, deduplicated by domain.

    WebHunter returns up to 8 results in relevance order; §5.1
    keeps the top 5 by relevance with domains deduplicated.
    Results whose title/snippet/url mention the requested
    company are preferred (a result is not evidence merely
    because WebHunter returned it). When NO result mentions the
    company, only the top 2 are kept: WebHunter's search
    provably returns unrelated results on name collisions
    (e.g. "Fragante" is a Spanish/Portuguese word), and a
    bounded sample of its head relevance order is the most
    that can be passed downstream without treating junk as
    evidence. Partially relevant evidence is preserved either
    way.
    """
    needle = (company or "").strip().lower()

    def _mentions_company(r: dict[str, Any]) -> bool:
        if not needle:
            return False
        haystack = " ".join(
            str(r.get(k) or "") for k in ("title", "snippet", "url")
        ).lower()
        return needle in haystack

    relevant: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for r in search_results:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if not url:
            continue
        domain = urlparse(url).hostname or url
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        item = {
            "url": url,
            "title": r.get("title") or "",
            "snippet": r.get("snippet") or "",
        }
        (relevant if _mentions_company(item) else other).append(item)

    if relevant:
        out = relevant + other
    elif needle:
        # No name match anywhere — WebHunter's results are at
        # best tangential; keep a small head sample only.
        out = other[:2]
    else:
        # No company to match against — trust relevance order.
        out = other
    return out[:keep]
