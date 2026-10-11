# CompetitorEngine

CompetitorEngine is a **pure orchestrator** microservice for competitive analysis. It does **no** reasoning, **no** prompting, and **no** web scraping itself. It sits between a frontend UI and two independent upstream services, decides what information a request needs, delegates the work over HTTP, and returns clean, structured, frontend-ready JSON.

| Service | Role | Endpoint used |
|---------|------|---------------|
| **CompetitorEngine** (this repo) | Orchestration, routing, validation, fault tolerance | — |
| **WebHunter** | Web search + page harvesting (external research) | `POST /research/sync` |
| **LLMPing** | LLM reasoning over the gathered context | `POST /chat` |

```
UI ──► Orchestrator (port 8001) ──► WebHunter (research)
                      │
                      └──► LLMPing (reasoning)
```

The orchestrator is **stateless**: conversation state lives in LLMPing (`session_id`) and the UI stores a compact `context_update` in `sessionStorage` and echoes it back on follow-ups.

---

## How It Works

1. The UI sends a request (form data or a natural-language intent).
2. The orchestrator plans the work: which research topics to request from WebHunter, what to ask LLMPing.
3. WebHunter returns sourced evidence (URLs, titles, snippets, crawled content).
4. LLMPing synthesizes that evidence into a structured analysis — every claim must be backed by a cited, verifiable source quote.
5. The orchestrator validates, normalizes, and derives charts from the real data, then returns the result.

If a named company is not already in the session context, the orchestrator searches WebHunter for it, asks LLMPing to extract a strict profile, and returns it in an `answer` block — all without the UI needing to know where the data came from.

**The orchestrator never fabricates data.** Missing fields are reported in `missing_data`; failed entities are isolated so one failure never breaks the whole response (`status: "partial"`).

---

## Architecture

| Layer | Path | Responsibility |
|-------|------|----------------|
| API | `app/main.py`, `app/routers/` | FastAPI app, CORS, health, three routers |
| Orchestrator | `app/orchestrator.py` | Intent routing, research planning, entity resolution, validation, normalization, chart derivation |
| Schemas | `app/schemas/` | Pydantic v2 request/response contracts |
| Service clients | `app/services/` | Thin HTTP adapters to WebHunter + LLMPing (the only seams to upstreams) |
| Discovery | `app/services/discovery.py` | Auto-discovers sibling containers when URLs are unset |

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full component/data-flow walkthrough.

---

## API Contract

### `GET /health`
```json
{ "status": "ok", "service": "orchestrator", "version": "2.1.0" }
```

### `POST /api/v1/parser/execute` (primary endpoint)
Request:
```json
{
  "parser_input": {
    "intent": "bootstrap",
    "message": "tell me about Fragante",
    "session_id": "uuid",
    "context_update": { "/* last response's context_update */": null },
    "current_analysis": { "/* last full data payload */": null },
    "form_input": { "business_name": "MyCo", "idea": "AI analytics", "industry": "SaaS" },
    "requested_count": 3
  }
}
```
`intent` is one of `bootstrap`, `question`, `follow-up`, `refine`, `compare`, `explain`, `regenerate`; or send an allowlisted `action` object (`{action, entity, section, target?}`) for structured dashboard actions.

Response:
```json
{
  "intent": "bootstrap",
  "status": "success | partial | error",
  "data": { "competitors": [ /* ... */ ], "swot": {}, "charts": [], "sources": [] },
  "answer": null,
  "missing_data": [{ "field": "competitors[X]", "reason": "...", "severity": "warning" }],
  "context_update": { "/* compact, store in sessionStorage */": null },
  "evicted_entities": [],
  "result_counts": { "requested": 3, "retrieved": 3, "valid": 3, "displayed": 3 },
  "entity_statuses": { "competitors": [{ "id": "x", "name": "X", "status": "complete", "missing_fields": [], "source": "web", "lookupConfidence": 78 }] }
}
```

- `bootstrap`/`refine`/`regenerate` render from `data.*`; `question`/`compare`/`explain` render from `answer.*`.
- `lookupConfidence < 60` → render a subtle confidence badge for web-sourced entities.
- `missing_data` with `severity: "warning"` → show warning notifications.

### `POST /api/v1/analyze` (legacy overview)
Submit a `FormInput` (`business_name`, `idea`, `industry` required) and get back the full `data` payload directly (no envelope).

### `POST /api/v1/chat` (legacy chat)
`{session_id, message, current_analysis, fresh_research}` → `{session_id, answer, mini_charts, metric_cards, sources}`. The orchestrator asks LLMPing whether fresh research is needed, calls WebHunter only if so.

Full field-by-field contracts: **[ORCHESTRATOR.md](ORCHESTRATOR.md)** (API), **[PARSER.md](PARSER.md)** (UI data inventory), **[FORbacked.md](FORbacked.md)** (backend→UI mapping).

---

## Configuration

All configuration comes from environment variables (`.env` / `.env.example`). URLs are optional: when unset, the orchestrator auto-discovers sibling containers at runtime.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLMPING_URL` / `WEBHUNTER_URL` | `""` | Explicit upstream base URLs; when set, discovery is skipped entirely |
| `LLMPING_TIMEOUT` / `WEBHUNTER_TIMEOUT` | `60` / `75` | httpx timeouts; WebHunter's is a client-level default — per-request calls override it (research 45s under a 90s batch budget, per-company lookups 60s) |
| `LLMPING_API_KEY` | — | Sent as `Authorization: Bearer ...` |
| `REQUIRE_SERVICE_URLS` | `false` | `true` = refuse to start without URLs (fail fast) |
| `DISCOVERY_CANDIDATES_LLMPING` / `_WEBHUNTER` | — | Extra CSV candidates probed before the built-ins |
| `SERVICE_HOST` / `SERVICE_PORT` | `0.0.0.0` / `8001` | Bind address / port |

CORS origins are hardcoded in `app/main.py` (`https://nayaksomkar.github.io`, `http://localhost:5173`, `http://localhost:3000`).

Discovery order (when URLs are unset): explicit URL → `host.docker.internal` → Docker DNS service name → `localhost`.

---

## Run Locally

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Run with Docker

```bash
docker build -t competitorengine:latest .
docker run -d -p 8001:8001 --env-file .env competitorengine:latest
```

See **[DOCKER.md](DOCKER.md)** for the full three-container topology (llmping:8000, webhunter:8765→8000, competitorengine:8001) and production (Render) wiring.

## Testing

```bash
pytest tests/ -v
```

---

## Documentation Map

| Doc | Covers |
|-----|--------|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | System overview, components, data flows, design principles |
| **[ORCHESTRATOR.md](ORCHESTRATOR.md)** | API contract, intent routing, "Ask About Any Company" flow, fault tolerance |
| **[PARSER.md](PARSER.md)** | UI data inventory — every field, validation rule, display location |
| **[SERVICES.md](SERVICES.md)** | Live wire contracts for LLMPing + WebHunter (observed from real requests) |
| **[DOCKER.md](DOCKER.md)** | Container topology, port mappings, env vars, deployment |
| **[FORbacked.md](FORbacked.md)** | Backend → UI value contract (field → component mapping) |
