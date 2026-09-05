# Competitive Analysis Orchestrator

This is the main backend service for the competitive analysis platform. It takes business information from the frontend, runs analysis using AI, and returns structured results.

---

## How It Works

1. User fills a form in the frontend with their business details
2. Frontend sends the data to this service
3. Service analyzes competitors, pricing, market gaps using AI
4. Service returns structured results (charts, tables, recommendations)

---

## API Endpoints

### Health Check
```
GET /health
```

### Analyze Business
```
POST /api/v1/analyze
```

**Send this JSON:**
```json
{
  "business_name": "Your Company",
  "idea": "What your business does",
  "industry": "Your industry",
  "products_services": ["Product 1", "Product 2"],
  "target_customers": "Who you serve",
  "geography": "Where you operate",
  "pricing": "Your pricing",
  "business_model": "How you make money",
  "competitors": ["Competitor 1", "Competitor 2"],
  "differentiators": "What makes you different",
  "research_goals": ["competitor_pricing", "market_gaps"],
  "user_query": "Any specific question?"
}
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
LLM_SERVICE_URL=https://llmping.onrender.com
SERVICE_PORT=8001
```

---

## CORS (Frontend Access)

Allowed origins:
- `https://nayaksomkar.github.io`
- `http://localhost:5173`

To add more, edit `app/main.py` → `allow_origins` list.

---

## For UI Team

### How to Connect
1. Send `POST` request to `/api/v1/analyze` with the JSON shown above
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

## Testing

```bash
pytest tests/ -v
```
