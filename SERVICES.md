# Backend Services — Live Contract Documentation

This document records what the orchestrator's two independent upstream
services actually return, based on **real requests made against the live
deployments** (not documentation assumptions). It is the reference for how
the orchestrator should consume and transform each response.

- **LLMPing** — `https://llmping.onrender.com` (v0.3.0, uvicorn)
- **WebHunter** — `https://webhunter-1v83.onrender.com` (v1.1.0, uvicorn, DuckDuckGo + Playwright)

Both are fully independent services. The orchestrator only calls them over
HTTP and consumes their responses — it never embeds, rebuilds, or re-implements
either one. The only seams are `app/services/llmping_client.py` and
`app/services/webhunter_client.py`.

All examples below are actual responses captured on 2026-10-08.

---

## 1. LLMPing

### 1.1 Endpoints (from live `openapi.json`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | Primary endpoint. Send a query, get an answer. |
| `GET` | `/health` | Health + configured providers |
| `GET` | `/` | Root info |
| `GET` | `/ping` | Multi-provider probe (query params) |
| `POST` | `/ping` | Multi-provider probe (JSON body) |

### 1.2 What we send

**`POST /chat`** — the only endpoint the orchestrator uses.

```text
POST https://llmping.onrender.com/chat
Content-Type: application/json

{
    "query": "<string, required — the full natural-language prompt>",
    "session_id": "<string | null, optional — server-side conversation key>"
}
```

- Required: `query` (string)
- Optional: `session_id` (string | null)
- No auth header required (the client can send `Authorization: Bearer` if `LLMPING_API_KEY` is set, but the live service accepts requests without it)
- No `provider`/`model` field exists on the request — provider selection is internal failover

**Real request the orchestrator sends** (collapsed from its internal task
payload by `llmping_client._build_query`):

```json
{
  "query": "Perform a full competitive analysis of TestCo (SaaS). Cover executive summary, market size and growth, positioning, a SWOT, key competitors with pricing and market position, market gaps, opportunities, risks, recommendations and an action plan. Include competitor profiling for: CompA, CompB.",
  "session_id": "doc-test-full"
}
```

### 1.3 What we get back

**Success** (`200`):

```json
{
  "answer": "The capital of France is Paris.",
  "provider": "google_genai",
  "model": "gemini-2.5-flash"
}
```

- `answer` (string, required) — the model's reply. **Free text.** For
  prose prompts it is a markdown report; for JSON-requesting prompts it is
  JSON wrapped in markdown fences (see 1.4).
- `provider` (string, required) — which provider actually served the request.
  Observed values: `google_genai` (`gemini-2.5-flash`), `groq`
  (`openai/gpt-oss-20b`). The service failovers automatically.
- `model` (string, required)

**Validation error** (`422`, missing `query`):

```json
{"detail": [{"type": "missing", "loc": ["body", "query"], "msg": "Field required", "input": {}}]}
```

**`GET /health`**:

```json
{"status": "healthy", "configured_providers": ["google_genai", "mistral", "groq", "cerebras", "nvidia"]}
```

**`GET /`**: `{"service": "LLMPing", "status": "running"}`

**`GET /ping?prompt=...&provider=...&model=...`** — probes all (or one) providers:

```json
{
  "prompt": "Say hi",
  "results": [
    {"provider": "google_genai", "model": "gemini-2.5-flash", "prompt": "Say hi", "ok": true, "text": "Hi!", "status": "OK", "error": null},
    {"provider": "mistral", "model": "mistral-small-latest", "prompt": "Say hi", "ok": false, "text": "", "status": "FAILED", "error": "Error response 429 ... Rate limit exceeded ..."},
    {"provider": "groq", "model": "openai/gpt-oss-20b", "prompt": "Say hi", "ok": true, "text": "Hi! 👋", "status": "OK", "error": null},
    {"provider": "cerebras", "model": "gpt-oss-120b", "prompt": "Say hi", "ok": false, "text": "", "status": "FAILED", "error": "Error code: 402 - Payment required ..."},
    {"provider": "nvidia", "model": "openai/gpt-oss-20b", "prompt": "Say hi", "ok": true, "text": "Hello! 👋 ...", "status": "OK", "error": null}
  ]
}
```

(`POST /ping` takes the same fields as a JSON body and returns the same shape.)

### 1.4 Observed behaviors (live-verified)

1. **`answer` is always free text.** There is no structured-output mode.
   A "full analysis" prompt returned a 6,157-character markdown report.
2. **JSON requests come back markdown-fenced.** When the prompt says
   "Reply with JSON only", the answer is:
   `` ```json\n{...}\n``` `` — a bare `json.loads()` on the answer **fails**.
   The orchestrator's `_parse_json_reply` handles this: direct parse first,
   then a `\{.*\}` regex fallback. Verified working against the live service.
3. **`session_id` gives real server-side conversation continuity.** Verified:
   turn 1 "My company is called TestCo..." → turn 2 (same `session_id`)
   "What is my company called?" correctly recalled **TestCo**; a new
   `session_id` did not. The UI must persist and send `session_id` for
   follow-up flows to benefit.
4. **Provider failover is automatic** — `/chat` returned `google_genai` and
   `groq` on different calls. `/ping` showed `mistral` rate-limited (429)
   and `cerebras` billing-blocked (402) at test time.
5. Latency: simple replies < 5s; a full-analysis prompt ~10-30s.

---

## 2. WebHunter

### 2.1 Endpoints (from live `openapi.json`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/research/sync` | Synchronous research — blocks 15-90s |
| `POST` | `/research` | Async start → returns `task_id` |
| `GET` | `/research/{task_id}` | Poll async task |
| `POST` | `/scrape` | Direct page scrape (Playwright) |
| `GET` | `/health` | Health check |

Note: `GET /` has no handler (`{"detail":"Not Found"}`).

### 2.2 What we send

**`POST /research/sync`** — the endpoint the orchestrator uses.

```text
POST https://webhunter-1v83.onrender.com/research/sync
Content-Type: application/json

{
    "query": "<string, required>",
    "max_results": "<int 1-50 | null, optional>",
    "max_pages": "<int 1-20 | null, optional>",
    "variants": ["<string>", ...] | null,   // optional
    "region": "<string> | null",            // optional
    "timeout_ms": "<int 1000-180000> | null" // optional
}
```

- Required: `query`
- Optional: `max_results` (capped at 50), `max_pages` (capped at 20),
  `variants`, `region`, `timeout_ms` (capped at 180000)

**What the orchestrator actually sends today** (`webhunter_client.research`):

```json
{
  "query": "Research TestCo (industry: SaaS analytics; geography: Global; pricing: Subscription; business model: B2B SaaS). Cover: competitors and comparable companies. Return URLs, titles, snippets, and any crawled content.",
  "max_results": 8
}
```

**What the §5.1 per-company lookup sends** (`webhunter_client.search_company`):

```json
{"query": "Fragante company profile Fragrance", "max_results": 8}
```

**Validation error** (`422`, missing `query`):

```json
{"detail": [{"type": "missing", "loc": ["body", "query"], "msg": "Field required", "input": {}}]}
```

**`POST /scrape`** (not currently used by the orchestrator):

```json
{
  "url": "<string | null>",          // url OR urls required
  "urls": ["<string>"] | null,
  "timeout_ms": "<int 1000-180000> | null",
  "content_max_chars": "<int 100-500000> | null",
  "include_html": "<bool> | null",
  "include_metadata": "<bool> | null",
  "include_links": "<bool> | null"
}
```

Sending neither `url` nor `urls` → `400`:
`{"detail": [{"type": "value_error", "loc": ["body"], "msg": "Value error, either \`url\` or \`urls\` must be supplied", ...}]}`

### 2.3 What we get back

**`POST /research/sync` success** (`200`):

```json
{
  "status": "completed",
  "query": "Fragante company profile Fragrance",
  "search_queries": ["Fragante company profile Fragrance"],
  "search_results": [
    {"sub_query": "Fragante company profile Fragrance", "url": "https://...", "title": "...", "snippet": "..."}
  ],
  "crawled_contents": [
    {"url": "https://...", "sub_query": "...", "title": "...", "content": "..."}
  ],
  "stats": {"search_results_count": 8, "crawled_pages_count": 3, "elapsed_ms": 30069},
  "errors": [],
  "error": null
}
```

**`POST /research/sync` failure** (`200` with `status: "failed"` — an
upstream pipeline failure, not an HTTP error):

```json
{
  "status": "failed",
  "query": "Indian fragrance brands competitors market share",
  "search_queries": ["Indian fragrance brands competitors market share"],
  "search_results": [],
  "crawled_contents": [],
  "stats": {"search_results_count": 0, "crawled_pages_count": 0, "elapsed_ms": 7829},
  "errors": [{"stage": "search", "message": "no search results returned"}],
  "error": "no search results returned"
}
```

**`POST /research` (async start)**:

```json
{"task_id": "5facebd4-a4ee-4d3e-ad22-c950c198dfcb", "status": "running"}
```

**`GET /research/{task_id}` (poll)** — wraps the sync payload in `result`:

```json
{"status": "failed", "query": "...", "result": { /* full sync payload */ }}
```

**`POST /scrape` success**:

```json
{
  "status": "completed",
  "results": [
    {
      "url": "https://example.com",
      "status": "success",
      "title": "Example Domain",
      "content": "Example Domain\nThis domain is for use in documentation examples...",
      "error": null,
      "links": ["https://iana.org/help/example-domains"],
      "metadata": {"status_code": 200, "content_type": "text/html; charset=utf-8", "char_count": 500, "elapsed_ms": 1397}
    }
  ],
  "errors": [],
  "stats": {"total_urls": 1, "successful": 1, "...": "..."}
}
```

A per-URL scrape failure (DNS) still returns `200` with
`results[].status: "failed"` and a Playwright error string in `results[].error`.

### 2.4 Observed behaviors (live-verified)

1. **Flaky search backend.** The same query fails ~17-40% of the time with
   `status: "failed"`, `error: "no search results returned"`,
   `errors: [{"stage": "search", ...}]`, after ~8s. Retries succeed —
   this is transient DuckDuckGo-backend flakiness, not a query problem.
2. **Relevance is frequently poor.** Observed top results for
   `"Fragante company profile Fragrance"`: Cambridge/Merriam-Webster/Dictionary.com
   pages for the word **"PEAK"**; an apartment complex in Huntsville, AL;
   for `"Indian fragrance brands competitors market share"`: Wikipedia articles
   about **India** and **Indian Motorcycle**; for `"Fragrance companies in India"`:
   an 8th-grade reading passage. The orchestrator must never trust
   `search_results` blindly.
3. **`variants` are concatenated, not substituted**: the service sends
   `query + " " + variant` to the search backend (observed
   `search_queries: ["Forest Essentials", "Forest Essentials Forest Essentials company", "Forest Essentials Forest Essentials funding"]`).
4. **Timing regularly hits 30s.** Observed `elapsed_ms`: 8s (failed), 30s,
   56s. One successful call took 30,069ms — exactly at the orchestrator's
   current `webhunter_timeout = 30` setting.
5. `max_results` is honored (asked 8, got 8). `search_queries` echoes the
   effective queries (single query unless `variants` given).
6. `crawled_contents[].content` can be `"Just a moment..."` (Cloudflare
   challenge) — crawled text is not always usable.

---

## 3. How the orchestrator consumes these responses

The orchestrator is a pure orchestration layer. All service contact is
isolated in two adapter clients; nothing else in the codebase may call the
services directly.

### 3.1 WebHunter seam (`app/services/webhunter_client.py`)

| Orchestrator need | Client method | Wire request | Response transformation |
|---|---|---|---|
| Bootstrap research | `research(business, research_types)` | `POST /research/sync` `{query, max_results: 8}` | `_adapt_response` → `{results: {<topic>: {sources[], crawled[], query, stats}}, raw}` — sources built from `search_results` (url/title/snippet), crawled text from `crawled_contents` |
| Per-company lookup (§5.1) | `search_company(company, industry, max_results)` | `POST /research/sync` `{query: "<company> company profile <industry>", max_results}` | `_dedupe_sources` → top-5 `{url, title, snippet}`, deduplicated by domain |

Failure handling (verified against the live service):
- `status == "failed"` or `error` set → logged, returned as empty results
  (does **not** raise) — the orchestrator treats it as "no data".
- `httpx.TimeoutException` / `HTTPStatusError` / `RequestError` → `WebHunterError`
  (raises); a connection error also resets `base_url` so the next call
  re-runs discovery.

### 3.2 LLMPing seam (`app/services/llmping_client.py`)

| Orchestrator need | Client method | Wire request | Response transformation |
|---|---|---|---|
| Full analysis | `chat({task: "full_analysis", ...})` | `POST /chat` `{query: <NL analysis prompt>, session_id}` | `_adapt_response` copies `answer` → `business_summary`, `executive_summary`, `report` |
| Research-need decision | `chat({task: "decide_research_need", ...})` | `POST /chat` `{query: <NL prompt requesting JSON>, session_id}` | caller parses JSON from `answer` |
| Answer a question | `chat({task: "answer_question", ...})` | `POST /chat` `{query: <message + context>, session_id}` | `answer` string |
| Extract company profile (§5.1) | `extract_profile(company, industry, sources)` | `POST /chat` `{query: <strict-schema JSON prompt + top-5 sources>, session_id}` | `_parse_json_reply` — direct `json.loads`, fallback to first balanced `{...}` (handles the markdown fences LLMPing actually returns) |
| Explain / compare | `explain(...)` / `compare(...)` | `POST /chat` | `_parse_json_reply` / raw `answer` |

### 3.3 Configuration (`app/config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `LLMPING_URL` / `WEBHUNTER_URL` | `""` | Explicit upstream base URLs; when unset, lazy discovery probes the candidate lists |
| `llmping_timeout` | `60` | httpx timeout for LLMPing |
| `webhunter_timeout` | `30` | httpx timeout for WebHunter |
| `discovery_timeout_seconds` | `2.0` | Per-candidate probe timeout |
| `discovery_candidates_llmping` / `_webhunter` | `""` | Extra CSV candidates probed before the built-ins |

Built-in discovery candidates (`app/services/discovery.py`):
- LLMPing: `host.docker.internal:8000` → `llmping:8000` → `localhost:8000`
- WebHunter: `host.docker.internal:8765` → `webhunter:8000` → `localhost:8765`

⚠️ Doc/code mismatch: `DOCKER.md` documents the WebHunter container port as
`8765`, but the discovery candidate list probes `webhunter:8000` (the
`host.docker.internal`/`localhost` entries use 8765). Any Docker-network
deployment relying on the `webhunter:` hostname must actually listen on
**8000** for discovery to find it.

---

## 4. Key findings for consuming these services

These come from live testing and define how the orchestrator must behave:

1. **WebHunter's 30s sync duration vs the 30s orchestrator timeout.**
   Successful sync calls regularly take 30-56s. At `webhunter_timeout = 30`
   the orchestrator will time out on a large fraction of otherwise-successful
   research calls (raising `WebHunterError`, which bootstrap then swallows
   into "no research"). Either raise `WEBHUNTER_TIMEOUT` to ≥ 90s, constrain
   the request (`max_pages: 1`, small `max_results`), or move to the async
   `POST /research` + poll flow.
2. **WebHunter is flaky (~17-40% "no search results returned").** The
   orchestrator's retry policy (2 retries) is load-bearing; consider a third
   retry, since failures are transient and cheap (~8s each).
3. **WebHunter relevance is poor.** The §5.1 lookup flow is only sound
   because LLMPing's extraction schema carries `confidence` and `sourceCount`.
   Live-verified: junk sources (dictionary pages for "PEAK") produced
   `confidence: 0, sourceCount: 0, pricingTier: "unknown"` — and the
   orchestrator's policy (mark the entity partial + `missing_data` warning
   when `confidence < 40` or `sourceCount < 2`) correctly degrades instead
   of hallucinating a profile. **Do not relax this gating.**
4. **LLMPing always answers in free text.** JSON-typed tasks must parse the
   `answer` string (fences included) — `_parse_json_reply` does this and
   must stay in the seam.
5. **LLMPing `session_id` is real conversation state.** Follow-up flows
   (`/api/v1/chat`, `question` intent) only benefit if the UI persists and
   returns the `session_id` the orchestrator generated.
6. **Bootstrap structured fields are LLMPing-dependent.** The real LLMPing
   returns a prose markdown report for `full_analysis` — it does **not**
   return the structured `competitors` / `swot` / `charts` / `metric_cards`
   keys the orchestrator's `_build_analysis_result` reads. Against the live
   service, bootstrap therefore populates `business_summary`,
   `executive_summary`, and `report` (via `_adapt_response`) but leaves
   competitor cards, SWOT, charts, and metric cards empty. Closing that gap
   requires either a structured-output prompt on the LLMPing side or
   orchestrator-side parsing of the markdown — a deliberate design decision,
   not a bug in the seam.
7. **Provider health varies.** At test time `google_genai` and `groq` served
   traffic; `mistral` was rate-limited (429) and `cerebras` billing-blocked
   (402). `/chat` failovers automatically, so no orchestrator change is
   needed — but latency and quality will vary by provider.

---

## 5. Reproducing these probes

```bash
# LLMPing
curl -s https://llmping.onrender.com/health
curl -s -X POST https://llmping.onrender.com/chat -H 'Content-Type: application/json' \
  -d '{"query": "What is the capital of France?", "session_id": "probe-1"}'
curl -s "https://llmping.onrender.com/ping?prompt=Say%20hi"

# WebHunter
curl -s https://webhunter-1v83.onrender.com/health
curl -s -X POST https://webhunter-1v83.onrender.com/research/sync -H 'Content-Type: application/json' \
  -d '{"query": "SaaS analytics competitors", "max_results": 5, "max_pages": 1}'
curl -s -X POST https://webhunter-1v83.onrender.com/scrape -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com", "content_max_chars": 500}'
```

Expect variability: WebHunter sync calls fail intermittently and result
relevance is unstable (see §2.4).
