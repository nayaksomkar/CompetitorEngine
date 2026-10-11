# ARCHITECTURE.md — CompetitorEngine

A concise walkthrough of how CompetitorEngine is put together: what each component does, how data flows through it, and the design principles that constrain it. For field-level contracts see the docs linked at the bottom.

---

## 1. System Overview

```
                    ┌─────────────────────────────────────────────┐
                    │              Frontend UI                     │
                    │  (holds context_update in sessionStorage)    │
                    └───────────────┬───────────────────────────┘
                                    │  POST /api/v1/parser/execute
                                    ▼
┌───────────────────────────────────────────────────────────────────┐
│  CompetitorEngine — FastAPI orchestrator (port 8001)              │
│                                                                   │
│  main.py ──► routers/ (overview, chat, parser)                    │
│                    │                                              │
│                    ▼                                              │
│  orchestrator.py — the only place routing decisions are made      │
│    • intent classification & planning                             │
│    • research planning (which WebHunter topics)                   │
│    • entity resolution ("Ask About Any Company")                  │
│    • validation, normalization, chart derivation                  │
│                    │                    │                          │
│         ┌──────────┘                    └──────────┐              │
│         ▼                                          ▼              │
│  services/webhunter_client.py          services/llmping_client.py   │
│  (adapter: build query / adapt reply)  (adapter: build query /    │
│                                        parse fenced JSON)         │
└───────────┬───────────────────────────────────┬────────────────────┘
            │  POST /research/sync              │  POST /chat
            ▼                                  ▼
   ┌────────────────┐                 ┌────────────────┐
   │   WebHunter    │                 │    LLMPing     │
   │ web search +   │                 │ LLM reasoning  │
   │ page harvesting│                 │ (multi-provider│
   │ (DuckDuckGo +  │                 │  failover)     │
   │  Playwright)   │                 └────────────────┘
   └────────────────┘
```

**The core rule:** the orchestrator does no reasoning, no prompting, and no scraping. All AI work is delegated to LLMPing; all web work is delegated to WebHunter. The only seams to the outside world are `app/services/webhunter_client.py` and `app/services/llmping_client.py` — nothing else in the codebase may call an upstream directly.

---

## 2. Component Layers

| Layer | Files | Responsibility |
|-------|-------|----------------|
| **App shell** | `app/main.py` | FastAPI instance, structlog config, CORS (origins hardcoded, mounted first), lifespan (warms upstream resolution), `/health`, `/`, global exception handler |
| **Config** | `app/config.py` | `pydantic-settings` `Settings` — all URLs/timeouts/CORS from env vars; optional fail-fast via `REQUIRE_SERVICE_URLS` |
| **Routers** | `app/routers/overview.py`, `chat.py`, `parser.py` | Three HTTP entry points; map upstream errors to 502, unexpected errors to 500 (parser returns a structured error envelope instead) |
| **Orchestrator** | `app/orchestrator.py` | The brain: intent routing, research planning, entity resolution, retries, per-entity failure isolation, validation/normalization, chart derivation, evidence verification |
| **Schemas** | `app/schemas/{analysis,business,domain,output,research}.py` | Pydantic v2 contracts for every request/response shape |
| **Service clients** | `app/services/{webhunter,llmping}_client.py` | Thin async adapters: `_build_query` (internal payload → wire payload) and `_adapt_response` (wire reply → internal keys) |
| **Discovery** | `app/services/discovery.py` | Probes a candidate URL list (first healthy wins); used when no explicit URL is configured |

---

## 3. Data Flows

### 3.1 Bootstrap / Overview (`intent: "bootstrap"`, `POST /api/v1/analyze`)

```
FormInput
  │
  ├─1. Plan research topics (_plan_research)
  │      default: competitor, product, pricing, customer_needs, market, source_verification
  │
  ├─2. WebHunter.research(business, research_types)
  │      → parallel per-topic POST /research/sync (bounded concurrency = 4, 90s batch budget)
  │      → _adapt_response: dedupe by URL, reject irrelevant/adult/no-context pages,
  │         merge search_results + crawled_contents into {sources[], crawled[]}
  │      → partial success preserved: a failed topic is logged and reported, not fatal
  │
  ├─3. LLMPing.chat({task: "full_analysis", context: {business, research}, ...})
  │      → _build_query collapses the payload into one natural-language prompt
  │        that demands a JSON object with evidence_urls + verbatim evidence_quotes
  │      → _adapt_response: strips markdown fences, parses the JSON, sets _evidence_required
  │
  ├─4. _build_analysis_result — validate every LLM claim against the WebHunter evidence:
  │      • evidence_urls must match URLs WebHunter actually returned
  │      • evidence_quotes must appear verbatim (≥20 chars) in the cited source text
  │      • unsupported claims/numbers are dropped, not trusted
  │
  ├─5. _normalize_result — clamp scores to 0-100, canonicalize enums,
  │      redistribute marketShare to sum 100 (only when ≥2 non-zero shares exist)
  │
  ├─6. _derive_charts — bar+pie charts from marketShare / growthRate / priceMonthly /
  │      feature adoption; empty data produces no chart (values never fabricated)
  │
  └─► AnalysisResult (the `data` block)
```

### 3.2 Parser-driven flow (`POST /api/v1/parser/execute`)

`Orchestrator.execute(parser_input)` routes by intent (or by allowlisted `action`):

| Intent | Handler | Behavior |
|--------|---------|----------|
| `bootstrap` | `_exec_bootstrap` | Full pipeline (§3.1) + per-competitor lookups for any requested competitor the LLM didn't return + company limit (3) + entity statuses |
| `question` / `follow-up` | `_exec_question` | Resolve entities (§3.3); if none needed a lookup, answer from context via LLMPing; else build an `answer` block |
| `refine` | `_exec_refine` | Remove named competitors, or look up + merge new ones (enforcing the 3-cap with eviction) |
| `compare` | `_exec_compare` | Resolve entities, LLMPing synthesizes a comparison summary, `answer.comparedTo[]` holds the rest |
| `explain` | `_exec_explain` | LLMPing `explain` task → `{explanation, evidence[]}` |
| `regenerate` | `_exec_regenerate` | Fresh bootstrap with the same input, intent echoed back |
| `action` (allowlisted) | `_exec_structured_action` | Retrieve only the named competitor's specific data (pricing, market position, strengths/weaknesses, sources, compare); no LLM chat round-trip; missing fields reported in `missing_data` |

Every handler returns a **`ParserOutput`** envelope: `{intent, status, data, answer, missing_data, context_update, evicted_entities, result_counts, entity_statuses, derived_data, ui_state}`.

### 3.3 "Ask About Any Company" flow (§5 of ORCHESTRATOR.md)

Triggered by `question`/`compare`/`refine`/`explain` when the message names a company not in the session context:

```
message
  │
  ├─1. _extract_company_mentions — quoted names → known names → context slugs →
  │     proper-noun runs (stopwords + short all-caps acronyms like SWOT/KPI excluded)
  │
  ├─2. Classify each mention: in_context (cached profile or 24h evicted cache)
  │     vs needs_lookup
  │
  ├─3. For each needs_lookup (parallel, capped at 3 per request):
  │     a. WebHunter.search_company(company, industry, metric)
  │        — metric-aware query templates (profile / market_share / pricing / funding)
  │        — 2 retries / 1s, bounded by a 130s wall-time budget
  │        — results deduped by domain, entity-mentioning results ranked first
  │     b. LLMPing.extract_profile(company, industry, top-5 sources)
  │        — strict JSON schema with confidence (0-100) + sourceCount
  │        — 2 retries / 500ms; on failure, fall back to raw sources (partial entity)
  │     c. Low confidence (<40) or thin sourcing (<2 sources) → partial + missing_data
  │
  ├─4. Merge into answer.competitors[] (source: "web", lookupConfidence badge)
  │
  └─5. Write back into context_update.entities.competitors (max 3 active);
        a 4th addition evicts the oldest (reported in evicted_entities[],
        profile cached 24h in-memory so follow-ups stay cheap)
```

### 3.4 Chat flow (`POST /api/v1/chat`)

```
message + session_id
  │
  ├─1. LLMPing chat({task: "decide_research_need"}) → {needs_research, research_plan, wants_visualizations}
  ├─2. If needed: WebHunter.research(...) (fresh evidence)
  └─3. LLMPing chat({task: "answer_question", message, current_context, fresh_research})
        → ChatResponse {session_id, answer, mini_charts, metric_cards, sources}
```

Sessions are owned by LLMPing — the orchestrator just forwards `session_id` and never stores conversation history.

---

## 4. Cross-Cutting Design Principles

**1. Pure orchestration / strict separation of concerns.** One service = one job. The orchestrator contains no reasoning, prompting, or scraping code; it only delegates via HTTP.

**2. Adapter pattern at every upstream boundary.** Each client separates (a) building the wire payload from internal vocabulary (`_build_query`) and (b) adapting the upstream reply back into internal keys (`_adapt_response`). When an upstream contract drifts, only the thin adapter changes.

**3. Never fabricate.** Missing or unsupported data is reported via `missing_data` (with the actual failure type: timeout / upstream failure / no results / malformed response). Unknown metrics stay `null`, empty charts are dropped, and a lone non-zero market share is never rescaled to 100%.

**4. Fault tolerance & failure isolation.** Retries with delays (bootstrap 2×/1s; per-entity WebHunter 2×/500ms under a 130s wall-time budget; LLMPing extraction 2×/500ms), per-entity status tracking (`complete`/`partial`/`failed`), and `status: "partial"` responses that preserve whatever succeeded. One slow or failing entity never stalls or destroys the whole response.

**5. Evidence-grounded output.** In the bootstrap flow, LLM claims survive only if their `evidence_urls` point at URLs WebHunter actually returned and their `evidence_quotes` appear verbatim in the cited source text. Unsupported claims, numbers, and quotes are stripped.

**6. Statelessness.** No database, no session store. Conversation state lives in LLMPing (`session_id`); the UI stores the compact `context_update` (business identity, active entity slugs, result meta, constraints, keywords) in `sessionStorage` and echoes it back.

**7. Fail-fast configuration (opt-in).** URLs come only from environment variables — never hardcoded (except the baked-in Render URLs in the Dockerfile). `REQUIRE_SERVICE_URLS=true` makes the service refuse to start without them; by default, lazy service discovery probes `host.docker.internal` → Docker DNS → `localhost` and caches the first healthy upstream.

**8. Defensive LLM parsing.** LLMPing returns free text (JSON wrapped in markdown fences), so `_parse_json_reply` tries a direct parse then falls back to the first balanced `{...}` block. Unexpected shapes degrade to empty rather than crashing — a scalar where a list is expected never iterates into characters.

---

## 5. Configuration & Topology

| Knob | Default | Notes |
|------|---------|-------|
| `LLMPING_URL` / `WEBHUNTER_URL` | `""` | Explicit URLs skip discovery entirely |
| `LLMPING_TIMEOUT` / `WEBHUNTER_TIMEOUT` | 60s / 75s | httpx timeouts; WebHunter's is a client-level default — per-request calls override it (research 45s under a 90s batch budget, per-company lookups 60s) |
| `REQUIRE_SERVICE_URLS` | `false` | `true` = refuse to boot without URLs |
| `DISCOVERY_CANDIDATES_*` | — | Extra CSV candidates prepended to the built-in probe list |
| `SERVICE_PORT` | 8001 | Orchestrator port |

Docker topology (see DOCKER.md): `competitorengine:8001`, `llmping:8000`, `webhunter:8765→8000`. Production (Render) bakes the deployed upstream URLs into the image via `ENV`.

---

## 6. Repository Layout

```
app/
  main.py                 # FastAPI shell, CORS, lifespan, health
  config.py               # pydantic-settings (env vars)
  orchestrator.py         # routing, research planning, entity resolution, validation
  routers/                # overview (/api/v1/analyze), chat (/api/v1/chat),
                          #   parser (/api/v1/parser/execute)
  schemas/                # analysis, business, domain, output, research contracts
  services/               # webhunter_client, llmping_client, discovery
tests/                    # pytest: API, dashboard response, parser flow,
                          #   upstream contracts (ASGI transport), resolution
```

---

## 7. Documentation Map

- **README.md** — quick start, API contract, configuration
- **ORCHESTRATOR.md** — full API contract, intent routing, lookup flow, fault tolerance, UI integration
- **PARSER.md** — UI data inventory: every field, validation rule, display location, chart derivation
- **SERVICES.md** — live wire contracts for LLMPing + WebHunter (captured from real requests)
- **DOCKER.md** — container topology, port mappings, env vars, deployment
- **FORbacked.md** — backend → UI value contract (field → component mapping, static UI values)
