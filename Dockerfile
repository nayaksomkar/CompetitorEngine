FROM python:3.11-slim

LABEL org.opencontainers.image.title="CompetitorEngine" \
      org.opencontainers.image.description="Competitor analysis orchestrator (WebHunter + LLMPing)" \
      org.opencontainers.image.source="https://github.com/your-org/CompetitorEngine" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8001}/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
