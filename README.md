# Microservice 1: Orchestrator/Agent Controller

The main orchestrator for the competitive-analysis platform. Receives business form data from the frontend, coordinates the analysis workflow through logical agent classes, and returns structured frontend-ready outputs.

---

## API Contract

### Base URL
```
http://localhost:8001
```

### Endpoints

#### Health Check
```
GET /health
```
**Response:**
```json
{
  "status": "ok",
  "service": "orchestrator",
  "version": "1.0.0"
}
```

---

#### Analyze Business
```
POST /api/v1/analyze
Content-Type: application/json
```

**Request Body (Input):**
```json
{
  "business_name": "FreshBrew Co.",
  "idea": "Subscription-based artisanal coffee delivery service",
  "industry": "Food & Beverage / E-commerce",
  "products_services": [
    "Monthly coffee subscriptions",
    "Single-origin beans",
    "Personalized brewing guides"
  ],
  "target_customers": "Coffee enthusiasts aged 25-45, urban professionals",
  "geography": "United States, starting with NYC and LA",
  "pricing": "$29/month basic, $49/month premium",
  "business_model": "Direct-to-consumer subscription",
  "competitors": ["Blue Bottle", "Trade Coffee", "Drink Trade"],
  "differentiators": "Farm-direct sourcing, AI taste matching, zero-waste packaging",
  "research_goals": ["competitor_pricing", "market_gaps", "customer_reviews"],
  "user_query": "How can I differentiate from Blue Bottle?"
}
```

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `business_name` | string | **Yes** | Name of the business |
| `idea` | string | **Yes** | Core business idea/concept |
| `industry` | string | **Yes** | Industry or sector |
| `products_services` | string[] | No | List of products or services |
| `target_customers` | string | No | Target customer segment |
| `geography` | string | No | Geographic market |
| `pricing` | string | No | Pricing strategy/model |
| `business_model` | string | No | Business model type |
| `competitors` | string[] | No | List of competitor names |
| `differentiators` | string | No | Key differentiators |
| `research_goals` | string[] | No | What to research: `competitor_pricing`, `market_gaps`, `customer_reviews`, `competitor_research` |
| `user_query` | string | No | Optional specific question |

---

**Response Body (Output):**
```json
{
  "business_summary": "FreshBrew Co. is a direct-to-consumer subscription coffee service...",
  "profile": {
    "business_name": "FreshBrew Co.",
    "idea": "Subscription-based artisanal coffee delivery service",
    "industry": "Food & Beverage / E-commerce",
    "products_services": ["Monthly coffee subscriptions", "Single-origin beans"],
    "target_customers": "Coffee enthusiasts aged 25-45",
    "geography": "United States, starting with NYC and LA",
    "pricing": "$29/month basic, $49/month premium",
    "business_model": "Direct-to-consumer subscription",
    "competitors": ["Blue Bottle", "Trade Coffee"],
    "differentiators": "Farm-direct sourcing, AI taste matching",
    "research_goals": ["competitor_pricing", "market_gaps"],
    "user_query": "How can I differentiate from Blue Bottle?",
    "summary": "FreshBrew Co. is a direct-to-consumer subscription coffee service..."
  },
  "competitors": [
    {
      "name": "Blue Bottle",
      "description": "Premium coffee roaster known for single-origin beans",
      "strengths": ["Strong brand recognition", "High-quality reputation"],
      "weaknesses": ["Higher price point", "Limited personalization"],
      "pricing": "$18-24 per bag, subscriptions from $35/month",
      "market_position": "Premium/luxury coffee segment",
      "source": "https://bluebottlecoffee.com",
      "explanation": "Blue Bottle is your primary benchmark for premium positioning..."
    }
  ],
  "swot": {
    "strengths": [
      {
        "point": "AI-powered taste personalization",
        "explanation": "No major competitor offers personalized taste matching at scale",
        "source": "competitive_analysis"
      }
    ],
    "weaknesses": [
      {
        "point": "New brand with no recognition",
        "explanation": "Competing against established players requires marketing investment",
        "source": "internal_assessment"
      }
    ],
    "opportunities": [
      {
        "point": "Underserved market for personalized subscriptions",
        "explanation": "No dominant player combines personalization with convenience",
        "source": "market_gap_analysis"
      }
    ],
    "threats": [
      {
        "point": "Well-funded competitors could replicate features",
        "explanation": "Trade Coffee has raised significant VC funding",
        "source": "competitor_research"
      }
    ]
  },
  "comparisons": [
    {
      "title": "Subscription Pricing Comparison",
      "entities": ["FreshBrew", "Blue Bottle", "Trade Coffee"],
      "rows": [
        {
          "feature": "Basic Plan",
          "values": {
            "FreshBrew": "$29/month",
            "Blue Bottle": "$35/month",
            "Trade Coffee": "$25/month"
          }
        },
        {
          "feature": "Premium Plan",
          "values": {
            "FreshBrew": "$49/month",
            "Blue Bottle": "$55/month",
            "Trade Coffee": "$40/month"
          }
        }
      ],
      "explanation": "FreshBrew is competitively priced while offering unique personalization"
    }
  ],
  "charts": [
    {
      "chart_type": "bar",
      "title": "Monthly Subscription Pricing Comparison",
      "labels": ["FreshBrew Basic", "FreshBrew Premium", "Trade Basic", "Trade Premium"],
      "datasets": [
        {
          "label": "Monthly Price ($)",
          "data": [29, 49, 25, 40]
        }
      ],
      "explanation": "FreshBrew sits in the competitive mid-range"
    },
    {
      "chart_type": "radar",
      "title": "Competitive Positioning",
      "labels": ["Price", "Quality", "Personalization", "Sustainability", "Convenience"],
      "datasets": [
        { "label": "FreshBrew", "data": [7, 8, 10, 9, 8] },
        { "label": "Blue Bottle", "data": [5, 10, 3, 6, 5] }
      ],
      "explanation": "FreshBrew leads in personalization and sustainability"
    }
  ],
  "insights": [
    {
      "title": "Pricing Sweet Spot Identified",
      "description": "Market data shows $29-49/month is optimal for premium coffee subscriptions",
      "importance": "high",
      "source": "pricing_research",
      "explanation": "Your planned pricing aligns with market expectations"
    }
  ],
  "recommendations": [
    {
      "title": "Launch with $29/$49 pricing tiers",
      "description": "Your planned pricing is competitive and validates against market data",
      "priority": "high",
      "rationale": "Market research confirms this range captures value-conscious premium buyers",
      "explanation": "Starting at $29 makes you accessible while $49 captures premium customers"
    }
  ],
  "action_plan": [
    {
      "action": "Finalize taste profile algorithm and quiz UX",
      "timeline": "Week 1-4",
      "priority": "high",
      "expected_outcome": "Working personalization engine with 85%+ satisfaction rate",
      "explanation": "This is your core differentiator - getting it right is critical"
    }
  ],
  "report": "# Competitive Analysis Report: FreshBrew Co.\n\n## Executive Summary\n...",
  "sources": [
    {
      "source": "https://bluebottlecoffee.com",
      "type": "web",
      "relevance": "Competitor data: Blue Bottle Coffee"
    }
  ],
  "metadata": {
    "generated_at": "2026-09-05T10:30:00",
    "model_used": "llm-brain",
    "confidence": 0.85,
    "processing_time_ms": 12500
  }
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `business_summary` | string | Clean human-readable summary of the business |
| `profile` | object | Validated structured business profile (same as input + summary) |
| `competitors` | array | Competitor cards with name, strengths, weaknesses, pricing, explanation |
| `swot` | object | SWOT analysis with strengths, weaknesses, opportunities, threats (each with point, explanation, source) |
| `comparisons` | array | Comparison tables with entities, rows, values |
| `charts` | array | Chart data (chart_type, title, labels, datasets) for frontend rendering |
| `insights` | array | Key insights with title, description, importance, source, explanation |
| `recommendations` | array | Prioritized recommendations with title, description, priority, rationale, explanation |
| `action_plan` | array | Timeline of actions with action, timeline, priority, expected_outcome, explanation |
| `report` | string | Full text markdown report |
| `sources` | array | Evidence references with source URL, type, relevance |
| `metadata` | object | generated_at, model_used, confidence, processing_time_ms |

---

#### Async Placeholder
```
POST /api/v1/analyze/async
```
Returns `202 Accepted` with job ID (not yet implemented).

---

## Quick Start

### Prerequisites
- Python 3.11+
- LLM Brain service URL (deployed separately)

### Run with uv
```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

### Run with Docker
```bash
docker build -t competitor-orchestrator .
docker run -d -p 8001:8001 --env-file .env competitor-orchestrator
```

---

## Checking LLM Connection

To verify the LLM service is reachable before running full analysis:

### Using curl
```bash
# Test if LLM endpoint responds (replace with your LLM_SERVICE_URL)
curl -X POST $LLM_SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello, respond with OK"}'
```

### Using the orchestrator logs
```bash
# Start the service and check logs for LLM connection status
uvicorn app.main:app --reload --port 8001
# Look for: "llm_response_received" (success) or "llm_connection_error" (failure)
```

### Common issues
- **Connection refused**: LLM service not running or wrong URL
- **Timeout**: Increase `LLM_TIMEOUT` in `.env`
- **401/403**: Authentication required (add headers to LLMClient if needed)

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_SERVICE_URL` | `http://localhost:8000/chat` | LLM Brain endpoint |
| `LLM_TIMEOUT` | `60` | Timeout in seconds |
| `LLM_MAX_RETRIES` | `3` | Max retry attempts |
| `SCRAPER_SERVICE_URL` | `http://localhost:8001` | Scraper service URL |
| `USE_MOCK_SCRAPER` | `true` | Use mock scraper data |
| `SERVICE_PORT` | `8001` | Service port |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ALLOWED_ORIGINS` | `http://localhost:3000,...` | CORS origins |

---

## Architecture

```
Client (Frontend)
       │
       ▼
┌─────────────────────────────────────────────────┐
│              Microservice 1 (Orchestrator)        │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐│
│  │  Business    │  │  Research    │  │  Data    ││
│  │  Parser      │→ │  Planner     │→ │  Summary ││
│  │  Agent       │  │  Agent       │  │  Agent   ││
│  └─────────────┘  └──────────────┘  └──────────┘│
│         │                                  │      │
│         ▼                                  ▼      │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐│
│  │ Competitor  │  │  Strategy    │  │  Report  ││
│  │ Analysis    │→ │  Agent       │→ │  Agent   ││
│  │ Agent       │  │  (SWOT, etc) │  │          ││
│  └─────────────┘  └──────────────┘  └──────────┘│
│                                                   │
│  ┌──────────────────────────────────────────────┐│
│  │  External Services                            ││
│  │  • LLM Brain (HTTP) — language reasoning      ││
│  │  • Scraper Provider (interface) — web data    ││
│  └──────────────────────────────────────────────┘│
└─────────────────────────────────────────────────┘
```

---

## Agent Classes

| Agent | Purpose |
|-------|---------|
| `BusinessParserAgent` | Step 1+2: Form → Summary → Structured Profile |
| `ResearchPlannerAgent` | Creates execution plan from user goals |
| `DataSummaryAgent` | Summarizes raw scraped data |
| `CompetitorAnalysisAgent` | Deep competitor analysis |
| `VisualizationAgent` | Generates chart/table specs |
| `StrategyAgent` | SWOT, recommendations, action plans |
| `ReportAgent` | Compiles final output |

---

## Testing

```bash
pytest tests/ -v
```

---

## Integration Notes

### Connecting to LLM Brain
The service sends `{"query": "..."}` to `LLM_SERVICE_URL`. The LLM Brain handles all language reasoning.

### Connecting to Scraper (Microservice 3)
Set `USE_MOCK_SCRAPER=false` and `SCRAPER_SERVICE_URL` when the scraper is built. The `HttpScraperProvider` will be used automatically.

### Frontend Integration
1. Collect form data from user
2. Send `POST /api/v1/analyze` with the JSON body shown above
3. Render the response:
   - `competitors` → Competitor cards
   - `swot` → SWOT grid
   - `charts` → Use with Chart.js/Recharts (bar, radar, pie supported)
   - `comparisons` → Data tables
   - `recommendations` → Cards with priority badges
   - `action_plan` → Timeline/list view
   - `report` → Markdown display
4. Use `explanation` fields for "Explain This" tooltips
5. Display `sources` as citations/evidence
