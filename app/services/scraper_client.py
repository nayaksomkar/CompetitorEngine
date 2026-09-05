import structlog
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = structlog.get_logger(__name__)


class ScraperProvider(ABC):
    """Abstract interface for web scraping provider."""

    @abstractmethod
    async def fetch(self, research_type: str, query_params: dict) -> dict:
        """Fetch data from the web based on research type and params."""
        ...


class MockScraperProvider(ScraperProvider):
    """Returns realistic mock data for development and testing."""

    async def fetch(self, research_type: str, query_params: dict) -> dict:
        log = logger.bind(research_type=research_type, provider="mock")
        log.info("mock_scraper_fetch")

        business_name = query_params.get("business_name", "Unknown")
        competitors = query_params.get("competitors", [])
        industry = query_params.get("industry", "General")

        if research_type == "competitor_research":
            return self._mock_competitor_data(competitors, industry)
        elif research_type == "pricing_research":
            return self._mock_pricing_data(competitors, industry)
        elif research_type == "customer_reviews":
            return self._mock_review_data(competitors)
        elif research_type == "market_gap":
            return self._mock_market_gap_data(query_params)
        else:
            return self._mock_generic_data(research_type, query_params)

    def _mock_competitor_data(self, competitors: list[str], industry: str) -> dict:
        competitor_list = competitors if competitors else ["CompetitorA", "CompetitorB"]
        return {
            "research_type": "competitor_research",
            "industry": industry,
            "competitors": [
                {
                    "name": comp,
                    "description": f"Established player in {industry} market",
                    "estimated_market_share": f"{(i + 1) * 15}%",
                    "pricing_range": ["$19/month", "$49/month", "$99/month"][i % 3],
                    "key_features": ["Feature A", "Feature B", "Feature C"],
                    "strengths": ["Brand recognition", "Large user base", "Funding"],
                    "weaknesses": ["Slow innovation", "High pricing", "Poor support"],
                    "source": f"mock://search/{comp.lower().replace(' ', '-')}",
                }
                for i, comp in enumerate(competitor_list)
            ],
            "source": "mock_search_engine",
        }

    def _mock_pricing_data(self, competitors: list[str], industry: str) -> dict:
        competitor_list = competitors if competitors else ["CompetitorA", "CompetitorB"]
        return {
            "research_type": "pricing_research",
            "industry": industry,
            "pricing_data": [
                {
                    "competitor": comp,
                    "plans": [
                        {"name": "Basic", "price": f"${10 + i * 5}/month", "features": ["Core features"]},
                        {"name": "Pro", "price": f"${30 + i * 10}/month", "features": ["All features", "Priority support"]},
                    ],
                    "source": f"mock://pricing/{comp.lower().replace(' ', '-')}",
                }
                for i, comp in enumerate(competitor_list)
            ],
            "market_average": "$35/month",
            "source": "mock_pricing_aggregation",
        }

    def _mock_review_data(self, competitors: list[str]) -> dict:
        competitor_list = competitors if competitors else ["CompetitorA", "CompetitorB"]
        return {
            "research_type": "customer_reviews",
            "reviews": [
                {
                    "competitor": comp,
                    "average_rating": round(3.5 + (i * 0.3), 1),
                    "total_reviews": 100 + i * 50,
                    "positive_themes": ["Easy to use", "Good value", "Reliable"],
                    "negative_themes": ["Limited features", "Slow support", "Buggy"],
                    "source": f"mock://reviews/{comp.lower().replace(' ', '-')}",
                }
                for i, comp in enumerate(competitor_list)
            ],
        }

    def _mock_market_gap_data(self, query_params: dict) -> dict:
        return {
            "research_type": "market_gap",
            "gaps_identified": [
                {
                    "gap": "No competitor offers personalized onboarding",
                    "opportunity_size": "medium",
                    "evidence": "Customer reviews mention confusion during setup",
                },
                {
                    "gap": "Pricing transparency is poor across the industry",
                    "opportunity_size": "high",
                    "evidence": "Multiple review complaints about hidden fees",
                },
            ],
            "underserved_segments": ["Small teams", "Non-technical users", "International markets"],
            "source": "mock_market_analysis",
        }

    def _mock_generic_data(self, research_type: str, query_params: dict) -> dict:
        return {
            "research_type": research_type,
            "query_params": query_params,
            "data": f"Mock data for {research_type}",
            "source": "mock_generic",
        }


class HttpScraperProvider(ScraperProvider):
    """Real HTTP client for Microservice 3 (to be used when scraper is built)."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.scraper_service_url
        self.timeout = 30

    async def fetch(self, research_type: str, query_params: dict) -> dict:
        log = logger.bind(research_type=research_type, provider="http")
        log.info("http_scraper_fetch")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/scrape",
                    json={"research_type": research_type, "params": query_params},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            log.error("scraper_http_error", error=str(e))
            raise


def get_scraper_provider() -> ScraperProvider:
    """Factory to return the appropriate scraper provider based on config."""
    if settings.use_mock_scraper:
        return MockScraperProvider()
    return HttpScraperProvider()
