import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult


@pytest.fixture
def sample_form_data():
    return {
        "business_name": "TestCo",
        "idea": "AI analytics platform",
        "industry": "SaaS",
        "products_services": ["Dashboard"],
        "target_customers": "SMBs",
        "geography": "US",
        "pricing": "$49/month",
        "business_model": "Subscription",
        "competitors": ["CompA"],
        "differentiators": "AI-first",
        "research_goals": ["competitor_research"],
        "user_query": "How to compete?",
    }


@pytest.fixture
def mock_analysis_result():
    return AnalysisResult(
        business_summary="Test summary",
        report="Test report",
    )


@pytest.mark.asyncio
async def test_health_check():
    """Test health endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_root_endpoint():
    """Test root endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "service" in response.json()


@pytest.mark.asyncio
async def test_analyze_endpoint_validation_error():
    """Test validation error on missing required fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/analyze", json={})
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_analyze_endpoint_success(sample_form_data, mock_analysis_result):
    """Test successful analysis endpoint call."""
    with patch("app.routers.analysis.Orchestrator") as MockOrchestrator:
        mock_instance = AsyncMock()
        mock_instance.run_analysis.return_value = mock_analysis_result
        MockOrchestrator.return_value = mock_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/analyze", json=sample_form_data)

    assert response.status_code == 200
    data = response.json()
    assert data["report"] == "Test report"


@pytest.mark.asyncio
async def test_analyze_endpoint_server_error(sample_form_data):
    """Test 500 error handling."""
    with patch("app.routers.analysis.Orchestrator") as MockOrchestrator:
        mock_instance = AsyncMock()
        mock_instance.run_analysis.side_effect = Exception("LLM service down")
        MockOrchestrator.return_value = mock_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/analyze", json=sample_form_data)

    assert response.status_code == 500
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_async_endpoint(sample_form_data):
    """Test async placeholder endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/analyze/async", json=sample_form_data)
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
