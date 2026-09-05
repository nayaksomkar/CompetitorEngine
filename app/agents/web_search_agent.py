import re

import structlog

from app.agents.base import BaseAgent

logger = structlog.get_logger(__name__)


class WebSearchAgent(BaseAgent):
    """
    Handles unknown or ambiguous terms by performing web search.
    Triggered when the LLM returns low-confidence or unrecognized results.
    Extracts keywords from the query + context, searches the web, and returns
    structured information about the term.
    """

    SYSTEM_PROMPT = """You are a research assistant that helps identify unknown terms
or products mentioned by users. Given a term and context, provide a concise summary
based on the web search results.

Rules:
- Base your answer ONLY on the provided search results
- Keep it brief (2-3 sentences)
- Include the type of product/service
- Note any relevance to competitive analysis if applicable
- Output as plain text, not JSON"""

    async def research_unknown_term(
        self,
        term: str,
        context: str = "",
    ) -> dict:
        """
        Research an unknown term using web search.

        Args:
            term: The unknown/unrecognized term to research
            context: Additional context from chat history or business profile

        Returns:
            dict with research results including summary, type, and relevance
        """
        log = logger.bind(term=term)
        log.info("researching_unknown_term")

        # Extract keywords from term and context
        keywords = self._extract_keywords(term, context)

        # Perform web search
        search_results = await self._web_search(keywords)

        if not search_results:
            return {
                "term": term,
                "found": False,
                "summary": f"Could not find information about '{term}'",
                "type": "unknown",
                "relevance": "none",
                "keywords": keywords,
            }

        # Summarize results using LLM
        summary = await self._summarize_results(term, search_results)

        return {
            "term": term,
            "found": True,
            "summary": summary,
            "type": self._detect_type(search_results),
            "relevance": self._assess_relevance(term, context, search_results),
            "keywords": keywords,
            "sources": [r.get("url", "") for r in search_results[:3]],
        }

    def _extract_keywords(self, term: str, context: str) -> list[str]:
        """Extract search keywords from term and context."""
        keywords = [term.strip()]

        # Add cleaned term without special chars
        cleaned = re.sub(r"[^\w\s]", "", term).strip()
        if cleaned and cleaned != term:
            keywords.append(cleaned)

        # Extract meaningful words from context
        if context:
            # Remove common stop words
            stop_words = {
                "the", "a", "an", "is", "are", "was", "were", "be", "been",
                "being", "have", "has", "had", "do", "does", "did", "will",
                "would", "could", "should", "may", "might", "must", "shall",
                "can", "need", "dare", "ought", "used", "to", "of", "in",
                "for", "on", "with", "at", "by", "from", "as", "into",
                "through", "during", "before", "after", "above", "below",
                "between", "out", "off", "over", "under", "again", "further",
                "then", "once", "here", "there", "when", "where", "why",
                "how", "all", "each", "every", "both", "few", "more", "most",
                "other", "some", "such", "no", "nor", "not", "only", "own",
                "same", "so", "than", "too", "very", "and", "but", "or",
                "if", "while", "that", "this", "what", "which", "who", "whom",
            }
            words = re.findall(r"\b[A-Za-z]{3,}\b", context.lower())
            meaningful = [w for w in words if w not in stop_words][:5]
            keywords.extend(meaningful)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for kw in keywords:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                unique.append(kw)

        return unique[:5]

    async def _web_search(self, keywords: list[str]) -> list[dict]:
        """
        Perform web search using the scraper service or HTTP fallback.
        Returns list of search result dicts with title, snippet, url.
        """
        query = " ".join(keywords[:3])  # Use top 3 keywords

        try:
            # Try using the scraper service first
            from app.services.scraper_client import get_scraper_provider

            scraper = get_scraper_provider()
            result = await scraper.fetch(
                "web_search",
                {"query": query, "keywords": keywords},
            )
            results = result.get("results", [])
            if results:
                return results
        except Exception as e:
            logger.warning("scraper_search_failed", error=str(e))

        # Fallback: use DuckDuckGo instant answer API
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": query,
                        "format": "json",
                        "no_html": "1",
                        "skip_disambig": "1",
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    if data.get("AbstractText"):
                        results.append({
                            "title": data.get("Heading", query),
                            "snippet": data["AbstractText"],
                            "url": data.get("AbstractURL", ""),
                        })
                    for topic in data.get("RelatedTopics", [])[:5]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            results.append({
                                "title": topic.get("Text", "")[:80],
                                "snippet": topic["Text"],
                                "url": topic.get("FirstURL", ""),
                            })
                    return results
        except Exception as e:
            logger.warning("web_search_failed", error=str(e))

        return []

    async def _summarize_results(self, term: str, results: list[dict]) -> str:
        """Summarize web search results using LLM."""
        if not results:
            return f"No information found about '{term}'"

        # Build context from results
        results_text = "\n\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')}"
            for r in results[:5]
        )

        prompt = self._format_prompt(
            self.SYSTEM_PROMPT,
            f"Term: {term}\n\nSearch Results:\n{results_text}",
        )

        try:
            summary = await self._query_llm(prompt)
            return summary.strip()
        except Exception:
            # Fallback: return first snippet
            return results[0].get("snippet", f"Found information about '{term}'")

    def _detect_type(self, results: list[dict]) -> str:
        """Detect the type of entity from search results."""
        text = " ".join(
            r.get("snippet", "") + " " + r.get("title", "")
            for r in results
        ).lower()

        if any(kw in text for kw in ["app", "ios", "android", "download"]):
            return "mobile_app"
        if any(kw in text for kw in ["saas", "platform", "software", "api"]):
            return "software"
        if any(kw in text for kw in ["company", "corp", "inc", "ltd"]):
            return "company"
        if any(kw in text for kw in ["product", "tool", "service"]):
            return "product"
        return "entity"

    def _assess_relevance(
        self, term: str, context: str, results: list[dict]
    ) -> str:
        """Assess relevance to competitive analysis context."""
        if not context:
            return "unknown"

        context_lower = context.lower()
        term_lower = term.lower()

        # Check if term appears in context
        if term_lower in context_lower:
            return "high"

        # Check for industry keywords overlap
        industry_keywords = [
            "competitor", "market", "business", "industry", "product",
            "service", "customer", "pricing", "strategy",
        ]
        text = " ".join(r.get("snippet", "") for r in results).lower()

        matches = sum(1 for kw in industry_keywords if kw in text)
        if matches >= 2:
            return "medium"
        if matches >= 1:
            return "low"

        return "none"
