# Competitive Analysis Orchestrator

This is the orchestrator service that forwards requests to the **LLM Ping** service. It takes business information from the frontend, sends it to LLM Ping for AI analysis, and returns structured results.

---

## Architecture

```
Frontend → Orchestrator (this service) → LLM Ping → AI Analysis → Response
```

The orchestrator acts as a middleware that:
1. Receives business data from frontend
2. Forwards it to LLM Ping service
3. Structures the AI response into charts, tables, recommendations

---

## How It Works

1. User fills a form in the frontend with their business details
2. Frontend sends the data to this orchestrator service
3. Orchestrator forwards to LLM Ping service for AI analysis
4. Service returns structured results (charts, tables, recommendations)

---

## LLM Ping Service (AI Backend)

This is the actual AI service that processes requests.

**Endpoint:** `POST http://localhost:8000/chat`

**Headers:**
```
Content-Type: application/json
```

**Request Format:**
```json
{
  "query": "What is Python?"
}
```

**Example curl:**
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

Allowed origins:
- `http://localhost:5173`

To add more, edit `app/main.py` → `allow_origins` list.

---

## Testing

```bash
pytest tests/ -v
```
