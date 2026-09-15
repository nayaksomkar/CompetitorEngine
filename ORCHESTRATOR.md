# ORCHESTRATOR.md — How the Main Orchestrator & Parser Work

This document describes how the orchestrator (CompetitorEngine), the parser contract, and Docker-hosted services fit together so the UI team can format requests correctly and consume responses reliably.

The orchestrator is the **brain** of the system. It owns all routing logic — what to fetch, what to search, what to skip, what to answer. The UI is a thin presentation layer that sends intent and renders what comes back.

---

## 1. System Overview

```
┌────────────────────┐
│  UI (Browser)      │
│  - Renders cards   │
│  - Holds context   │
│  - Sends requests  │
└─────────┬──────────┘
          │  HTTP POST  /api/v1/parser/execute
          │  { parser_input: { intent, ... } }
          ▼
┌─────────────────────────────────────────────────────────┐
│              Orchestrator (Docker Container)            │
│  CompetitorEngine — FastAPI service (port 8001)         │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │  Intent      │───▶│  Planner     │───▶│ Executor  │ │
│  │  Classifier  │    │  (what steps │    │ (calls    │ │
│  │              │    │   to run)    │    │  services)│ │
│  └──────────────┘    └──────┬───────┘    └─────┬─────┘ │
│                            │                  │       │
│                            │                  │       │
│                  ┌─────────┼──────────┐       │       │
│                  ▼         ▼          ▼       ▼       │
│              [WebHunter] [LLMPing] [Cache] [Validator]│
│              (search)    (reason)  (memo)  (sanity)  │
└─────────────────────────────────────────────────────────┘
```

All three services run in **separate Docker containers**. The orchestrator is stateless; the UI keeps session context.

---

## 2. Docker Architecture (Localhost)

See `DOCKER.md` for the canonical port mapping. Quick reference:

| Container            | Host port | Container port | Role                    |
|----------------------|-----------|----------------|-------------------------|
| `competitorengine`   | **8001**  | 8001           | Orchestrator (this svc) |
| `llmping`            | **8000**  | 8000           | LLM analysis            |
| `webhunter`          | **8765**  | 8000           | Web search / harvesting |

The orchestrator **refuses to start** without `LLMPING_URL` and `WEBHUNTER_URL` set.

---

## 3. The Orchestrator Decides Everything

The UI does **not** decide what to search, what to ask, or what to skip. It sends an `intent` and a `message`. The orchestrator:

1. **Classifies** the intent (`bootstrap`, `question`, `refine`, `compare`, `explain`, `regenerate`, `follow-up`).
2. **Plans** the work — which services to call, in what order, with what queries.
3. **Executes** the plan, gathering context as needed.
4. **Validates** the output (entity-level, never hallucinated fillers).
5. **Returns** a typed response the UI can render.

If the user asks about a company the UI has never heard of, the orchestrator **searches the web via WebHunter** and **asks LLMPing to synthesize** an answer. The UI does not need to know whether a company came from session context, the bundled dataset, or a fresh web search — it just renders the response.

---

## 4. Parser-Driven Flow (Recommended)

### 4.1 Request Format

**Endpoint:** `POST /api/v1/parser/execute`

```json
{
  "parser_input": {
    "intent": "question",
    "message": "Tell me about Fragante as a competitor in this list",
    "session_id": "abc-123",
    "current_analysis": { /* last full response, may be null */ },
    "context_update": { /* last context_update, may be null */ },
    "form_input": { /* last form_input from bootstrap, may be null */ }
  }
}
```

Minimal shapes per intent:

```jsonc
// bootstrap (first run, no context)
{ "parser_input": { "intent": "bootstrap", "requested_count": 3, "form_input": { /* full form */ } } }

// question / follow-up
{ "parser_input": { "intent": "question", "message": "...", "session_id": "...", "context_update": { /* last */ } } }

// refine
{ "parser_input": { "intent": "refine", "message": "add Fragante as a competitor", "context_update": { /* last */ }, "form_input": { /* last */ } } }

// compare / explain
{ "parser_input": { "intent": "compare", "message": "compare Forest Essentials vs Fragante", "context_update": { /* last */ } } }

// regenerate
{ "parser_input": { "intent": "regenerate", "form_input": { /* same as last bootstrap */ }, "context_update": { /* last */ } } }
```

### 4.2 Field Reference

| Field              | Type     | Required         | Description                                          |
|--------------------|----------|------------------|------------------------------------------------------|
| `intent`           | string   | Yes              | One of: `bootstrap`, `question`, `refine`, `compare`, `explain`, `regenerate`, `follow-up` |
| `message`          | string   | For question/compare/explain/refine | The user's natural-language request |
| `session_id`       | string   | For follow-ups   | Stable session identifier (UI-generated UUID)        |
| `context_update`   | object   | Recommended      | Last `context_update` from a previous response       |
| `current_analysis` | object   | Optional         | Last full `data` payload (for rich follow-ups)       |
| `form_input`       | object   | For bootstrap/refine/regenerate | The bootstrap questionnaire payload |
| `requested_count`  | int      | Optional         | Max competitors (1-3, default 3)                     |

### 4.3 Intent Routing

The orchestrator owns the routing. The UI just labels what it thinks the user wants.

| Intent         | Orchestrator Behavior |
|----------------|----------------------|
| `bootstrap`    | Full analysis: research → reasoning → validation. Builds competitor set, SWOT, market gaps, charts. |
| `question`     | Classify the question. If it names a company not in the current set → **search WebHunter**, then **ask LLMPing** to synthesize. If it references existing context → answer from context. |
| `follow-up`    | Same as `question`. Alias kept for clarity in the UI. |
| `refine`       | Mutate the dataset (add/remove/change competitor). If the user names a new company, search it first; never invent data. |
| `compare`      | Generate side-by-side comparison for two or more named entities. Look each up via WebHunter if not in context. |
| `explain`      | Drill into a single data point from the previous response. |
| `regenerate`   | Re-run bootstrap with the same `form_input`. |

---

## 5. The "Ask About Any Company" Flow

This is the flow that handles queries like *"tell me about Fragante"*. The orchestrator runs it for `question`, `compare`, `refine`, or `explain` intents when the named company is not already in context.

### 5.1 Planner Steps

1. **Resolve entities from the message**
   - Extract every proper-noun company / brand mention.
   - Normalize (lowercase, strip legal suffixes: "Inc", "Ltd", "Pvt", "India", etc.) → slug.
   - Check against `context_update.entities.competitors`. Each entity is either `in_context` or `needs_lookup`.

2. **For each `needs_lookup` entity, run a WebHunter search**
   - Query templates (orchestrator chooses the best one based on the user's industry context):
     - `"{company} company profile {industry}"`
     - `"{company} pricing {industry}"`
     - `"{company} competitors market share"`
     - `"{company} funding headquarters"`
   - WebHunter returns up to N=8 sources with `{title, url, snippet, publisher, date}`.
   - Keep the top 5 by relevance; deduplicate by domain.

3. **Ask LLMPing to extract a profile**
   - Input: `{ company_name, industry, sources[] }`.
   - Output schema (strict):
     ```json
     {
       "name": "Fragante",
       "description": "1-2 sentence summary",
       "pricingTier": "Premium | Mid-range | Budget | Ultra-Premium | unknown",
       "marketPosition": "Leader | Challenger | Niche | Emerging | unknown",
       "marketShare": null,
       "growthRate": null,
       "funding": "string or null",
       "founded": "string or null",
       "hq": "string or null",
       "strengths": ["up to 3"],
       "weaknesses": ["up to 3"],
       "confidence": 0-100,
       "sourceCount": 5
     }
     ```
   - If `confidence < 40` or `sourceCount < 2`, the orchestrator marks the entity `partial` and includes it in `missing_data`.

4. **Merge into the response**
   - `in_context` entities use the cached profile from the previous analysis.
   - `needs_lookup` entities use the freshly extracted profile and are flagged `source: "web"`.
   - The full set is returned in `data.competitors[]` (existing) **or** `data.answer.competitors[]` (new, for one-off questions — see 5.2).

5. **Write back into context_update**
   - Add the new entity slug to `context_update.entities.competitors` (max 3 active).
   - If the cap is hit, the oldest `needs_lookup` entity is dropped and reported in `evicted_entities[]`.

### 5.2 Response Shape for Lookup Questions

When the intent is `question` / `compare` / `explain` and lookup was required, the response includes an `answer` block alongside (not instead of) the normal `data` block:

```jsonc
{
  "intent": "question",
  "status": "success" | "partial",
  "answer": {
    "summary": "Fragante is a mid-range Indian fragrance brand launched in 2019...",
    "competitors": [
      {
        "id": "fragante",
        "name": "Fragante",
        "source": "web",
        "lookupConfidence": 78,
        "profile": {
          "description": "...",
          "pricingTier": "Mid-range",
          "marketPosition": "Emerging",
          "strengths": ["..."],
          "weaknesses": ["..."],
          "funding": null,
          "founded": "2019",
          "hq": "Mumbai, India"
        },
        "sources": [
          { "id": "fragante-s1", "title": "Fragante launches...", "publisher": "...", "url": "...", "date": "..." }
        ]
      }
    ],
    "comparedTo": [
      // when intent === "compare", the in-context competitors included in the side-by-side
    ]
  },
  "data": { /* existing analysis, unchanged for question intents */ },
  "missing_data": [],
  "context_update": { /* updated */ },
  "evicted_entities": []
}
```

For `compare` intents, `answer.comparedTo[]` mirrors the same shape (so the UI can render a 2-column or N-column table).

For `explain` intents, `answer` carries `{ question, explanation, evidence[], sources[] }` instead of a competitor profile.

### 5.3 What the UI Sends

The UI does **not** pre-classify entities or trigger lookups. It just sends:

```jsonc
{
  "parser_input": {
    "intent": "question",
    "message": "tell me about Fragante as compared to other relevant companies in this list",
    "session_id": "<uuid>",
    "context_update": <from sessionStorage>
  }
}
```

The orchestrator reads `message`, extracts "Fragante", sees it's not in `context_update.entities.competitors`, runs the WebHunter search, asks LLMPing, and returns the merged answer.

### 5.4 Follow-Up Questions

A follow-up ("and how does it compare to Jo Malone?") is sent the same way — the orchestrator uses `context_update` to resolve pronouns ("it", "the cheaper one", "the leader") per §8.4 of the previous version. If the follow-up introduces a new company, that company is looked up the same way.

---

## 6. Response Format

### 6.1 Success Response

```json
{
  "intent": "bootstrap",
  "status": "success",
  "data": {
    "business_summary": "...",
    "profile": { /* BusinessProfile */ },
    "executive_summary": "...",
    "market_info": { "size": "$5B", "growth": "12%" },
    "positioning": "...",
    "gaps": ["..."],
    "opportunities": ["..."],
    "risks": ["..."],
    "competitors": [ /* Competitor[] */ ],
    "swot": { /* SWOT */ },
    "comparisons": [],
    "charts": [ /* ChartData[] */ ],
    "metric_cards": [ /* MetricCard[] */ ],
    "insights": [ /* InsightItem[] */ ],
    "recommendations": [ /* Recommendation[] */ ],
    "action_plan": [ /* ActionPlanItem[] */ ],
    "report": "...",
    "sources": [ /* Source[] */ ],
    "metadata": {
      "generated_at": "2026-09-10T12:00:00+00:00",
      "model_used": "llm-brain",
      "confidence": 0.85,
      "processing_time_ms": 4500
    }
  },
  "answer": null,
  "missing_data": [],
  "context_update": { /* compact */ },
  "evicted_entities": [],
  "error": null,
  "result_counts": {
    "requested": 3,
    "retrieved": 1,
    "valid": 1,
    "displayed": 1
  },
  "operations_performed": ["extract", "infer", "calculate", "normalize"],
  "entity_statuses": {
    "competitors": [
      { "id": "fragante", "name": "Fragante", "status": "complete", "missing_fields": [], "source": "web", "lookupConfidence": 78 }
    ]
  }
}
```

### 6.2 Status Values

| Status    | Meaning                                          | UI Action                              |
|-----------|--------------------------------------------------|----------------------------------------|
| `success` | All data retrieved successfully                  | Render normally                        |
| `partial` | Some data missing or failed                      | Render valid data, show warnings       |
| `error`   | Critical failure                                 | Show error state, preserve any valid data |

### 6.3 Entity Status Values

| Status     | Meaning                              | UI Action                              |
|------------|--------------------------------------|----------------------------------------|
| `complete` | All required fields present          | Render normally                        |
| `partial`  | Some optional fields missing         | Render with "—" for missing fields     |
| `failed`   | Entity could not be generated        | Skip entity, show in `missing_data`    |
| `loading`  | Lookup in flight (long WebHunter run)| Show skeleton card                     |

For web-looked-up entities (`source: "web"`), include `lookupConfidence` (0-100) in the entity status. The UI should render a subtle badge when `lookupConfidence < 60`.

---

## 7. Fault Tolerance & Error Handling

### 7.1 Retry Policy

| Operation                 | Max Retries | Delay | Fallback                  |
|---------------------------|-------------|-------|---------------------------|
| Bootstrap (full analysis) | 2           | 1s    | Return partial data       |
| Chat question             | 1           | 500ms | Try WebHunter, then partial|
| WebHunter search          | 2           | 1s    | Mark entity `failed`      |
| LLMPing extraction        | 2           | 500ms | Use raw sources only      |
| Single entity generation  | 1           | 500ms | Skip entity               |

### 7.2 Failure Isolation

```
Forest Essentials ✓ → Render
Kama Ayurveda    ✓ → Render
Fragante         ✗ (WebHunter timeout) → Skip, report in missing_data
Jo Malone        ✓ → Render
```

The orchestrator **never fabricates** entities to fill the requested count.

### 7.3 Partial Results

If lookup for Fragante times out twice, the orchestrator returns:

```json
{
  "status": "partial",
  "data": { "competitors": [/* the ones that worked */] },
  "answer": null,
  "missing_data": [
    { "field": "competitors[fragante]", "reason": "WebHunter timeout after 2 attempts", "severity": "warning" }
  ]
}
```

The UI renders valid competitors and shows the warning.

---

## 8. Context Management

### 8.1 Context Update (What UI Should Store)

After each response, the UI should store `context_update` in `sessionStorage`:

```typescript
const CONTEXT_KEY = 'competitor_analysis_context';
sessionStorage.setItem(CONTEXT_KEY, JSON.stringify(response.context_update));
```

### 8.2 Context Structure

```json
{
  "version": 1,
  "business": {
    "name": "Scentra",
    "industry": "Fragrance",
    "pricing": "₹3,500",
    "model": "DTC"
  },
  "entities": {
    "competitors": ["forest-essentials", "kama-ayurveda", "jo-malone-india", "fragante"],
    "focus": null
  },
  "result_meta": {
    "requested_count": 3,
    "retrieved_count": 4,
    "filters": []
  },
  "constraints": {
    "included": ["Indian premium"],
    "excluded": []
  },
  "keywords": ["Fragrance", "Premium", "DTC"]
}
```

Note: `fragante` may appear here even though it was looked up via WebHunter. The next time the UI references "Fragante" or any pronoun pointing to it, the orchestrator can use the cached profile instead of searching again.

### 8.3 Reference Resolution

| User Says                | Orchestrator Resolves To                            |
|--------------------------|-----------------------------------------------------|
| "it" / "that one"        | `context.entities.focus`                            |
| "the cheaper one"        | Lowest `priceMonthly` in current entity set         |
| "the leader"             | Competitor with `marketPosition: "Leader"`          |
| "compare them"           | Last two entities mentioned in the session          |
| "add X"                  | Look up X via WebHunter, add to `entities.competitors` (max 3 active + evicted) |
| "what about Fragante?"   | If in context → use cached profile. If not → §5 lookup flow. |

### 8.4 Eviction Policy

The active set is capped at **3 competitors** for dynamic data (PARSER.md §2.2). When a 4th is added via `refine`:

1. The new entity is added.
2. The oldest `in_context` entity (lowest `last_referenced_at`) is moved to `evicted_entities[]`.
3. The evicted entity's profile is still cached under its slug for 24 hours, so a follow-up "what about Forest Essentials?" can still answer from cache without re-fetching.

The eviction is reported back in `evicted_entities[]` so the UI can show a "Forest Essentials moved to history" toast.

---

## 9. UI Integration Guide

### 9.1 Sending a Bootstrap Request

```javascript
const response = await fetch('http://localhost:8001/api/v1/parser/execute', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    parser_input: {
      intent: 'bootstrap',
      form_input: { /* full form */ },
      requested_count: 3
    }
  })
});
```

### 9.2 Sending a Free-Form Question

This is the new flow. Same endpoint, same shape, different intent:

```javascript
const context = JSON.parse(sessionStorage.getItem('competitor_analysis_context'));

const response = await fetch('http://localhost:8001/api/v1/parser/execute', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    parser_input: {
      intent: 'question',
      message: 'tell me about Fragante as compared to other relevant companies in this list',
      session_id: context.session_id,
      context_update: context
    }
  })
});

const data = await response.json();

if (data.answer) {
  // Render data.answer.competitors[] as a card or table.
  // If intent was "compare", data.answer.comparedTo[] is the second column.
  // Show data.answer.sources[] at the bottom.
}

if (data.status === 'partial') {
  // Show warnings from data.missing_data
}
```

The UI does not need to know whether `answer.competitors[0]` came from session context or a fresh WebHunter search — it just renders `source: "web" | "context"` as a small badge.

### 9.3 Handling Partial Results

```javascript
const { status, data, answer, missing_data, entity_statuses } = response;

// Bootstrap-style render: full competitor set
if (data?.competitors) {
  data.competitors.forEach(comp => {
    const ent = entity_statuses.competitors.find(s => s.id === slugify(comp.name));
    if (ent?.status !== 'failed') renderCompetitorCard(comp, ent);
  });
}

// Question-style render: looked-up answer
if (answer?.competitors) {
  answer.competitors.forEach(comp => renderLookupCard(comp));
}

if (missing_data.length > 0) {
  showWarnings(missing_data.filter(m => m.severity === 'warning'));
}
```

### 9.4 Updating Context After Each Response

```javascript
function updateContext(response) {
  if (response.context_update) {
    sessionStorage.setItem(
      'competitor_analysis_context',
      JSON.stringify(response.context_update)
    );
  }
}
```

---

## 10. Field Mapping (Orchestrator → UI)

| Orchestrator Field                                | UI Component            |
|---------------------------------------------------|-------------------------|
| `data.competitors[]`                              | Competitor cards        |
| `data.swot`                                       | SWOT grid               |
| `data.charts[]`                                   | Chart visualizations    |
| `data.metric_cards[]`                             | KPI cards               |
| `data.recommendations[]`                          | Recommendation list     |
| `data.action_plan[]`                              | Action plan timeline    |
| `data.insights[]`                                 | Insight cards           |
| `data.report`                                     | Full report viewer      |
| `data.sources[]`                                  | Sources list            |
| `answer.competitors[]` *(new)*                    | Lookup cards (one-off)  |
| `answer.comparedTo[]` *(new)*                     | Comparison table cells  |
| `answer.summary` *(new)*                          | Lookup card lead text   |
| `answer.sources[]` *(new)*                        | Per-lookup source list  |
| `entity_statuses.competitors[].lookupConfidence`  | "Web data" confidence badge |
| `entity_statuses.competitors[].source`            | "from session" / "from web" badge |
| `evicted_entities[]`                              | Eviction toast          |
| `result_counts`                                   | Result count indicators |
| `missing_data[]`                                  | Warning notifications   |
| `context_update`                                  | SessionStorage data     |

---

## 11. Error Handling

### 11.1 HTTP Status Codes

| Code | Meaning                                       | UI Action                                       |
|------|-----------------------------------------------|-------------------------------------------------|
| 200  | Success or partial success                    | Parse `status` field                            |
| 422  | Validation error (malformed request)          | Show "Invalid request format"                   |
| 500  | Internal server error                         | Show "Service unavailable, try again"           |
| 502  | Upstream service (WebHunter/LLMPing) failed   | Show "Research service down, partial data available" |
| 504  | Lookup timeout (WebHunter > 10s × 2 retries)  | Show "Couldn't look up {entity}, skipping"      |

### 11.2 Structured Error Response

```json
{
  "intent": "question",
  "status": "error",
  "data": null,
  "answer": null,
  "error": "WebHunter unavailable: connection refused",
  "missing_data": [
    { "field": "competitors[fragante]", "reason": "WebHunter connection refused", "severity": "critical" }
  ]
}
```

---

## 12. Performance & Cost Optimization

- **Cache aggressively**: `context_update` carries compact profiles; a follow-up about a cached entity costs zero WebHunter calls.
- **Lookup budget**: cap each request at 3 WebHunter searches (so one user message naming 3 new companies stays under the cap).
- **Token budget**: pass only the top 5 sources per entity to LLMPing, not all 8.
- **Parallelism**: when a message names multiple new entities, run their WebHunter searches in parallel.
- **Eviction**: 24-hour TTL on evicted entities to bound memory.

---

## 13. Testing the Integration

### 13.1 Health Check

```bash
curl http://localhost:8001/health
```

Response:
```json
{ "status": "ok", "service": "orchestrator", "version": "2.1.0" }
```

### 13.2 Quick Test Request

```bash
curl -X POST http://localhost:8001/api/v1/parser/execute \
  -H "Content-Type: application/json" \
  -d '{
    "parser_input": {
      "intent": "bootstrap",
      "requested_count": 3,
      "form_input": {
        "business_name": "TestCo",
        "idea": "AI analytics",
        "industry": "SaaS"
      }
    }
  }'
```

### 13.3 Lookup Question Test

```bash
curl -X POST http://localhost:8001/api/v1/parser/execute \
  -H "Content-Type: application/json" \
  -d '{
    "parser_input": {
      "intent": "question",
      "message": "tell me about Fragante",
      "session_id": "test-123",
      "context_update": {
        "version": 1,
        "business": { "name": "TestCo", "industry": "Fragrance" },
        "entities": { "competitors": [], "focus": null },
        "result_meta": { "requested_count": 3, "retrieved_count": 0, "filters": [] },
        "constraints": { "included": [], "excluded": [] },
        "keywords": ["Fragrance"]
      }
    }
  }'
```

Expected: a 200 with `answer.competitors[0].name === "Fragante"`, `source: "web"`, and at least 2 sources in `answer.sources`.

### 13.4 Compare Test

```bash
curl -X POST http://localhost:8001/api/v1/parser/execute \
  -H "Content-Type: application/json" \
  -d '{
    "parser_input": {
      "intent": "compare",
      "message": "compare Fragante and Jo Malone India",
      "session_id": "test-123",
      "context_update": { /* same */ }
    }
  }'
```

Expected: `answer.competitors[0]` is Fragante (web), `answer.comparedTo[0]` is Jo Malone India (context), with a 2-column render in the UI.

---

## 14. Migration Notes

### 14.1 From `/api/v1/analyze` to `/api/v1/parser/execute`

```jsonc
// Old
{ "business_name": "...", "idea": "...", "industry": "...", "competitors": [...] }

// New
{ "parser_input": { "intent": "bootstrap", "form_input": { /* same fields */ }, "requested_count": 3 } }
```

### 14.2 From `/api/v1/chat` to `/api/v1/parser/execute`

```jsonc
// Old
{ "session_id": "abc", "message": "Tell me about CompetitorA" }

// New
{ "parser_input": { "intent": "question", "session_id": "abc", "message": "Tell me about CompetitorA", "context_update": { /* last */ } } }
```

### 14.3 New `answer` Block

The new `answer` field is added for any intent that produces a one-off lookup result. Bootstrap responses set `answer: null`. Question/compare/explain responses populate it.

### 14.4 New `evicted_entities` Block

Whenever a 4th entity is added to the active set, the oldest is moved to `evicted_entities[]`. The UI should show a one-line toast and offer an "undo" that re-sends the same `refine` request.

---

## 15. Summary

**Key Points for the UI:**

1. Send **one** request shape: `{ parser_input: { intent, ... } }` to `/api/v1/parser/execute`.
2. The orchestrator decides what to search, what to ask, what to cache — never the UI.
3. For "tell me about X" queries, just send `intent: "question"` and the message. The orchestrator runs WebHunter if X is unknown.
4. Always send `context_update` for follow-ups — it cuts lookup cost.
5. Render `data.*` for bootstrap/refine/regenerate, `answer.*` for question/compare/explain.
6. Show `lookupConfidence` as a badge for web-sourced entities.
7. Handle `missing_data` warnings and `evicted_entities` toasts.
8. Never retry failed requests — orchestrator handles retries.

**The orchestrator guarantees:**

- Stateless request handling with cached context per session.
- WebHunter-backed lookup for any named company, even ones never seen before.
- LLMPing-synthesized profiles grounded in real sources.
- Fault tolerance: per-entity retries, partial results, no hallucinated fillers.
- Compact `context_update` so follow-ups stay cheap.
- Maximum 3 active competitors with 24h evicted cache.
