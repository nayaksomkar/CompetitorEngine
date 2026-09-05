import pytest

from app.agents.web_search_agent import WebSearchAgent


class TestWebSearchAgent:
    """Tests for the WebSearchAgent keyword extraction and entity detection."""

    def setup_method(self):
        # Use a fake LLM client since we only test local methods
        class FakeLLM:
            async def send_query(self, prompt):
                return "Summary"

            async def send_prompt_with_context(self, sys, user):
                return "Summary"

        self.agent = WebSearchAgent(FakeLLM())

    def test_extract_keywords_basic(self):
        """Should include the term itself."""
        keywords = self.agent._extract_keywords("Fragnote", "")
        assert "Fragnote" in keywords

    def test_extract_keywords_with_context(self):
        """Should extract meaningful words from context, filtering stop words."""
        keywords = self.agent._extract_keywords(
            "Fragnote",
            "I want to analyze this competitive note-taking app for SMBs",
        )
        # Should include the term
        assert "Fragnote" in keywords
        # Should include meaningful words from context
        assert any(k in keywords for k in ["note-taking", "SMBs", "competitive", "analyze"])
        # Should NOT include stop words
        assert "the" not in [k.lower() for k in keywords]
        assert "for" not in [k.lower() for k in keywords]

    def test_extract_keywords_dedup(self):
        """Should deduplicate keywords case-insensitively."""
        keywords = self.agent._extract_keywords("Fragnote", "fragnote is great")
        fragnote_count = sum(1 for k in keywords if k.lower() == "fragnote")
        assert fragnote_count == 1

    def test_extract_keywords_limit(self):
        """Should limit to 5 keywords."""
        keywords = self.agent._extract_keywords(
            "TestApp",
            "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        )
        assert len(keywords) <= 5

    def test_extract_keywords_cleaned_term(self):
        """Should add cleaned term without special chars."""
        keywords = self.agent._extract_keywords("Test-App!", "")
        assert "Test-App!" in keywords
        assert "TestApp" in keywords

    def test_detect_type_mobile_app(self):
        """Should detect mobile apps from search results."""
        results = [{"title": "App Store", "snippet": "Download this iOS app"}]
        assert self.agent._detect_type(results) == "mobile_app"

    def test_detect_type_software(self):
        """Should detect software platforms."""
        results = [{"snippet": "A SaaS platform for businesses"}]
        assert self.agent._detect_type(results) == "software"

    def test_detect_type_company(self):
        """Should detect companies."""
        results = [{"snippet": "Acme Corp Inc is a leading provider"}]
        assert self.agent._detect_type(results) == "company"

    def test_detect_type_unknown(self):
        """Should default to entity for ambiguous results."""
        results = [{"snippet": "Something mysterious"}]
        assert self.agent._detect_type(results) == "entity"

    def test_assess_relevance_high(self):
        """Should return high when term appears in context."""
        results = [{"snippet": "Some info"}]
        relevance = self.agent._assess_relevance("Fragnote", "I use Fragnote daily", results)
        assert relevance == "high"

    def test_assess_relevance_unknown_no_context(self):
        """Should return unknown with no context."""
        results = [{"snippet": "Some info"}]
        relevance = self.agent._assess_relevance("Fragnote", "", results)
        assert relevance == "unknown"

    def test_assess_relevance_medium(self):
        """Should return medium when results have industry keywords."""
        results = [{"snippet": "A product for business market and customer pricing"}]
        relevance = self.agent._assess_relevance("Fragnote", "random context", results)
        assert relevance in ("medium", "high")

    def test_assess_relevance_none(self):
        """Should return none when no relevant keywords."""
        results = [{"snippet": "Random unrelated content"}]
        relevance = self.agent._assess_relevance("Fragnote", "xyz abc", results)
        assert relevance == "none"
