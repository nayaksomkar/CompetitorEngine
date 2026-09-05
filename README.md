# Competitive Analysis Orchestrator

This is the orchestrator service that forwards requests to **LLM providers** for AI analysis. It takes business information from the frontend, distributes work across multiple AI agents (each with its own provider), and returns structured results.

---

## Architecture

```
Frontend → Orchestrator (this service) → LLM Providers (with fallback chain)
                                        ↓
                              ┌─────────────────────┐
                              │  Business Parser    │ → google_genai (fallback: mistral)
                              │  Research Planner   │ → google_genai (fallback: groq)
                              │  Competitor Analysis│ → mistral (fallback: google_genai)
                              │  Strategy Agent     │ → groq (fallback: cerebras)
                              │  Visualization      │ → google_genai (fallback: mistral)
                              │  Report Agent       │ → cerebras (fallback: groq)
                              └─────────────────────┘
```

---

## How It Works

1. User fills a form in the frontend with their business details
2. Frontend sends the data to this orchestrator service
3. Orchestrator distributes work to agents, each using its configured LLM provider
4. If a provider fails, automatic fallback to the next provider in the chain
5. Service returns structured results (charts, tables, recommendations)

---

## Configuration (config.json)

All providers, models, and fallbacks are configured in `config.json`:

```json
{
  "llm": {
    "providers": [
      {
        "name": "google_genai",
        "url": "http://localhost:8000/chat",
        "model": "gemini-2.5-flash",
        "timeout": 30,
        "max_retries": 2,
        "enabled": true
      }
    ],
    "fallback_chain": ["google_genai", "mistral", "groq", "cerebras"],
    "default_provider": "google_genai"
  },
  "agents": {
    "business_parser": {
      "provider": "google_genai",
      "model": "gemini-2.5-flash",
      "fallback": "mistral"
    }
  }
}
```

### Provider Configuration

| Field | Description |
|-------|-------------|
| `name` | Provider identifier |
| `url` | LLM endpoint URL (accepts `{"query": "..."}` format) |
| `model` | Model name to use |
| `timeout` | Request timeout in seconds |
| `max_retries` | Retries before fallback |
| `enabled` | Whether provider is active |

### Agent Configuration

Each agent has its own provider and fallback:

| Agent | Default Provider | Fallback |
|-------|-----------------|----------|
| `business_parser` | google_genai | mistral |
| `research_planner` | google_genai | groq |
| `competitor_analysis` | mistral | google_genai |
| `strategy` | groq | cerebras |
| `visualization` | google_genai | mistral |
| `report` | cerebras | groq |

### Fallback Chain

When a provider fails, the system automatically tries the next provider:
1. Try agent's primary provider
2. Try agent's specific fallback
3. Try global fallback chain
4. Return error if all fail

---

## LLM Provider API Format

All providers accept requests in this format:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is Python?"}'
```

---

## Orchestrator API Endpoints

### Health Check
```
GET /health
```

### Analyze Business
```
POST /api/v1/analyze
```

**Required fields:** `business_name`, `idea`, `industry` (rest are optional)

**You get back:**
- `competitors` — List of competitors with strengths/weaknesses
- `swot` — SWOT analysis (strengths, weaknesses, opportunities, threats)
- `charts` — Data for rendering charts (bar, radar, pie)
- `comparisons` — Comparison tables
- `insights` — Key findings
- `recommendations` — What you should do
- `action_plan` — Step-by-step actions with timeline
- `report` — Full text report
- `sources` — Where the data came from

Every item includes an `explanation` field — use this for "Explain This" tooltips in the UI.

---

## For UI Team

### How to Send Requests

**Base URL:** `http://localhost:8001` (orchestrator) or your deployed URL

**Endpoint:** `POST /api/v1/analyze`

**Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "business_name": "MyStartup",
  "idea": "AI project management tool",
  "industry": "SaaS",
  "products_services": ["Dashboard", "API"],
  "target_customers": "SMBs",
  "geography": "US",
  "pricing": "$49/month",
  "business_model": "Subscription",
  "competitors": ["Asana", "Monday.com"],
  "differentiators": "AI-first approach",
  "research_goals": ["competitor_research", "pricing_research"],
  "user_query": "How to compete?"
}
```

**Example with fetch:**
```javascript
const response = await fetch('http://localhost:8001/api/v1/analyze', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    business_name: "MyStartup",
    idea: "AI project management tool",
    industry: "SaaS",
    competitors: ["Asana", "Monday.com"]
  })
});

const data = await response.json();
```

**Example with curl:**
```bash
curl -X POST http://localhost:8001/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "business_name": "MyStartup",
    "idea": "AI project management tool",
    "industry": "SaaS",
    "competitors": ["Asana", "Monday.com"]
  }'
```

---

### Response Format

```json
{
  "business_summary": "Brief summary of the business",
  "profile": { "business_name": "MyStartup", "idea": "..." },
  "competitors": [
    {
      "name": "CompA",
      "description": "What they do",
      "strengths": ["Strong brand"],
      "weaknesses": ["High price"],
      "pricing": "$59/month",
      "market_position": "Leader",
      "explanation": "Why this matters"
    }
  ],
  "swot": {
    "strengths": [{ "point": "...", "explanation": "...", "source": "..." }],
    "weaknesses": [{ "point": "...", "explanation": "...", "source": "..." }],
    "opportunities": [{ "point": "...", "explanation": "...", "source": "..." }],
    "threats": [{ "point": "...", "explanation": "...", "source": "..." }]
  },
  "charts": [
    {
      "chart_type": "bar",
      "title": "Pricing Comparison",
      "labels": ["MyStartup", "CompA"],
      "datasets": [{ "label": "Monthly Price", "data": [49, 59] }],
      "explanation": "Shows price advantage"
    }
  ],
  "comparisons": [
    {
      "title": "Feature Comparison",
      "entities": ["MyStartup", "CompA"],
      "rows": [{ "feature": "Starting Price", "values": {"MyStartup": "$49", "CompA": "$59"} }],
      "explanation": "Key differentiator"
    }
  ],
  "insights": [
    {
      "title": "Key Finding",
      "description": "Details here",
      "importance": "high",
      "source": "competitor_research",
      "explanation": "Why this matters"
    }
  ],
  "recommendations": [
    {
      "title": "Do this",
      "description": "Specific action",
      "priority": "high",
      "rationale": "Why do this",
      "explanation": "Additional context"
    }
  ],
  "action_plan": [
    {
      "action": "Launch campaign",
      "timeline": "Week 1-2",
      "priority": "high",
      "expected_outcome": "More users",
      "explanation": "Why this timing"
    }
  ],
  "report": "# Full Report\n\nMarkdown formatted report...",
  "sources": [
    { "source": "https://example.com", "type": "web", "relevance": "Why cited" }
  ],
  "metadata": {
    "generated_at": "2025-01-15T10:30:00",
    "confidence": 0.85,
    "processing_time_ms": 4500
  }
}
```

---

### How to Connect
1. Send `POST` request to `/api/v1/analyze` with business data
2. Display the response using the field names provided
3. Use `explanation` fields for tooltips
4. Show `sources` as clickable links

### Rendering Charts
The `charts` array contains:
- `chart_type`: "bar", "radar", or "pie"
- `labels`: Labels for the chart
- `datasets`: Data values

Use with Chart.js or Recharts.

---

## Run Locally

```bash
# Setup
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Run
uvicorn app.main:app --reload --port 8001
```

---

## Run with Docker

```bash
docker build -t competitor-orchestrator .
docker run -d -p 8001:8001 --env-file .env competitor-orchestrator
```

---

## Environment Variables

Create a `.env` file:
```
SERVICE_PORT=8001
```

---

## CORS (Frontend Access)

Allowed origins (configured in config.json):
- `http://localhost:5173`
- `http://localhost:3000`

To add more, edit `config.json` → `cors.allowed_origins`.

---

## Testing

```bash
pytest tests/ -v
```
