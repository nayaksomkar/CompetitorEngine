# FORbacked.md — Backend → UI Value Contract

> Purpose: document **how backend-provided values are used and displayed in the UI**, so a backend developer knows exactly what the frontend expects and how each value ultimately appears to the user.
>
> Sources used (and only these): `README.md`, `ORCHESTRATOR.md`, `ORCHESTRATOR_PROMPT.md`, `PARSER.md`, `DOCKER.md`.
> (The `.kilo/worktrees/soft-hornet/` copies of these files are byte-identical; `.aider.chat.history.md` is a tool session log with no project documentation; `.commandcode/taste/` files are tool configuration, not project docs.)

---

## 0. Source Authority Order

| Document | Role | Authority |
|---|---|---|
| `PARSER.md` | UI data inventory — per-field display rules, validation, defaults | **Authoritative for UI rendering/formatting** |
| `ORCHESTRATOR.md` | Current API contract (`/api/v1/parser/execute`), response envelope, field→UI mapping, error handling | **Authoritative for API contract** |
| `ORCHESTRATOR_PROMPT.md` | Backend build spec for the orchestrator (same contract as `ORCHESTRATOR.md`, plus WebHunter/LLMPing internals) | Authoritative (consistent with `ORCHESTRATOR.md`) |
| `DOCKER.md` | Ops/deployment, WebHunter API reference, env vars | Supporting |
| `README.md` | App overview + **legacy** backend wiring (`/api/v1/analyze`, old chart schema) | Legacy — predates the orchestrator (see Conflict C1) |

`ORCHESTRATOR.md` references `PARSER.md` directly (§8.4 cites "PARSER.md §2.2") and documents the migration *from* the README's old endpoint (§14), which establishes `ORCHESTRATOR.md` + `PARSER.md` as the current contract and `README.md` as the legacy one.

---

## 1. Documented Conflicts & Resolutions

### C1 — API endpoint: `/api/v1/analyze` vs `/api/v1/parser/execute`
- **README.md**: `POST /api/v1/analyze` with `{business_name, idea, industry, competitors, research_goals}`.
- **ORCHESTRATOR.md / ORCHESTRATOR_PROMPT.md / PARSER.md**: `POST /api/v1/parser/execute` with `{parser_input: {intent, ...}}`.
- **Resolution:** `ORCHESTRATOR.md` §14.1 explicitly documents the migration "From `/api/v1/analyze` to `/api/v1/parser/execute`". **`/api/v1/parser/execute` is the current authoritative endpoint.** The README documents the legacy integration path only.

### C2 — Chart schema: `datasets` vs `series`
- **README.md**: `{ chart_type, labels: [...], datasets: [{ name, color, values }] }`.
- **PARSER.md §2.9**: `{ title, kind, xLabel, yLabel, series: [{ id, name, color, points: [{ label, value, color? }] }] }`.
- **Resolution:** `PARSER.md` is the dedicated UI data inventory and is more detailed; its `kind` enum (`bar|line|area|radar|pie`) matches the README's chart-type list. **PARSER.md's `ChartData` is authoritative.** Conceptual mapping of the legacy shape: `chart_type→kind`, `labels→series[].points[].label`, `values→series[].points[].value`, `datasets→series`.

### C3 — Report shape: `report` (string/object) vs `reports` (array) — **partially Undetermined**
- **README.md**: `"report": { ... }` (object).
- **ORCHESTRATOR.md §6.1**: `"report": "..."` (string); §10 maps `data.report` → "Full report viewer".
- **PARSER.md §2.8/§2.13**: `"reports": [Report]` (array of `{id, title, summary, type, date, pages, sections[]}`) rendered in `ReportCard`.
- **Resolution:** `PARSER.md` provides the only complete, field-level Report UI contract, so `reports[]` (array of Report objects) is the documented UI shape. **The reconciliation between `ORCHESTRATOR.md`'s `report` string and `PARSER.md`'s `reports[]` array is Undetermined** — no document describes a transformation from a report string into Report objects.

### C4 — `pricingTier` enum: "Mid-range" vs "Mid" — **Undetermined (cosmetic)**
- **ORCHESTRATOR.md §5.1 / ORCHESTRATOR_PROMPT.md (LLMPing schema)**: `Premium | Mid-range | Budget | Ultra-Premium | unknown`.
- **PARSER.md §9 (normalization)**: `Budget, Mid, Premium, Ultra-Premium`.
- **Resolution:** Undetermined. The UI displays the raw string in a gray badge (PARSER.md §2.2), so both values render identically; the canonical enum value sent by the backend is not settled by the docs.

### C5 — Required request fields
- **PARSER.md §2.1** marks `targetCustomers` as Required = Yes.
- **README.md** says "Only `business_name`, `idea`, and `industry` are required"; the executable test requests in `ORCHESTRATOR.md` §13.2 and `ORCHESTRATOR_PROMPT.md` §2 send only `business_name`, `idea`, `industry` and succeed.
- **Resolution:** The executable examples are authoritative for the minimum: **`business_name`, `idea`, `industry` are required; everything else is optional.** `PARSER.md`'s "Required" column for `targetCustomers` appears overstated (it is "used for analysis context").

### C6 — Health response shape
- **README.md**: `/health` → `{ status: "ok" }` (minimal).
- **ORCHESTRATOR.md §13.1**: `{ "status": "ok", "service": "orchestrator", "version": "2.1.0" }` (fuller).
- **Resolution:** Not a contradiction — README shows the minimal shape; `ORCHESTRATOR.md` documents the full orchestrator health payload.

### C7 — `charts` container shape — **Undetermined**
- **PARSER.md §2.13**: `charts` is an **object** with 8 named slots (`marketShare`, `marketSharePie`, `growth`, `growthPie`, `pricing`, `pricingPie`, `featureAdoption`, `featureAdoptionPie`), each a `ChartData`.
- **ORCHESTRATOR.md §6.1** and **README.md**: `charts` is an **array** `[...]` of chart objects.
- **Resolution:** Undetermined. `PARSER.md`'s named slots are the only documented mapping of specific charts to UI locations (§10 derivation table); whether the wire format is a keyed object or ordered array is not settled.

### C8 — `data` payload for `question` intents
- **ORCHESTRATOR.md §5.2**: `"data": { /* existing analysis, unchanged for question intents */ }`.
- **ORCHESTRATOR_PROMPT.md** example response: `"data": null`.
- **Resolution:** The UI must handle both — `ORCHESTRATOR.md` §9.2/§9.3 sample code checks `data?.competitors` **and** `answer?.competitors` independently and renders whichever is present.

---

## 2. End-to-End Data Flow (generic pattern)

```text
Backend field
    ↓
API response value (JSON, snake_case on the wire)
    ↓
Frontend interpretation/transformation (HttpApi snake_case → camelCase per README; enum→badge color; suffix/format rules per PARSER.md)
    ↓
UI component (card, table, chart, badge, modal, toast)
    ↓
Value visible to the user
```

Concrete instantiation (competitor name):

```text
competitors[].name  (API: "Fragante")
    ↓
response data.competitors[0].name = "Fragante"
    ↓
HttpApi converts to camelCase (already camelCase here); CompetitorCard reads .name
    ↓
CompetitorCard header (+ 2-char logo colored by logoColor)
    ↓
User sees "Fragante" in the card header
```

---

## 3. Transport & Endpoints

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/health` | Health check → `{ status: "ok", service: "orchestrator", version: "2.1.0" }` | ORCHESTRATOR.md §13.1 |
| `POST` | `/api/v1/parser/execute` | Main analysis/intent endpoint | ORCHESTRATOR.md §4.1 |
| `POST` | `/api/v1/analyze` | **Legacy** analysis endpoint (README) | README.md |

- Base URL for the UI's backend client: `VITE_API_URL` env var (README §5).
- **Documented transformation:** "The `HttpApi` class handles all the translation — your backend sends snake_case, the UI uses camelCase automatically." (README §5). PARSER.md's `AnalysisData` uses camelCase (`businessName`, `marketShare`); ORCHESTRATOR.md's response examples use snake_case (`business_summary`, `market_info`) — the two naming conventions are bridged by this documented conversion.
- UI is "a thin presentation layer that sends intent and renders what comes back" (ORCHESTRATOR.md §1).

---

## 4. Request Fields (UI → Backend)

Sent as `{ parser_input: {...} }` to `/api/v1/parser/execute` (ORCHESTRATOR.md §4.1–4.2):

| Field | Type | Required | Purpose / Notes |
|---|---|---|---|
| `intent` | string | Yes | `bootstrap`, `question`, `refine`, `compare`, `explain`, `regenerate`, `follow-up` |
| `message` | string | For question/compare/explain/refine | User's natural-language request |
| `session_id` | string | For follow-ups | Stable session identifier (**UI-generated UUID** — static frontend behavior) |
| `context_update` | object | Recommended | Last `context_update` from a previous response (from `sessionStorage`) |
| `current_analysis` | object | Optional | Last full `data` payload (for rich follow-ups) |
| `form_input` | object | For bootstrap/refine/regenerate | Bootstrap questionnaire payload |
| `requested_count` | int | Optional | Max competitors (1–3, **default 3**) |

Legacy request (README, `/api/v1/analyze`): `{ business_name, idea, industry, competitors[], research_goals[] }` — snake_case; only the first three required.

---

## 5. Response Envelope

```json
{
  "intent": "bootstrap",
  "status": "success | partial | error",
  "data": { /* AnalysisData — see §6 */ },
  "answer": null,
  "missing_data": [ { "field": "string", "reason": "string", "severity": "critical|warning|info" } ],
  "context_update": { /* compact session context */ },
  "evicted_entities": [],
  "error": null,
  "result_counts": { "requested": 3, "retrieved": 1, "valid": 1, "displayed": 1 },
  "operations_performed": ["extract", "infer", "calculate", "normalize"],
  "entity_statuses": { "competitors": [ { "id", "name", "status", "missing_fields", "source", "lookupConfidence" } ] }
}
```

PARSER.md §6 additionally defines (not present in ORCHESTRATOR.md's envelope): `derived_data` (`{ charts_generated[], calculations_performed[] }`) and `ui_state` (`{ active_tab, highlighted_entities[], result_counts }`).

### 5.1 `status` values (ORCHESTRATOR.md §6.2)

| Status | Meaning | UI Action |
|---|---|---|
| `success` | All data retrieved successfully | Render normally |
| `partial` | Some data missing or failed | Render valid data, show warnings |
| `error` | Critical failure | Show error state, preserve any valid data |

### 5.2 Entity `status` values (ORCHESTRATOR.md §6.3, PARSER.md §4.4)

| Status | Meaning | UI Action |
|---|---|---|
| `complete` | All required fields present | Render normally |
| `partial` | Some optional fields missing | Render with "—" for missing fields |
| `failed` | Entity could not be generated | Skip entity, show in `missing_data` |
| `loading` | Lookup in flight (long WebHunter run) | Show skeleton card |

For web-looked-up entities (`source: "web"`), `lookupConfidence` (0–100) is included; **the UI renders a subtle badge when `lookupConfidence < 60`** (ORCHESTRATOR.md §6.3).

### 5.3 HTTP status codes → UI text (ORCHESTRATOR.md §11.1)

| Code | UI displays (static text unless noted) |
|---|---|
| 200 | Parse `status` field |
| 422 | "Invalid request format" |
| 500 | "Service unavailable, try again" |
| 502 | "Research service down, partial data available" |
| 504 | "Couldn't look up {entity}, skipping" (**dynamic**: `{entity}` interpolated) |

### 5.4 Envelope field → UI mapping (ORCHESTRATOR.md §10)

| Backend field | UI component |
|---|---|
| `data.competitors[]` | Competitor cards |
| `data.swot` | SWOT grid |
| `data.charts[]` | Chart visualizations |
| `data.metric_cards[]` | KPI cards |
| `data.recommendations[]` | Recommendation list |
| `data.action_plan[]` | Action plan timeline |
| `data.insights[]` | Insight cards |
| `data.report` | Full report viewer |
| `data.sources[]` | Sources list |
| `answer.competitors[]` | Lookup cards (one-off) |
| `answer.comparedTo[]` | Comparison table cells |
| `answer.summary` | Lookup card lead text |
| `answer.sources[]` | Per-lookup source list |
| `entity_statuses.competitors[].lookupConfidence` | "Web data" confidence badge |
| `entity_statuses.competitors[].source` | "from session" / "from web" badge |
| `evicted_entities[]` | Eviction toast |
| `result_counts` | Result count indicators |
| `missing_data[]` | Warning notifications |
| `context_update` | SessionStorage data |

---

## 6. Domain Objects — Field-by-Field

### 6.1 BusinessProfile (questionnaire input) — PARSER.md §2.1

```text
form_input.{business_name, idea, industry, geography, ...}
    ↓
response data.profile / data.businessName ...
    ↓
No transformation documented (displayed as-is)
    ↓
Sidebar, Chat header, Overview title/badges
    ↓
User sees the raw strings
```

| Field | Type | Required | UI display location | Missing behavior |
|---|---|---|---|---|
| `businessName` | string | Yes | Sidebar, Chat header, Overview title, Action plan title | Validation error: "Business name required" (PARSER.md §4.1) |
| `idea` | string | Yes | Overview subtitle | Not documented |
| `industry` | string | Yes | Overview badge | Not documented |
| `geography` | string | No | Overview badge | Not documented |
| `productsServices` | string[] | No | Form only (used to derive products) | — |
| `targetCustomers` | string | Yes (PARSER.md) / optional in practice (C5) | Form only (analysis context) | — |
| `pricing` | string | No | Form only (used to derive pricing tiers) | — |
| `businessModel` | string | No | Form only (analysis context) | — |
| `competitors` | string[] | No | Form only (used to derive competitor profiles) | — |
| `differentiators` | string | No | Form only (used for SWOT/insights) | — |
| `researchGoals` | string[] | No | Form only (used for action plan/reports) | — |

All values are **backend-supplied** (echoed from the form / generated by the parser). Display: direct, no formatting documented.

### 6.2 Competitor — PARSER.md §2.2 (CompetitorCard + ComparisonTable)

```text
competitors[] fields
    ↓
API response data.competitors[] (or answer.competitors[] for lookups)
    ↓
Badge color by enum; "%" suffixes; "—" for missing; max-3 truncation for lists
    ↓
CompetitorCard / ComparisonTable
    ↓
Formatted values visible to user
```

| Field | Type | UI display | Transformation / formatting | Missing/invalid behavior |
|---|---|---|---|---|
| `id` | string (slug) | (identifier; used in charts/context) | Backend generates from name: `name.toLowerCase().replace(/\s+/g, '-')` (PARSER.md §4.1) | Must be unique; validation failure → regenerate |
| `name` | string | Card header; ComparisonTable "Vendor" column | 2-char logo shown alongside (logo derived from name — **exact 2-char derivation not documented**); colored by `logoColor` | Required ("No placeholder/empty strings in required fields", §11 checklist) |
| `logoColor` | string (hex) | Card header 2-char logo background | Direct use as color | Not documented |
| `description` | string | Card body | Direct | Not documented |
| `funding` | string | Stat | Direct | Missing → "—" |
| `founded` | string | Stat | Direct | Missing → "—" |
| `hq` | string | Stat; ComparisonTable "HQ" column | Direct | "—" (partial-entity rule) |
| `marketShare` | number (0–100) | Stat; ComparisonTable "Share" column | "%" suffix appended; clamped to 0–100; redistributed if sum ≠ 100 across competitors (§4.1) | Clamped/ redistributed; validation: sum must equal 100 (±1 rounding tolerance, §11) |
| `growthRate` | number (percentage, can be negative) | Stat; ComparisonTable "Growth" column | "%" suffix; **green text if > 30** | Default: 0 (§4.1) |
| `pricingTier` | string (e.g. Premium, Ultra-Premium) | Gray badge; ComparisonTable "Pricing tier" | Direct string (enum conflict C4) | Not documented |
| `marketPosition` | enum Leader/Challenger/Niche/Emerging | Badge; ComparisonTable "Position" | Badge color: Leader=red, Challenger=violet, Niche=green, Emerging=amber | Default: "Emerging" (§4.1); ORCHESTRATOR.md also allows "unknown" |
| `strengths` | string[] | List with check icons | **Max 3 displayed** | Not documented |
| `weaknesses` | string[] | List with alert icons; ComparisonTable "Biggest weakness" = `weaknesses[0]` | **Max 3 displayed** in card | Not documented |
| `swot` | object {strengths, weaknesses, opportunities, threats} | Expandable 4-panel grid | 4 panels: Strengths (emerald), Weaknesses (rose), Opportunities (sky), Threats (amber) | Not documented |
| `explanation` | object (see §6.11) | "Explain this" modal | See §6.11 | Optional |
| `status` | enum complete/partial/loading/failed | Rendering mode | See §5.2 | — |

Lookup-response competitor (`answer.competitors[]`, ORCHESTRATOR.md §5.2) adds: `source` ("web"|"context" → small badge, rendered as "from web"/"from session" per §10), `lookupConfidence` (0–100 → subtle badge when < 60), `profile` {description, pricingTier, marketPosition, strengths, weaknesses, funding, founded, hq}, and per-entity `sources[]`.

Example (ORCHESTRATOR.md §5.2):
```json
{ "id": "fragante", "name": "Fragante", "source": "web", "lookupConfidence": 78,
  "profile": { "description": "...", "pricingTier": "Mid-range", "marketPosition": "Emerging",
               "strengths": ["..."], "weaknesses": ["..."], "funding": null, "founded": "2019", "hq": "Mumbai, India" },
  "sources": [ { "id": "fragante-s1", "title": "Fragante launches...", "publisher": "...", "url": "...", "date": "..." } ] }
```

### 6.3 Product / ProductFeature — PARSER.md §2.3 (ProductBreakdown)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `id` | string | — | — | Unique within type |
| `competitorId` | string | — | Links to competitor; "must match an existing competitor `id`" (§11.4) | — |
| `name` | string | Header | Direct | — |
| `tagline` | string | Subtitle | Direct | — |
| `category` | string | Blue badge | Direct | — |
| `pricingModel` | string | Gray badge | Direct | — |
| `startingPrice` | number | "From ₹{price}" | Static prefix "From ₹" + number | Not documented |
| `features[].name` | string | Filterable/sortable list | Direct | — |
| `features[].description` | string | List detail | Direct | — |
| `features[].maturity` | enum Beta/GA/Deprecated/Roadmap | Badge | GA=green, Beta=amber, Roadmap=blue, Deprecated=gray | Default: "GA" (§4.1) |
| `features[].adoption` | number (0–100) | Progress bar with "%" | Percent + progress bar; clamped | Clamped to 0–100 |
| `status` | enum | Rendering mode | See §5.2 | — |

Dynamic limits (PARSER.md §3.1): 2–3 products for dynamic data (static sample: 4).

### 6.4 PricingTier — PARSER.md §2.4 (PricingTable)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `name` | string | Card title | Direct | — |
| `highlighted` | boolean | "Most popular" badge | Boolean → static badge text | Absent → no badge |
| `pricingModel` | string | Gray badge | Direct | — |
| `bestFor` | string | Text | Direct | — |
| `priceMonthly` | number or "Custom" | "₹{price}" or "Custom" | Static "₹" prefix + number; string "Custom" displayed as-is | Default: "Custom" (§4.1) |
| `billing` | enum monthly/yearly | "/mo" or "/mo · billed yearly" | Enum → static suffix string | Not documented |
| `features` | string[] | List with check icons | Direct | — |

Note: `priceMonthly` also drives backend pronoun resolution — "the cheaper one" resolves to the lowest `priceMonthly` in the current entity set (ORCHESTRATOR.md §8.3, PARSER.md §8).

Dynamic limits: 6–9 pricing tiers for dynamic data (static sample: 10).

### 6.5 MarketGap — PARSER.md §2.5 (MarketGapCard)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `title` | string | Card title | Direct | — |
| `description` | string | Card body | Direct | — |
| `estimatedRevenue` | string | Gray badge | Direct (e.g. preformatted "$5B"-style string) | — |
| `opportunityScore` | number (0–100) | Progress bar "/100" (emerald) | Score + "/100" suffix + bar; clamped | Clamped to 0–100 |
| `difficultyScore` | number (0–100) | Progress bar "/100" (rose) | Same, rose color | Clamped |
| `affectedSegments` | string[] | Blue badges | Direct | — |

Dynamic limits: 2–3 market gaps (static sample: 4).

### 6.6 InsightItem — PARSER.md §2.6 (InsightCard)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `title` | string | Card title | Direct | — |
| `summary` | string | Card body | Direct | — |
| `detail` | string | Expanded content | Direct | — |
| `category` | enum Opportunity/Risk/Trend/Recommendation/Insight | Badge | Opportunity=green, Risk=red, Trend=blue, Recommendation=violet (**"Insight" badge color not documented**) | Default: "Insight" (§4.1) |
| `impact` | enum High/Medium/Low | "Impact: {impact}" badge | Static "Impact: " prefix; High=red, Medium=amber, Low=gray | Default: "Medium" (§4.1) |
| `confidence` | number (0–100) | "{confidence}% confidence" | Number + "% confidence" suffix; clamped | Clamped to 0–100 |
| `relatedCompetitors` | string[] | (not documented beyond schema) | — | — |
| `explanation` | string (optional) | "Explain this" modal | Direct | Optional — absent → no explanation content |

Dynamic limits: 3–4 insights, 2 recommendations (static: 4 and 2). Note: `recommendations` reuse the `InsightItem` shape (PARSER.md §2.13).

### 6.7 ActionPlanItem — PARSER.md §2.7 (ActionPlanList)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `title` | string | Item title | Direct | — |
| `description` | string | Item body | Direct | — |
| `rationale` | string | "Why: {rationale}" | Static "Why: " prefix | — |
| `owner` | string | "Owner: {owner}" | Static "Owner: " prefix | — |
| `priority` | enum P0/P1/P2 | Group header | P0 → "Ship now", P1 → "Next", P2 → "Later" (static mapping) | Default: "P1" (§4.1) |
| `horizon` | enum Now/Next/Later | "Horizon: {horizon}" | Static "Horizon: " prefix | Default: "Next" (§4.1) |
| `effort` | enum Low/Medium/High | "Effort: {effort}" | Static "Effort: " prefix | Default: "Medium" (§4.1) |
| `impact` | enum Low/Medium/High | "Impact: {impact}" | Static "Impact: " prefix | Default: "Medium" (§4.1) |

Dynamic limits: 4–6 action plan items (static sample: 6).

### 6.8 Report / ReportSection — PARSER.md §2.8 (ReportCard)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `title` | string | Card title | Direct | — |
| `summary` | string | Card body | Direct | — |
| `type` | enum Executive Summary/Deep Dive/Market Landscape/Go-to-Market | Blue badge | Direct | Default: "Executive Summary" (§4.1) |
| `pages` | number | "{pages} pages" gray badge | Number + " pages" suffix | — |
| `date` | string | Text | Direct | — |
| `sections[].heading` | string | Expandable list heading | Rendered **uppercase** | — |
| `sections[].body` | string | Expandable list body | Direct | — |

Dynamic limits: 2–3 reports (static sample: 3). See Conflict C3 for the `report` vs `reports` shape discrepancy.

### 6.9 ChartData — PARSER.md §2.9 (Chart) — authoritative schema (see C2)

```json
{ "title": "string", "kind": "bar|line|area|radar|pie", "xLabel": "string", "yLabel": "string",
  "series": [ { "id": "string", "name": "string", "color": "string (hex)",
                "points": [ { "label": "string", "value": "number", "color": "string (optional)" } ] } ] }
```

| Field | UI display | Transformation | Missing behavior |
|---|---|---|---|
| `title` | Chart title | Direct | — |
| `kind` | Determines chart type rendered (custom SVG: bar, line, area, radar, pie — no charting library, README Design Notes) | Enum → chart renderer | Default: "bar" (§4.1) |
| `xLabel` / `yLabel` | Axis labels | Direct | — |
| `series[].name` | Legend entry | Direct | — |
| `series[].color` | Series color | Hex string used directly | — |
| `series[].points[].label` | Hover tooltip label / category | Direct | — |
| `series[].points[].value` | Hover tooltip value / bar height / point position | Direct numeric use | — |
| `series[].points[].color` | Optional per-point color override | Direct, optional | Absent → series color |

**Chart derivation (PARSER.md §10)** — charts are backend-derived from other data:
- `marketShare` bar chart + `marketSharePie` ← `competitors[].marketShare`
- `growth` bar chart + `growthPie` ← `competitors[].growthRate`
- `pricing` bar chart + `pricingPie` ← `pricingTiers[].priceMonthly`
- `featureAdoption` bar chart + `featureAdoptionPie` ← `products[].features[].adoption`

Consistency rules (§11.4): chart series must reference real competitor IDs; pie must match bar data; colors consistent per entity across charts. Retry fallback for failed chart generation: **show "No data available"** (§4.2).

Legacy README chart example (for reference, old schema): `{ "chart_type": "bar", "labels": ["Le Labo", "Byredo", "Replica"], "datasets": [{ "name": "Market Share", "color": "#10a37f", "values": [8, 6, 11] }] }`.

### 6.10 Source — PARSER.md §2.10 (SourcesList)

| Field | Type | UI display | Transformation | Missing behavior |
|---|---|---|---|---|
| `title` | string | Source title | Direct | — |
| `publisher` | string (optional) | "{publisher} · {date}" | Combined with date using " · " separator | Optional — absent → separator/segment omitted (exact omission rule not documented) |
| `date` | string (optional) | "{publisher} · {date}" | Combined as above | Optional |
| `snippet` | string (optional) | Body text | Direct | Optional |
| `url` | string (optional) | External link icon | Presence → icon rendered | Optional — absent → no link icon |

WebHunter search results (internal to backend, ORCHESTRATOR.md §5.1 / ORCHESTRATOR_PROMPT.md) have the same shape: `{title, url, snippet, publisher, date}` — up to 8 returned, top 5 kept by relevance, deduplicated by domain. These flow into `sources[]` and per-entity `sources[]`.

Dynamic limits: 4–6 sources (static sample: 8). Retry policy: 0 retries — on failure, **omit sources** (§4.2).

### 6.11 Explanation — PARSER.md §2.11 ("Explain this" modal)

README: "Every item includes an `explanation` field — use it for 'Explain This' tooltips."

| Field | UI display | Transformation |
|---|---|---|
| `summary` | Modal body | Direct |
| `whyItMatters` (string[]) | "Why it matters" section | Rendered with sparkle icons (static UI) |
| `evidence[].{label, detail}` | Badge + detail pairs | `label` → badge, `detail` → text |
| `sources[]` | List with external links | Same rendering as §6.10 |

For `explain` intents, `answer` carries `{ question, explanation, evidence[], sources[] }` instead of a competitor profile (ORCHESTRATOR.md §5.2).

### 6.12 SWOT (business-level) — PARSER.md §2.12 (SwotGrid)

`{ strengths[], weaknesses[], opportunities[], threats[] }` → 4-panel grid: Strengths (emerald), Weaknesses (rose), Opportunities (sky), Threats (amber). Direct rendering of string arrays. Also present per-competitor (`competitors[].swot`) as an expandable 4-panel grid (§6.2).

### 6.13 Answer block (lookup responses) — ORCHESTRATOR.md §5.2

Populated for `question` / `compare` / `explain` intents; `null` for bootstrap (§14.3).

| Field | Type | UI usage | Transformation | Missing behavior |
|---|---|---|---|---|
| `answer.summary` | string | Lookup card lead text | Direct | — |
| `answer.competitors[]` | array | Lookup cards (one-off) | Same rendering as §6.2 + source/confidence badges | — |
| `answer.comparedTo[]` | array | Comparison table cells (2-column or N-column table) | Mirrors `competitors[]` shape | Empty for non-compare intents |
| `answer.question` | string | (explain intent) | Direct | null |
| `answer.explanation` | string | (explain intent) | Direct | null |
| `answer.evidence[]` | array | (explain intent) | Badge + detail pairs | null |
| `answer.sources[]` | array | Per-lookup source list, shown at bottom (§9.2) | §6.10 rendering | null |

Example (ORCHESTRATOR_PROMPT.md): `answer.competitors[0].name === "Fragante"`, `source: "web"`, `lookupConfidence: 78`, with ≥2 sources expected in `answer.sources` (§13.3).

### 6.14 Orchestrator-only `data` fields (ORCHESTRATOR.md §6.1) — no PARSER.md UI inventory

These appear in the orchestrator's success response but have **no field-level UI spec in PARSER.md**; UI usage beyond what is listed is **not documented**:

| Field | Type | Example | Documented UI usage |
|---|---|---|---|
| `business_summary` | string | "..." | Not documented |
| `profile` | object (BusinessProfile) | `{...}` | See §6.1 |
| `executive_summary` | string | "..." | Not documented |
| `market_info` | object `{size, growth}` | `{"size": "$5B", "growth": "12%"}` | Not documented |
| `positioning` | string | "..." | Not documented |
| `gaps` | string[] | `["..."]` | Not documented (distinct from `marketGaps[]` objects) |
| `opportunities` | string[] | `["..."]` | Not documented |
| `risks` | string[] | `["..."]` | Not documented |
| `comparisons` | array | `[]` | Not documented |
| `metric_cards` | array | `/* MetricCard[] */` | "KPI cards" (§10); **MetricCard schema not documented** |
| `report` | string | "..." | "Full report viewer" (§10); shape conflict C3 |
| `metadata.generated_at` | ISO 8601 string | `"2026-09-10T12:00:00+00:00"` | Not documented |
| `metadata.model_used` | string | `"llm-brain"` | Not documented |
| `metadata.confidence` | number (0–1 scale) | `0.85` | Not documented. **Note scale differs from `insights[].confidence` and `lookupConfidence` (0–100)** |
| `metadata.processing_time_ms` | int | `4500` | Not documented |

### 6.15 Context update (stored by UI in `sessionStorage`) — PARSER.md §7 / ORCHESTRATOR.md §8

Key: `'competitor_analysis_context'` (static frontend constant). Stored via `sessionStorage.setItem(CONTEXT_KEY, JSON.stringify(response.context_update))` — cleared when the tab/browser closes. Sent back with every follow-up request.

```json
{ "version": 1,
  "business": { "name": "Scentra", "industry": "Fragrance", "pricing": "₹3,500", "model": "DTC" },
  "entities": { "competitors": ["forest-essentials", "kama-ayurveda", "jo-malone-india", "fragante"], "focus": null },
  "result_meta": { "requested_count": 3, "retrieved_count": 4, "filters": [] },
  "constraints": { "included": ["Indian premium"], "excluded": [] },
  "keywords": ["Fragrance", "Premium", "DTC"] }
```

- `entities.competitors`: **max 3 active** slugs; a 4th addition evicts the oldest `in_context` entity (lowest `last_referenced_at`) into `evicted_entities[]`; evicted profiles stay cached 24h (ORCHESTRATOR.md §8.4).
- `entities.focus`: resolves pronouns "it" / "that one" (§8.3).
- `business.pricing` is a preformatted string (e.g. "₹3,500") — PARSER.md §2.1 says `pricing` is "Form only", so it is **not displayed** in the UI directly.
- What NOT to store (PARSER.md §7.4): full conversation history, complete AnalysisData, raw user messages, generated report/insight text, chart data, sensitive business data beyond the session.

---

## 7. Static UI Values

Text permanently defined by the frontend (never supplied by the backend):

| Static value | Where |
|---|---|
| "Explain this" | Tooltip/modal trigger on charts, tables, insights (README, PARSER.md) |
| "Most popular" | PricingTier card when `highlighted === true` |
| "Ship now" / "Next" / "Later" | Action plan group headers for P0/P1/P2 |
| "Why: ", "Owner: ", "Effort: ", "Impact: ", "Horizon: " | Action plan item prefixes |
| "Impact: " | Insight impact badge prefix |
| "% confidence" | Insight confidence suffix |
| "%" | `marketShare`, `growthRate`, `adoption` suffixes |
| "/100" | opportunity/difficulty score bars |
| "From ₹", "₹" | Product `startingPrice`, PricingTier `priceMonthly` prefixes |
| "Custom" | `priceMonthly` display when value is the string "Custom" (also the documented default) |
| "/mo", "/mo · billed yearly" | `billing` display |
| "·" | Source "{publisher} · {date}" separator |
| "—" (em dash) | Missing `funding`/`founded`/optional fields on partial entities |
| "No data available" | Failed chart generation fallback |
| "Invalid request format" / "Service unavailable, try again" / "Research service down, partial data available" | HTTP 422 / 500 / 502 error text |
| "Business name required" | Validation error for empty `businessName` |
| Badge color rules (Leader=red …, GA=green …, etc.) | All enum→color mappings in §6 |
| SWOT panel colors (emerald/rose/sky/amber) | SwotGrid |
| Tab names: Overview, Competitors, Products, Pricing, Market Gaps, Insights, Reports, Sources (+ Chat) | 9 tabs (README "9 tabs"; `ui_state.active_tab` enum in PARSER.md §6) |
| `sessionStorage` key `'competitor_analysis_context'` | Context storage |
| `VITE_API_URL` | Backend base URL configuration |
| Skeleton card, empty state, error state | `loading` / zero-results / `error` rendering |
| "undo" offer on eviction toast | ORCHESTRATOR.md §14.4 |

---

## 8. Backend Dynamic Values

Values originating from the API/backend (summary — full per-field detail in §6):

- All `data.*` domain objects: `competitors[]`, `products[]`, `pricingTiers[]`, `marketGaps[]`, `insights[]`, `recommendations[]`, `actionPlan[]`, `reports[]`, `charts`, `swot`, `sources[]`, plus profile/business fields.
- `answer.*` block for lookup intents (`summary`, `competitors[]`, `comparedTo[]`, `question`, `explanation`, `evidence[]`, `sources[]`).
- Provenance/confidence: `entity_statuses.competitors[].source` ("web"|"context"), `lookupConfidence` (0–100), per-entity `status` and `missing_fields`.
- Counts: `result_counts.{requested, retrieved, valid, displayed}` → result count indicators.
- Diagnostics: `missing_data[].{field, reason, severity}`, `evicted_entities[]`, `error`, `operations_performed[]`, `derived_data`, `ui_state.{active_tab, highlighted_entities}`.
- Session context: `context_update` (stored in sessionStorage, echoed back).
- Orchestrator-only fields listed in §6.14 (business_summary, market_info, metadata, etc.).
- Internal pipeline values that never reach the UI directly: WebHunter query templates (`"{company} company profile {industry}"`, `"{company} pricing {industry}"`, `"{company} competitors market share"`, `"{company} funding headquarters"`), WebHunter request `{query, max_results: 8, max_pages: 5, variants, region: "wt-wt", timeout_ms: 30000}`, LLMPing extraction schema (`confidence` 0–100, `sourceCount`), env vars (`LLMPING_URL`, `WEBHUNTER_URL`, `LLMPING_TIMEOUT` 60s, `WEBHUNTER_TIMEOUT` 30s, `SERVICE_HOST`, `SERVICE_PORT`, `LOG_LEVEL`, `LLMPING_API_KEY` sent as `Authorization: Bearer ...`).

---

## 9. Derived UI Values

Values the **frontend** calculates/formats/combines from backend data (per PARSER.md display rules):

| Derived display | From | Rule |
|---|---|---|
| `marketShare` text | `competitors[].marketShare` | number + "%" |
| `growthRate` text + color | `competitors[].growthRate` | number + "%"; green if > 30 |
| `startingPrice` text | `products[].startingPrice` | "From ₹" + number |
| `priceMonthly` text | `pricingTiers[].priceMonthly` | "₹" + number, or "Custom" |
| `billing` text | `pricingTiers[].billing` | "monthly" → "/mo"; "yearly" → "/mo · billed yearly" |
| Score bars | `opportunityScore`, `difficultyScore` | number + "/100" + progress bar (emerald / rose) |
| Adoption bar | `features[].adoption` | number + "%" + progress bar |
| Insight confidence text | `insights[].confidence` | number + "% confidence" |
| Report pages badge | `reports[].pages` | number + " pages" |
| Source byline | `publisher`, `date` | "{publisher} · {date}" |
| ComparisonTable "Biggest weakness" | `weaknesses[0]` | first element of weaknesses array |
| 2-char logo | `name` + `logoColor` | 2 characters (derivation not documented) on colored badge |
| Enum → badge color | all enum fields | static color mapping (§6) |
| `lookupConfidence` badge | `lookupConfidence` | subtle badge when < 60 |
| Warnings list | `missing_data[]` | `filter(m => m.severity === 'warning')` (ORCHESTRATOR.md §9.3) |
| Eviction toast text | `evicted_entities[]` | "{EntityName} moved to history" (entity name interpolated, ORCHESTRATOR.md §8.4) |
| 504 error text | HTTP 504 + entity | "Couldn't look up {entity}, skipping" |

Backend-side derived values (calculated by the parser/orchestrator, not the UI): `marketShare` normalization to sum 100 (§4.1, "calculate" operation), chart generation from raw metrics (§10), slug IDs from names, `result_counts`, `derived_data`, `ui_state`.

---

## 10. Placeholders / Fallbacks

| Situation | Displayed | Source |
|---|---|---|
| Missing `funding` / `founded` (or any optional field on a `partial` entity) | "—" | PARSER.md §2.2, §4.4 |
| `priceMonthly` not a number | "Custom" (also the documented default) | PARSER.md §2.4, §4.1 |
| Chart generation fails after retry | "No data available" | PARSER.md §4.2 |
| Entity `loading` | Skeleton card / placeholder | ORCHESTRATOR.md §6.3, PARSER.md §4.4 |
| Entity `failed` | Entity skipped; entry in `missing_data` | PARSER.md §4.4–4.5 |
| Zero competitors available | Empty state ("If 0 are available, render the empty state") | PARSER.md §14 |
| `status: "partial"` | Valid data rendered + warnings from `missing_data` (severity "warning") | ORCHESTRATOR.md §6.2, §9.3 |
| `status: "error"` | Error state; any valid data preserved | ORCHESTRATOR.md §6.2 |
| HTTP 422 / 500 / 502 / 504 | Static error strings (§5.3) | ORCHESTRATOR.md §11.1 |
| Evicted entity | One-line toast "{name} moved to history" + "undo" (re-sends same `refine` request) | ORCHESTRATOR.md §8.4, §14.4 |
| `lookupConfidence < 60` | Subtle confidence badge | ORCHESTRATOR.md §6.3 |
| `insights[].explanation` absent | No "Explain this" content (field optional) | PARSER.md §2.6 |
| Optional Source fields absent | Corresponding segment/icon omitted (exact rule not documented) | PARSER.md §2.10 |

Degradation priority (PARSER.md §4.6): `Complete data → Partial but useful data → Explicit unavailable state → Empty state → Error state`. "Never: Partial data → Discard everything → Broken UI." The orchestrator **never fabricates** entities to fill the requested count (ORCHESTRATOR.md §7.2).

---

## 11. Placeholder & Dynamic Value Inventory

Every placeholder/dynamic value mentioned in the docs, with the 6 required points:

1. **`{entity}` in "Couldn't look up {entity}, skipping"** — Provided by: HTTP 504 path, entity name from the failed lookup. Expected: company name string. Appears: error message. Formatted: interpolated into static sentence. Unavailable: not applicable (message only exists on failure). Real backend value (dynamic interpolation into static UI text).
2. **`{publisher}` / `{date}` in "{publisher} · {date}"** — Provided by: `Source.publisher`, `Source.date`. Expected: strings. Appears: Sources list byline. Formatted: joined with " · ". Unavailable: both optional; omission rule not documented. Real backend values.
3. **`{confidence}` in "{confidence}% confidence"** — Provided by: `insights[].confidence` (0–100). Appears: InsightCard. Formatted: number + "% confidence". Unavailable: clamped 0–100 per validation. Real backend value.
4. **`{pages}` in "{pages} pages"** — Provided by: `reports[].pages`. Appears: ReportCard gray badge. Formatted: number + " pages". Unavailable: not documented. Real backend value.
5. **`{price}` in "₹{price}" / "From ₹{price}"** — Provided by: `pricingTiers[].priceMonthly`, `products[].startingPrice`. Expected: number (or "Custom" for priceMonthly). Appears: PricingTable, ProductBreakdown. Formatted: static "₹" (and "From ") prefix. Unavailable: priceMonthly defaults to "Custom". Real backend value with static prefix.
6. **`{rationale}`, `{owner}`, `{effort}`, `{impact}`, `{horizon}` in "Why:/Owner:/Effort:/Impact:/Horizon: {…}"** — Provided by: `actionPlan[]` fields. Appears: ActionPlanList items. Formatted: static prefixes. Unavailable: not documented. Real backend values.
7. **`{impact}` in "Impact: {impact}" (insights)** — Provided by: `insights[].impact`. Appears: InsightCard badge. Formatted: static prefix + enum, color High=red/Medium=amber/Low=gray. Unavailable: default "Medium". Real backend value.
8. **`{company}` / `{industry}` in WebHunter query templates** — Provided by: orchestrator (extracted from user `message` + `context_update.business.industry`). Expected: company/industry strings. Appears: backend search queries only — **never displayed in the UI**. Real backend-internal placeholders.
9. **`{company_name}`, `{title_1}`, `{snippet_1}` in the LLMPing extraction prompt** — Provided by: orchestrator from WebHunter results. Backend-internal only; never displayed.
10. **`{name}` in "{EntityName} moved to history" toast** — Provided by: `evicted_entities[]`. Appears: eviction toast. Formatted: entity name + static suffix. Unavailable: toast only shown when eviction occurs. Real backend value.
11. **`answer.competitors[].id` values like "fragante-s1" (source ids)** — Provided by: backend. Expected: string ids. Appears: internal keys; UI displays `title`/`publisher`/`date`, not ids. Static-ish backend identifiers.
12. **`session_id`** — Provided by: **the UI itself** (UI-generated UUID, ORCHESTRATOR.md §4.2). Expected: stable session identifier. Appears: request payloads only. Static frontend-generated value (not backend).
13. **`requested_count`** — Provided by: UI request. Expected: int 1–3, default 3. Appears: request + echoed in `result_counts.requested` / `context_update.result_meta.requested_count`. Real UI-supplied value.
14. **`"Custom"` priceMonthly** — Provided by: backend as a string value (also the validation default). Expected: literal "Custom" or number. Appears: PricingTable card. Formatted: displayed as-is. Real backend value that doubles as the fallback for missing numeric prices.
15. **`"—"` em dash** — Static UI placeholder for missing optional fields (funding, founded, etc.). Not a backend value.
16. **`VITE_API_URL`** — Static config value from `.env`; provides the backend base URL. Not displayed.
17. **`ui_state.active_tab`** — Provided by: backend parser output. Expected: one of `chat|overview|competitors|products|pricing|market-gaps|insights|reports|sources`. Appears: tab activation. Unavailable: not documented. Real backend value (UI hint).
18. **`context_update` placeholders in docs** (`{ /* last context_update, may be null */ }`, `{ /* last form_input from bootstrap, may be null */ }`, `{ /* last full response, may be null */ }`) — Provided by: UI sessionStorage. Expected: previous response objects or null. Appears: request payloads. Unavailable: null allowed. Real UI-held values echoed to backend.

---

## 12. Undetermined / Not-Documented Items (explicit gaps)

- **C3**: reconciliation of `report` (string, ORCHESTRATOR.md) vs `report` (object, README) vs `reports[]` (array, PARSER.md) — **Undetermined**; PARSER.md's `reports[]` is the only field-level UI contract.
- **C4**: canonical `pricingTier` enum ("Mid-range" vs "Mid") — **Undetermined** (cosmetic; raw string displayed).
- **C7**: `charts` as named object (PARSER.md) vs array (ORCHESTRATOR.md/README) — **Undetermined**.
- 2-char logo: which 2 characters are derived from `name` — not documented.
- `insights[].category === "Insight"` badge color — not documented (only 4 of 5 enum values have colors).
- `metric_cards[]` (MetricCard) schema — not documented; only UI mapping ("KPI cards").
- UI usage of `business_summary`, `executive_summary`, `market_info`, `positioning`, `gaps`, `opportunities`, `risks`, `comparisons`, `metadata.*`, `operations_performed[]`, `derived_data` — not documented.
- `ChatMessage` schema (referenced by `AnalysisData.conversation`) — not defined in any doc.
- `source` segment omission rule in "{publisher} · {date}" when publisher/date is null — not documented.
- `generated_at` display format (if shown) — not documented.
- UI usage of `relatedCompetitors` (InsightItem) — not documented.

---

## Backend → UI Contract

Authoritative quick-reference. Fields marked *(legacy)* are from README.md's old `/api/v1/analyze` contract; all others are from the current `/api/v1/parser/execute` contract. "—" in Missing/Empty means "not documented".

| Backend Field | Type | Purpose | UI Usage | Transformation | Missing/Empty Behavior |
|---|---|---|---|---|---|
| `intent` | string enum | Echoes request intent (`bootstrap`, `question`, `refine`, `compare`, `explain`, `regenerate`, `follow-up`) | Determines render mode (`data.*` vs `answer.*`) | None | — |
| `status` | string enum | Response outcome | success→render; partial→render+ warnings; error→error state | None | — |
| `error` | string \| null | Error description | Error state text | Displayed as-is | null → no error UI |
| `data` | object \| null | Full analysis payload | All main tabs (Overview, Competitors, Products, Pricing, Market Gaps, Insights, Reports, Sources) | snake_case → camelCase via `HttpApi` (README §5) | null for question-only responses (C8) |
| `data.businessName` / `profile` | string / object | Business identity | Sidebar, Chat header, Overview title, Action plan title | None | Empty → "Business name required" |
| `data.idea` | string | Business description | Overview subtitle | None | — |
| `data.industry`, `data.geography` | string | Classification | Overview badges | None | — |
| `data.competitors[]` | array | Competitor set (max 3 dynamic) | Competitor cards, ComparisonTable | See per-field rules below | `failed` entities skipped, reported in `missing_data` |
| `competitors[].id` | string (slug) | Entity key | Charts/context references | Generated: `name.toLowerCase().replace(/\s+/g,'-')` | Must be unique |
| `competitors[].name` | string | Display name | Card header; "Vendor" column | 2-char logo (derivation n/d) + `logoColor` | Required, non-empty |
| `competitors[].logoColor` | hex string | Logo badge color | Card header logo | Direct color use | — |
| `competitors[].description` | string | Summary | Card body | None | — |
| `competitors[].funding` | string | Funding info | Stat | None | "—" |
| `competitors[].founded` | string | Founding info | Stat | None | "—" |
| `competitors[].hq` | string | Headquarters | Stat; "HQ" column | None | "—" |
| `competitors[].marketShare` | number 0–100 | Share % | Stat; "Share" column | + "%" suffix; clamped; redistributed to sum 100 | Clamped/redistributed |
| `competitors[].growthRate` | number | Growth % | Stat; "Growth" column | + "%"; green if > 30 | Default 0 |
| `competitors[].pricingTier` | string | Tier label | Gray badge; "Pricing tier" column | Raw string (enum conflict C4) | — |
| `competitors[].marketPosition` | enum | Position label | Badge; "Position" column | Leader=red, Challenger=violet, Niche=green, Emerging=amber | Default "Emerging" |
| `competitors[].strengths` / `weaknesses` | string[] | Pros/cons | Check/alert icon lists (max 3); `weaknesses[0]` → "Biggest weakness" | Truncate display to 3 | — |
| `competitors[].swot` | object | Per-competitor SWOT | Expandable 4-panel grid (emerald/rose/sky/amber) | None | — |
| `competitors[].explanation` | object | "Explain this" content | Modal (§6.11) | None | Optional |
| `competitors[].status` | enum | Entity state | Render/skeleton/skip | None | §5.2 rules |
| `data.products[]` | array | Product breakdown | ProductBreakdown | — | Failed → skip |
| `products[].startingPrice` | number | Entry price | "From ₹{price}" | Static "From ₹" prefix | — |
| `products[].features[].maturity` | enum | Feature maturity | Badge (GA=green, Beta=amber, Roadmap=blue, Deprecated=gray) | None | Default "GA" |
| `products[].features[].adoption` | number 0–100 | Adoption % | Progress bar + "%" | Clamped | Clamped |
| `data.pricingTiers[]` | array | Pricing plans | PricingTable cards | — | — |
| `pricingTiers[].priceMonthly` | number \| "Custom" | Monthly price | "₹{price}" or "Custom" | Static "₹" prefix | Default "Custom" |
| `pricingTiers[].billing` | enum | Billing cadence | "/mo" or "/mo · billed yearly" | Enum → static suffix | — |
| `pricingTiers[].highlighted` | boolean | Highlight flag | "Most popular" badge | Boolean → badge | Absent → no badge |
| `data.marketGaps[]` | array | Market opportunities | MarketGapCards | — | — |
| `marketGaps[].opportunityScore` / `difficultyScore` | number 0–100 | Scores | Progress bars "/100" (emerald / rose) | Clamped | Clamped |
| `marketGaps[].estimatedRevenue` | string | Revenue estimate | Gray badge | None | — |
| `marketGaps[].affectedSegments` | string[] | Segments | Blue badges | None | — |
| `data.insights[]` / `recommendations[]` | array | Insights (same shape) | InsightCards | — | Failed → skip |
| `insights[].category` | enum | Insight class | Badge (Opp=green, Risk=red, Trend=blue, Rec=violet; "Insight" color n/d) | None | Default "Insight" |
| `insights[].impact` | enum | Impact level | "Impact: {impact}" badge (High=red, Medium=amber, Low=gray) | Static prefix | Default "Medium" |
| `insights[].confidence` | number 0–100 | Confidence | "{n}% confidence" | + "% confidence" suffix; clamped | Clamped |
| `data.actionPlan[]` | array | Recommended actions | ActionPlanList timeline | — | — |
| `actionPlan[].priority` | enum P0/P1/P2 | Priority | Group header → "Ship now"/"Next"/"Later" | Enum → static label | Default "P1" |
| `actionPlan[].horizon` / `effort` / `impact` | enums | Planning attrs | "Horizon:/Effort:/Impact: {value}" | Static prefixes | Defaults "Next"/"Medium"/"Medium" |
| `actionPlan[].rationale` / `owner` | string | Justification / owner | "Why: {rationale}" / "Owner: {owner}" | Static prefixes | — |
| `data.reports[]` | array | Reports | ReportCards | — | See C3 shape conflict |
| `reports[].type` | enum | Report class | Blue badge | None | Default "Executive Summary" |
| `reports[].pages` | number | Length | "{n} pages" gray badge | + " pages" suffix | — |
| `reports[].sections[].heading` | string | Section heading | Expandable list heading | Uppercased | — |
| `data.charts` (object per PARSER.md / array per ORCHESTRATOR.md — C7) | ChartData[] | Visualizations | Chart components (custom SVG: bar/line/area/radar/pie) | `kind` → renderer; derived from marketShare/growthRate/priceMonthly/adoption | Retry fails → "No data available" |
| `charts[].series[].points[].value` | number | Data point | Bar height / point position / tooltip | Direct numeric use | — |
| `data.swot` | object | Business SWOT | SwotGrid 4-panel (emerald/rose/sky/amber) | None | — |
| `data.sources[]` | array | Citations | SourcesList | `{publisher} · {date}` byline; `url` → external link icon | 0 retries → omitted |
| `answer` | object \| null | One-off lookup result | Lookup cards / comparison table / explain modal | None | null for bootstrap |
| `answer.summary` | string | Answer lead | Lookup card lead text | None | — |
| `answer.competitors[].source` | "web" \| "context" | Provenance | Small badge ("from web" / "from session") | None | — |
| `answer.competitors[].lookupConfidence` | int 0–100 | Lookup confidence | Subtle badge when < 60 | Threshold compare | — |
| `answer.comparedTo[]` | array | Comparison column(s) | Comparison table cells (N-column) | Mirrors competitors[] shape | Empty for non-compare |
| `answer.question` / `explanation` / `evidence[]` / `sources[]` | mixed | Explain-intent payload | Explain modal / source list | §6.11 rules | null |
| `missing_data[]` | array | Failure reports | Warning notifications | `filter(severity === 'warning')` | Empty → nothing shown |
| `evicted_entities[]` | array | Evicted entity slugs | "{Name} moved to history" toast + undo | Name interpolation | Empty → no toast |
| `result_counts` | object {requested, retrieved, valid, displayed} | Result counts | Result count indicators | None | — |
| `entity_statuses.competitors[]` | array | Per-entity status | Status-driven rendering + badges | §5.2 rules | — |
| `context_update` | object | Session context | Stored in `sessionStorage['competitor_analysis_context']`, echoed on follow-ups | `JSON.stringify` | Absent → keep prior context |
| `ui_state.active_tab` / `highlighted_entities` | string / array | UI hints | Tab activation / entity highlighting | None | — |
| `derived_data` | object | Provenance of generated charts/calcs | Not documented | — | — |
| `operations_performed[]` | string[] | Ops audit | Not documented | — | — |
| `data.metric_cards[]` | array (MetricCard n/d) | KPIs | KPI cards | — | — |
| `data.metadata.*` | string/number | Generation metadata (`generated_at`, `model_used`, `confidence` 0–1, `processing_time_ms`) | Not documented | — | — |
| `data.business_summary` / `executive_summary` / `positioning` / `market_info` / `gaps` / `opportunities` / `risks` / `comparisons` | strings/arrays/object | Orchestrator narrative fields | Not documented in PARSER.md | — | — |
| *(legacy)* `chart_type` / `labels` / `datasets[]` | string / array | Old chart schema (README) | Replaced by `kind` / `series[].points` (C2) | Conceptual mapping only | *(legacy)* |
| *(legacy)* `competitors` / `research_goals` request fields | array | Old request shape | Replaced by `parser_input.form_input` (C1) | — | *(legacy)* |

---

*End of contract. Everything above is established solely from `README.md`, `ORCHESTRATOR.md`, `ORCHESTRATOR_PROMPT.md`, `PARSER.md`, and `DOCKER.md`; items the docs do not settle are marked **Undetermined** or "not documented" rather than guessed.*
