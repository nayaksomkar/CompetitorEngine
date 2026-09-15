# Parser.md — Dynamic Data, Partial Results & UI Data Flow

## 1. Core Objective

The parser transforms arbitrary user input into the exact data structure required by the UI. Every value currently displayed in the hardcoded UI must be producible by the parser when a user provides equivalent information.

The sample data (perfume/protein) demonstrates the UI structure — the parser must dynamically generate equivalent structures for ANY user input.

**Key Requirements:**
- Document ALL data the UI displays
- Fault-tolerance: validation, failure protection, limited retries
- If some data fails, return only valid data with clear error states
- **Max 3 companies for dynamic data** (reduces LLM cost)
- Do NOT modify static/sample data behavior

---

## 2. Complete UI Data Inventory

### 2.1 BusinessProfile (Questionnaire Input)

| Field | Type | Required | UI Display Location |
|-------|------|----------|---------------------|
| `businessName` | string | Yes | Sidebar, Chat header, Overview title, Action plan title |
| `idea` | string | Yes | Overview subtitle |
| `industry` | string | Yes | Overview badge |
| `productsServices` | string[] | No | Form only (used to derive products) |
| `targetCustomers` | string | Yes | Form only (used for analysis context) |
| `geography` | string | No | Overview badge |
| `pricing` | string | No | Form only (used to derive pricing tiers) |
| `businessModel` | string | No | Form only (used for analysis context) |
| `competitors` | string[] | No | Form only (used to derive competitor profiles) |
| `differentiators` | string | No | Form only (used for SWOT/insights) |
| `researchGoals` | string[] | No | Form only (used for action plan/reports) |

### 2.2 Competitor (Displayed in CompetitorCard + ComparisonTable)

```json
{
  "id": "string (slug)",
  "name": "string",
  "logoColor": "string (hex color for 2-char logo)",
  "description": "string",
  "funding": "string",
  "founded": "string",
  "hq": "string (headquarters location)",
  "marketShare": "number (0-100)",
  "growthRate": "number (percentage, can be negative)",
  "pricingTier": "string (e.g., Premium, Ultra-Premium)",
  "marketPosition": "Leader|Challenger|Niche|Emerging",
  "strengths": ["string (up to 3 displayed)"],
  "weaknesses": ["string (up to 3 displayed)"],
  "swot": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "opportunities": ["string"],
    "threats": ["string"]
  },
  "explanation": {
    "summary": "string",
    "whyItMatters": ["string"],
    "evidence": [{ "label": "string", "detail": "string" }],
    "sources": ["Source"]
  },
  "status": "complete|partial|loading|failed"
}
```

**UI Display Locations:**
- `name`, `logoColor` → Card header with 2-char logo
- `marketPosition` → Badge (red=Leader, violet=Challenger, green=Niche, amber=Emerging)
- `pricingTier` → Gray badge
- `description` → Card body
- `marketShare` → Stat with "%" suffix
- `growthRate` → Stat with "%" suffix, green if >30
- `funding` → Stat or "—"
- `founded` → Stat or "—"
- `strengths` → List with check icons (max 3)
- `weaknesses` → List with alert icons (max 3)
- `swot` → Expandable 4-panel grid
- `explanation` → "Explain this" modal

**ComparisonTable columns:** Vendor (name), Share (marketShare%), Growth (growthRate%), Pricing tier, Position, HQ, Biggest weakness (weaknesses[0])

### 2.3 Product / ProductFeature (Displayed in ProductBreakdown)

```json
{
  "id": "string",
  "competitorId": "string (links to competitor)",
  "name": "string",
  "tagline": "string",
  "category": "string",
  "pricingModel": "string",
  "startingPrice": "number",
  "features": [
    {
      "id": "string",
      "name": "string",
      "description": "string",
      "maturity": "Beta|GA|Deprecated|Roadmap",
      "adoption": "number (0-100)"
    }
  ],
  "status": "complete|partial|loading|failed"
}
```

**UI Display Locations:**
- `name` → Header
- `category` → Blue badge
- `pricingModel` → Gray badge
- `tagline` → Subtitle
- `startingPrice` → "From ₹{price}"
- `features` → Filterable/sortable list
- `maturity` → Badge (GA=green, Beta=amber, Roadmap=blue, Deprecated=gray)
- `adoption` → Progress bar with "%"

### 2.4 PricingTier (Displayed in PricingTable)

```json
{
  "id": "string",
  "competitorId": "string (links to competitor)",
  "name": "string",
  "priceMonthly": "number|string ('Custom')",
  "billing": "monthly|yearly",
  "features": ["string"],
  "bestFor": "string",
  "pricingModel": "string",
  "highlighted": "boolean (shows 'Most popular' badge)"
}
```

**UI Display Locations:**
- `name` → Card title
- `highlighted` → "Most popular" badge
- `pricingModel` → Gray badge
- `bestFor` → Text
- `priceMonthly` → "₹{price}" or "Custom"
- `billing` → "/mo" or "/mo · billed yearly"
- `features` → List with check icons

### 2.5 MarketGap (Displayed in MarketGapCard)

```json
{
  "id": "string",
  "title": "string",
  "description": "string",
  "opportunityScore": "number (0-100)",
  "difficultyScore": "number (0-100)",
  "estimatedRevenue": "string",
  "affectedSegments": ["string"]
}
```

**UI Display Locations:**
- `title` → Card title
- `description` → Card body
- `estimatedRevenue` → Gray badge
- `opportunityScore` → Progress bar "/100" (emerald)
- `difficultyScore` → Progress bar "/100" (rose)
- `affectedSegments` → Blue badges

### 2.6 InsightItem (Displayed in InsightCard)

```json
{
  "id": "string",
  "title": "string",
  "summary": "string",
  "detail": "string",
  "category": "Opportunity|Risk|Trend|Recommendation|Insight",
  "impact": "High|Medium|Low",
  "confidence": "number (0-100)",
  "relatedCompetitors": ["string"],
  "explanation": "Explanation (optional)"
}
```

**UI Display Locations:**
- `category` → Badge (Opportunity=green, Risk=red, Trend=blue, Recommendation=violet)
- `impact` → "Impact: {impact}" badge (High=red, Medium=amber, Low=gray)
- `confidence` → "{confidence}% confidence"
- `title` → Card title
- `summary` → Card body
- `detail` → Expanded content
- `explanation` → "Explain this" modal

### 2.7 ActionPlanItem (Displayed in ActionPlanList)

```json
{
  "id": "string",
  "title": "string",
  "description": "string",
  "rationale": "string",
  "owner": "string",
  "priority": "P0|P1|P2",
  "horizon": "Now|Next|Later",
  "effort": "Low|Medium|High",
  "impact": "Low|Medium|High"
}
```

**UI Display Locations:**
- `priority` → Group header (P0="Ship now", P1="Next", P2="Later")
- `title` → Item title
- `description` → Item body
- `rationale` → "Why: {rationale}"
- `owner` → "Owner: {owner}"
- `effort` → "Effort: {effort}"
- `impact` → "Impact: {impact}"
- `horizon` → "Horizon: {horizon}"

### 2.8 Report / ReportSection (Displayed in ReportCard)

```json
{
  "id": "string",
  "title": "string",
  "summary": "string",
  "type": "Executive Summary|Deep Dive|Market Landscape|Go-to-Market",
  "date": "string",
  "pages": "number",
  "sections": [
    {
      "heading": "string",
      "body": "string"
    }
  ]
}
```

**UI Display Locations:**
- `type` → Blue badge
- `pages` → "{pages} pages" gray badge
- `date` → Text
- `title` → Card title
- `summary` → Card body
- `sections` → Expandable list with heading (uppercase) + body

### 2.9 ChartData (Displayed in Chart)

```json
{
  "title": "string",
  "kind": "bar|line|area|radar|pie",
  "xLabel": "string",
  "yLabel": "string",
  "series": [
    {
      "id": "string",
      "name": "string",
      "color": "string (hex)",
      "points": [
        {
          "label": "string",
          "value": "number",
          "color": "string (optional)"
        }
      ]
    }
  ]
}
```

**UI Display Locations:**
- `title` → Chart title
- `kind` → Determines chart type rendered
- `series` → Data visualization with legend
- `points` → Individual data points with hover tooltips

### 2.10 Source (Displayed in SourcesList)

```json
{
  "id": "string",
  "title": "string",
  "url": "string (optional)",
  "publisher": "string (optional)",
  "date": "string (optional)",
  "snippet": "string (optional)"
}
```

**UI Display Locations:**
- `title` → Source title
- `publisher` + `date` → "{publisher} · {date}"
- `snippet` → Body text
- `url` → External link icon

### 2.11 Explanation (Displayed in Explain modal)

```json
{
  "summary": "string",
  "whyItMatters": ["string"],
  "evidence": [
    {
      "label": "string",
      "detail": "string"
    }
  ],
  "sources": ["Source"]
}
```

**UI Display Locations:**
- `summary` → Modal body
- `whyItMatters` → "Why it matters" section with sparkle icons
- `evidence` → Badge + detail pairs
- `sources` → List with external links

### 2.12 SWOT (Business-level, displayed in SwotGrid)

```json
{
  "strengths": ["string"],
  "weaknesses": ["string"],
  "opportunities": ["string"],
  "threats": ["string"]
}
```

**UI Display Locations:**
- 4-panel grid: Strengths (emerald), Weaknesses (rose), Opportunities (sky), Threats (amber)

### 2.13 AnalysisData (Root State)

```json
{
  "businessName": "string",
  "industry": "string",
  "idea": "string",
  "targetCustomers": "string",
  "geography": "string",
  "pricing": "string",
  "businessModel": "string",
  "differentiators": "string",
  "researchGoals": ["string"],
  "profile": "BusinessProfile",
  "competitors": ["Competitor"],
  "products": ["Product"],
  "pricingTiers": ["PricingTier"],
  "marketGaps": ["MarketGap"],
  "insights": ["InsightItem"],
  "recommendations": ["InsightItem"],
  "actionPlan": ["ActionPlanItem"],
  "reports": ["Report"],
  "charts": {
    "marketShare": "ChartData",
    "marketSharePie": "ChartData",
    "growth": "ChartData",
    "growthPie": "ChartData",
    "pricing": "ChartData",
    "pricingPie": "ChartData",
    "featureAdoption": "ChartData",
    "featureAdoptionPie": "ChartData"
  },
  "swot": "SWOT",
  "sources": ["Source"],
  "conversation": ["ChatMessage"]
}
```

---

## 3. Dynamic Result Limits

### 3.1 Company Limit (CRITICAL for LLM Cost)

**Maximum 3 competitors for dynamically generated data.**

This limit applies ONLY to dynamically generated company data when a user provides their own input. The static/sample data (perfume/protein) remains unchanged with 4 competitors.

| Data Type | Dynamic Limit | Static/Sample |
|-----------|---------------|---------------|
| Competitors | **3 max** | 4 (unchanged) |
| Products | 2-3 | 4 (unchanged) |
| PricingTiers | 6-9 | 10 (unchanged) |
| MarketGaps | 2-3 | 4 (unchanged) |
| Insights | 3-4 | 4 (unchanged) |
| Recommendations | 2 | 2 (unchanged) |
| ActionPlan | 4-6 | 6 (unchanged) |
| Reports | 2-3 | 3 (unchanged) |
| Sources | 4-6 | 8 (unchanged) |

### 3.2 Why 3 Companies?

- Reduces LLM token usage by ~25%
- Faster response times
- Lower cost for free-tier users
- Still provides meaningful competitive analysis

---

## 4. Fault-Tolerance & Error Handling

### 4.1 Validation Rules

| Field | Validation | Failure Action |
|-------|------------|----------------|
| `businessName` | Non-empty string | Return error: "Business name required" |
| `competitors[].id` | Unique slug | Generate from name: `name.toLowerCase().replace(/\s+/g, '-')` |
| `competitors[].marketShare` | Number 0-100 | Clamp to range, redistribute if sum ≠ 100 |
| `competitors[].marketPosition` | Enum: Leader, Challenger, Niche, Emerging | Default: "Emerging" |
| `competitors[].growthRate` | Number | Default: 0 |
| `products[].features[].maturity` | Enum: GA, Beta, Roadmap, Deprecated | Default: "GA" |
| `products[].features[].adoption` | Number 0-100 | Clamp to range |
| `pricingTiers[].priceMonthly` | Number or "Custom" | Default: "Custom" |
| `marketGaps[].opportunityScore` | Number 0-100 | Clamp to range |
| `marketGaps[].difficultyScore` | Number 0-100 | Clamp to range |
| `insights[].category` | Enum: Opportunity, Risk, Trend, Recommendation, Insight | Default: "Insight" |
| `insights[].impact` | Enum: High, Medium, Low | Default: "Medium" |
| `insights[].confidence` | Number 0-100 | Clamp to range |
| `actionPlan[].priority` | Enum: P0, P1, P2 | Default: "P1" |
| `actionPlan[].horizon` | Enum: Now, Next, Later | Default: "Next" |
| `actionPlan[].effort` | Enum: Low, Medium, High | Default: "Medium" |
| `actionPlan[].impact` | Enum: Low, Medium, High | Default: "Medium" |
| `reports[].type` | Enum: Executive Summary, Deep Dive, Market Landscape, Go-to-Market | Default: "Executive Summary" |
| `charts[].kind` | Enum: bar, line, area, radar, pie | Default: "bar" |

### 4.2 Retry Policy

| Operation | Max Retries | Retry Delay | Fallback |
|-----------|-------------|-------------|----------|
| Bootstrap (full analysis) | 2 | 1s | Return partial data |
| Single competitor generation | 1 | 500ms | Skip competitor |
| Chart generation | 1 | 500ms | Show "No data available" |
| Insight generation | 1 | 500ms | Skip insight |
| Source generation | 0 | - | Omit sources |

### 4.3 Partial Result Handling

**If some data fails, return only valid data:**

```json
{
  "status": "partial",
  "data": {
    "competitors": [
      { "id": "comp-1", "name": "Competitor A", "status": "complete" },
      { "id": "comp-2", "name": "Competitor B", "status": "complete" },
      { "id": "comp-3", "name": "Competitor C", "status": "failed" }
    ]
  },
  "missing_data": [
    {
      "field": "competitors[2]",
      "reason": "Failed to generate profile for Competitor C",
      "severity": "warning"
    }
  ]
}
```

**UI renders:** 2 competitor cards, skips the 3rd.

### 4.4 Per-Entity Status

Every dynamically retrieved entity has its own status:

```json
{
  "id": "...",
  "name": "...",
  "status": "complete|partial|loading|failed",
  "missing_fields": ["funding", "founded"]
}
```

**UI behavior:**
- `complete` → Render normally
- `partial` → Render with "—" for missing fields
- `loading` → Show skeleton/placeholder
- `failed` → Skip entity, log error

### 4.5 Failure Isolation

**One failure must NOT break the entire UI:**

```
Competitor A ✓ → Render
Competitor B ✓ → Render
Competitor C ✗ → Skip, show error in missing_data
Competitor D ✓ → Render
```

Errors are isolated to the smallest possible unit.

### 4.6 Graceful Degradation Priority

```
Complete data → Partial but useful data → Explicit unavailable state → Empty state → Error state
```

**Never:** Partial data → Discard everything → Broken UI

---

## 5. Parser Pipeline

```
User Input
    ↓
[1] Context Resolution (load from sessionStorage)
    ↓
[2] Intent Classification
    ↓
[3] Entity Extraction
    ↓
[4] Semantic Intermediate Representation
    ↓
[5] Gap Analysis (available vs required)
    ↓
[6] Operation Planning
    ↓
[7] Data Generation (with retry + validation)
    ↓
[8] Append Valid Results (until stop condition)
    ↓
[9] Normalization
    ↓
[10] Derive Charts/Statistics
    ↓
[11] Validation
    ↓
[12] Context Update
    ↓
Structured Output → UI State
```

### Step 1: Context Resolution

Load existing context from `sessionStorage`:
```json
{
  "business": { "name": "...", "industry": "..." },
  "entities": { "competitors": ["id1", "id2"], "focus": "id1" },
  "constraints": { "included": [], "excluded": [] },
  "keywords": ["keyword1", "keyword2"]
}
```

### Step 2: Intent Classification

| Intent | Description |
|--------|-------------|
| `bootstrap` | Initial business description — generate full analysis |
| `question` | Ask about existing data — return answer + optional ChatAsset |
| `refine` | Modify existing data (add/remove competitor, adjust values) |
| `compare` | Compare entities side-by-side |
| `explain` | Explain a specific insight/data point |
| `regenerate` | Recreate a specific section |
| `follow-up` | Reference previous context |

### Step 3: Entity Extraction

```json
{
  "business_names": [],
  "product_names": [],
  "competitor_names": [],
  "industries": [],
  "metrics": [],
  "features": [],
  "pricing_mentions": [],
  "geographic_mentions": [],
  "comparisons": [],
  "constraints": []
}
```

### Step 4: Semantic Intermediate Representation

```json
{
  "intent": "bootstrap|question|refine|compare|explain|regenerate|follow-up",
  "entities": {},
  "facts": {},
  "constraints": [],
  "requested_operations": [],
  "missing_information": [],
  "confidence": {}
}
```

### Step 5: Gap Analysis

Determine what data already exists vs. what must be retrieved.

### Step 6: Operation Planning

| Operation | When Needed |
|-----------|-------------|
| `extract` | Always — pull values from user text |
| `infer` | Fill gaps from industry knowledge (marked with lower confidence) |
| `calculate` | Derive metrics (market share totals must = 100%) |
| `normalize` | Standardize formats, casing, units |
| `categorize` | Assign positions, maturity levels, priorities |
| `generate` | Create charts, action plans, reports from extracted data |

### Step 7: Data Generation with Retry

For each entity type, generate with retry logic:

```
for each competitor (max 3):
  retry_count = 0
  while retry_count < max_retries:
    try:
      competitor = generate_competitor()
      if validate(competitor):
        append(competitors, competitor)
        break
    catch error:
      retry_count++
      if retry_count >= max_retries:
        log_error(error)
        break
```

### Step 8: Append Valid Results

Append valid entities until stop condition:
1. Target count reached (3 for competitors)
2. All input competitors processed
3. Retrieval exhausted

### Step 9: Normalization

| Field | Normalization |
|-------|---------------|
| `marketShare` | Number 0-100, sum to 100 across competitors |
| `growthRate` | Number (percentage) |
| `adoption` | Number 0-100 |
| `confidence` | Number 0-100 |
| `opportunityScore` | Number 0-100 |
| `difficultyScore` | Number 0-100 |
| `marketPosition` | One of: Leader, Challenger, Niche, Emerging |
| `maturity` | One of: GA, Beta, Roadmap, Deprecated |
| `impact` | One of: High, Medium, Low |
| `priority` | One of: P0, P1, P2 |
| `horizon` | One of: Now, Next, Later |
| `effort` | One of: Low, Medium, High |
| `pricingTier` | One of: Budget, Mid, Premium, Ultra-Premium |
| `id` | URL-safe slug derived from name |

### Step 10: Derive Charts

Generate charts from actual competitor data:
- `marketShare` → Bar chart from `competitors[].marketShare`
- `marketSharePie` → Pie chart from same data
- `growth` → Bar chart from `competitors[].growthRate`
- `growthPie` → Pie chart from same data
- `pricing` → Bar chart from `pricingTiers[].priceMonthly`
- `pricingPie` → Pie chart from same data
- `featureAdoption` → Bar chart from `products[].features[].adoption`
- `featureAdoptionPie` → Pie chart from same data

### Step 11: Validation Checklist

- [ ] `businessName` present and non-empty
- [ ] `competitors` array has 1-3 items with unique IDs
- [ ] `marketShare` values sum to 100 (±1 rounding tolerance)
- [ ] All `id` fields are unique within their type
- [ ] Chart `series` arrays have consistent point counts
- [ ] All enum fields use valid values
- [ ] No placeholder/empty strings in required fields

### Step 12: Context Update

Update browser instance context (see Section 7).

---

## 6. Parser Output Contract

```json
{
  "intent": "bootstrap|question|refine|compare|explain|regenerate|follow-up",
  "status": "success|partial|error",
  "data": {
    "businessName": "string",
    "industry": "string",
    "idea": "string",
    "targetCustomers": "string",
    "geography": "string",
    "pricing": "string",
    "businessModel": "string",
    "differentiators": "string",
    "researchGoals": ["string"],
    "profile": "BusinessProfile",
    "competitors": ["Competitor"],
    "products": ["Product"],
    "pricingTiers": ["PricingTier"],
    "marketGaps": ["MarketGap"],
    "insights": ["InsightItem"],
    "recommendations": ["InsightItem"],
    "actionPlan": ["ActionPlanItem"],
    "reports": ["Report"],
    "charts": {
      "marketShare": "ChartData",
      "marketSharePie": "ChartData",
      "growth": "ChartData",
      "growthPie": "ChartData",
      "pricing": "ChartData",
      "pricingPie": "ChartData",
      "featureAdoption": "ChartData",
      "featureAdoptionPie": "ChartData"
    },
    "swot": "SWOT",
    "sources": ["Source"],
    "conversation": ["ChatMessage"]
  },
  "derived_data": {
    "charts_generated": ["marketShare", "growth", "pricing", "featureAdoption"],
    "calculations_performed": ["marketShare_normalization"]
  },
  "ui_state": {
    "active_tab": "chat|overview|competitors|products|pricing|market-gaps|insights|reports|sources",
    "highlighted_entities": [],
    "result_counts": {
      "requested": 3,
      "retrieved": 3,
      "valid": 2,
      "displayed": 2
    }
  },
  "operations_performed": ["extract", "infer", "calculate", "normalize"],
  "missing_data": [
    {
      "field": "string",
      "reason": "string",
      "severity": "critical|warning|info"
    }
  ],
  "context_update": {},
  "error": null
}
```

---

## 7. Browser Instance Context

### 7.1 Storage

Use `sessionStorage` (cleared when tab/browser closes):

```typescript
const CONTEXT_KEY = 'competitor_analysis_context';
```

### 7.2 Context Structure

```json
{
  "version": 1,
  "business": {
    "name": "string",
    "industry": "string",
    "pricing": "string",
    "model": "string"
  },
  "entities": {
    "competitors": ["id1", "id2", "id3"],
    "products": ["id1", "id2"],
    "focus": "current focus entity id"
  },
  "result_meta": {
    "requested_count": 3,
    "retrieved_count": 2,
    "filters": []
  },
  "constraints": {
    "included": ["what to include"],
    "excluded": ["what to exclude"]
  },
  "keywords": ["compact", "keyword", "list"]
}
```

### 7.3 What to Store

| Category | Examples |
|----------|----------|
| Business identity | name, industry, pricing tier, model |
| Active entities | competitor IDs (max 3) |
| Result metadata | requested/retrieved counts |
| Active constraints | "exclude X", "focus on Y" |
| Current focus | Which entity is active |
| Keyword tags | 5-10 compact keywords |

### 7.4 What NOT to Store

- Full conversation history
- Complete AnalysisData payload
- Raw user messages
- Generated reports/insights text
- Chart data (regenerate on demand)
- Sensitive business data beyond session

### 7.5 Context Update Strategy

```
New User Input
       ↓
Combine with Current Context
       ↓
Resolve References
       ↓
Parse + Process
       ↓
Generate Compact Context Summary
       ↓
Overwrite (don't append) Context in sessionStorage
```

---

## 8. Reference Resolution

| Reference Pattern | Resolution |
|-------------------|------------|
| "it" / "that one" | Use `context.entities.focus` |
| "the cheaper one" | Find lowest `priceMonthly` in current data |
| "the leader" | Find competitor with `marketPosition: "Leader"` |
| "compare them" | Use last two entities mentioned |
| "remove that" | Remove from context.entities |
| "add X" | Add to context.entities (if < 3), trigger regeneration |
| "the other one" | Use non-focus entity from context |
| "same industry" | Use `context.business.industry` |

---

## 9. Dynamic Operations by Intent

### 9.1 Bootstrap (Initial Analysis)

**Trigger:** User provides business description for first time.

**Operations:**
1. Extract business facts from input
2. Infer industry, positioning, target market
3. Generate up to 3 competitors (with retry)
4. Generate products based on business type
5. Generate pricing tiers
6. Generate market gaps
7. Generate insights
8. Generate action plan
9. Generate reports
10. Generate charts from actual data
11. Generate sources
12. Generate SWOT
13. Create initial greeting conversation

### 9.2 Question (Query Existing Data)

**Trigger:** User asks about displayed data.

**Operations:**
1. Identify referenced entity from context
2. Retrieve relevant data
3. Generate text response
4. Include relevant ChatAsset
5. Update context focus

### 9.3 Refine (Modify Data)

**Trigger:** User requests changes.

**Operations:**
1. Identify what to modify
2. Apply change (add/remove/update)
3. Regenerate affected derived data
4. Update charts if competitors/metrics changed
5. Update context constraints

### 9.4 Compare

**Trigger:** User wants side-by-side comparison.

**Operations:**
1. Identify entities to compare
2. Generate comparison-table ChatAsset
3. Update context focus

### 9.5 Regenerate Section

**Trigger:** User wants fresh version of a section.

**Operations:**
1. Identify section from context
2. Regenerate that section's data
3. Preserve other sections
4. Update affected charts

---

## 10. ChatAsset Selection Guide

| User Ask | Primary Response | ChatAsset |
|----------|------------------|-----------|
| "Who are my competitors?" | Overview text | `competitor-card` for top 2-3 |
| "Compare X and Y" | Comparison summary | `comparison-table` |
| "What pricing should I use?" | Pricing strategy text | `pricing-table` |
| "Show market share" | Market overview | `chart` (marketShare) |
| "What are my strengths?" | SWOT summary | `swot` |
| "Any opportunities?" | Gap analysis | `market-gap` |
| "What should I do?" | Action summary | `action-plan` |
| "Give me a report" | Report intro | `report` |
| "Any insights?" | Key insight summary | `insight` |
| "Show everything" | Dashboard summary | `dashboard` |

---

## 11. Implementation Notes

### 11.1 The Parser is NOT a Formatter

The parser is a **semantic-to-UI data pipeline**:

```
UNDERSTAND → EXTRACT → RESOLVE → RETRIEVE → CALCULATE → TRANSFORM → NORMALIZE → VALIDATE → STRUCTURE → UPDATE CONTEXT → POPULATE UI
```

### 11.2 Frontend Responsibility

```
RECEIVE STRUCTURED STATE → RENDER COMPONENTS → ACCEPT USER INPUT → SEND INPUT BACK
```

### 11.3 Never Hardcode to Sample

The perfume/protein samples are schema references only. The parser must handle:

- Different industries (tech, food, fashion, services, etc.)
- Different business models (B2B, B2C, marketplace, SaaS, etc.)
- Different scales (startup, SMB, enterprise)
- Different information completeness (full paragraph vs. sparse notes)
- **Max 3 competitors for dynamic data**

### 11.4 Data Consistency Rules

1. Chart `series` must reference real competitor IDs
2. `marketShare` pie chart must match bar chart data
3. Chart colors must be consistent across charts for same entity
4. `actionPlan` items should reference insights/competitors mentioned
5. `sources` should cite real publications when possible
6. `pricingTiers` should align with business pricing tier
7. Statistics must reflect actual data counts, not sample counts
8. `products[].competitorId` must match an existing competitor `id`
9. `pricingTiers[].competitorId` must match an existing competitor `id`

---

## 12. Example: Parsing New User Input

### Input:
> "I'm starting a sustainable sneaker brand in Europe targeting eco-conscious millennials. My main competitors are Allbirds and Veja."

### Intermediate Representation:
```json
{
  "intent": "bootstrap",
  "entities": {
    "competitor_names": ["Allbirds", "Veja"],
    "industries": ["sustainable fashion", "footwear"],
    "geographic_mentions": ["Europe"]
  },
  "facts": {
    "product_type": "sneakers",
    "target_market": "eco-conscious millennials",
    "sustainability_focus": true
  },
  "constraints": ["sustainable", "European market", "millennial target"],
  "missing_information": ["brand name", "pricing tier", "specific features"],
  "requested_count": 3
}
```

### Operations Performed:
1. Extract: Allbirds, Veja, Europe, sustainable, sneakers, millennials
2. Infer: pricing likely "Premium" (sustainable), model "DTC"
3. Generate 3 competitors (Allbirds, Veja + 1 inferred)
4. Calculate: market shares distributed among 3 competitors
5. Generate: products, pricing, gaps, insights, charts from actual data
6. Normalize: all enum values, IDs, chart structures

### Context Stored:
```json
{
  "version": 1,
  "business": {
    "name": "Your Brand",
    "industry": "sustainable footwear",
    "pricing": "Premium",
    "model": "DTC"
  },
  "entities": {
    "competitors": ["allbirds", "veja", "rothys"],
    "focus": null
  },
  "result_meta": {
    "requested_count": 3,
    "retrieved_count": 3,
    "filters": ["sustainable", "europe"]
  },
  "constraints": {
    "included": ["sustainable", "eco-conscious"],
    "excluded": []
  },
  "keywords": ["sneakers", "sustainable", "millennials", "europe", "dtc"]
}
```

---

## 13. Acceptance Criteria

### Dynamic input
A user can enter information substantially different from the sample data.

### Dynamic result count (max 3 companies)
The UI works with 1, 2, or 3 competitors for dynamic data. Static samples remain at 4.

### No sample dependency
Sample values are never required for the application to function.

### Partial retrieval
If only some entities are retrieved, valid entities still appear.

### Partial entity
If some fields of an entity are unavailable, the entity can still appear with "—" for missing fields.

### Failure isolation
One failed retrieval does not destroy unrelated successful results.

### No hallucinated filler
Missing results are never fabricated merely to satisfy a requested count.

### Retry with backoff
Failed operations retry up to max_retries before falling back.

### Dynamic statistics
Counts and aggregates reflect actual data.

### Dynamic UI
All repeatable UI components render from arrays/collections.

### Context continuity
Follow-up requests can reference previously established information.

### Context compactness
Only useful semantic context is stored in the browser instance.

### Graceful degradation
The application remains usable when retrieval is incomplete.

---

## 14. Final Principle

The entire application must behave as a **data-driven dynamic system**, not a sample-data-driven interface.

**The UI must render what actually exists, not what the sample suggests should exist.**

- If 2 competitors are available, render 2.
- If 3 are available and permitted, render 3.
- If 0 are available, render the empty state.
- If one company has incomplete data, render that company with "—" for missing fields.
- If one retrieval fails, continue with everything else that succeeded.

The parser's job is to continuously convert **real user intent + available information + current instance context** into the **best valid UI state possible**, without relying on fixed sample values or fixed result counts.

**Max 3 competitors for dynamic data to reduce LLM cost.**
