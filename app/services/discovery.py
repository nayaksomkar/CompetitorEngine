"""
Service discovery — probe a list of candidate URLs and return the first
healthy one.

CompetitorEngine delegates research to WebHunter and reasoning to
LLMPing. Historically both URLs had to be set as environment variables
or the container refused to start. This module lets the orchestrator
discover whichever sibling service is actually reachable, so a fresh
container can boot without any service URLs configured and still find
its siblings via `host.docker.internal`, Docker DNS, or `localhost`.
"""
from __future__ import annotations

import asyncio
from typing import Iterable
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)


class ServiceURL(ValueError):
    """Raised when a configured service URL is malformed."""


def normalize_url(raw: str) -> str:
    """Return a clean base URL.

    Strips whitespace and a single trailing slash. Validates that the
    result has an http/https scheme and a non-empty host. Raises
    ``ServiceURL`` on anything that is not a usable base URL so the
    caller can fail fast instead of probing a garbage address.
    """
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ServiceURL(
            f"invalid scheme {parsed.scheme!r} in URL {raw!r} "
            f"(expected http or https)"
        )
    if not parsed.hostname:
        raise ServiceURL(f"missing host in URL {raw!r}")
    return url


# Built-in candidate order — first healthy wins.
# `host.docker.internal` covers Docker Desktop (mac/win) and Linux with
# the `--add-host=host.docker.internal:host-gateway` flag. The plain
# service names (`llmping`, `webhunter`) cover a shared user-defined
# Docker network. `localhost` covers running everything bare-metal.
DEFAULT_LLMPING_CANDIDATES: tuple[str, ...] = (
    "http://host.docker.internal:8000",
    "http://llmping:8000",
    "http://localhost:8000",
)

DEFAULT_WEBHUNTER_CANDIDATES: tuple[str, ...] = (
    "http://host.docker.internal:8765",
    "http://webhunter:8000",
    "http://localhost:8765",
)


def _normalize_candidates(
    extra_csv: str | None,
    defaults: Iterable[str],
) -> list[str]:
    """Merge user-supplied CSV candidates with built-in defaults.

    User-supplied candidates come first so operators can pre-pend a
    preferred host without touching code.
    """
    out: list[str] = []
    if extra_csv:
        for raw in extra_csv.split(","):
            url = raw.strip().rstrip("/")
            if url:
                out.append(url)
    for d in defaults:
        url = d.rstrip("/")
        if url and url not in out:
            out.append(url)
    return out


async def _probe(url: str, timeout: float) -> bool:
    """Return True if `url` accepts the connection and replies HTTP.

    Any HTTP response — even 404 — proves the host is alive and
    answering on this port. Only transport-level failures
    (DNS error, connection refused, timeout) count as unhealthy.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return True
    except httpx.HTTPError:
        return False


async def discover(
    candidates: Iterable[str],
    *,
    env_override: str | None = None,
    timeout: float = 2.0,
) -> str | None:
    """Return the first reachable URL among `candidates`.

    If `env_override` is a non-empty string, it is probed first —
    explicit configuration always wins over discovery. Returns `None`
    when nothing answers.
    """
    ordered: list[str] = []
    if env_override:
        ordered.append(env_override.rstrip("/"))
    for c in candidates:
        url = c.rstrip("/")
        if url and url not in ordered:
            ordered.append(url)

    if not ordered:
        return None

    log = logger.bind(candidates=ordered, timeout=timeout)
    log.info("service_discovery_started")

    # Probe serially — fastest-path to the first healthy answer. Probes
    # are short (default 2s) so total worst-case latency is bounded.
    for url in ordered:
        if await _probe(url, timeout):
            log.info("service_discovery_resolved", url=url)
            return url

    log.warning("service_discovery_failed")
    return None


# Re-export so callers don't need to import the helper separately.
async def discover_all(
    *,
    llmping_override: str | None = None,
    webhunter_override: str | None = None,
    llmping_candidates: Iterable[str] = DEFAULT_LLMPING_CANDIDATES,
    webhunter_candidates: Iterable[str] = DEFAULT_WEBHUNTER_CANDIDATES,
    timeout: float = 2.0,
) -> dict[str, str | None]:
    """Resolve both services in parallel. Returns {name: url-or-None}."""
    llmping_url, webhunter_url = await asyncio.gather(
        discover(
            llmping_candidates,
            env_override=llmping_override,
            timeout=timeout,
        ),
        discover(
            webhunter_candidates,
            env_override=webhunter_override,
            timeout=timeout,
        ),
    )
    return {"llmping": llmping_url, "webhunter": webhunter_url}
