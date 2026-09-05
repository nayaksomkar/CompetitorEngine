import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers.chat import router as chat_router
from app.routers.overview import router as overview_router

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="CompetitorEngine",
    description=(
        "Pure orchestrator. Delegates research to WebHunter and "
        "reasoning to LLMPing via HTTP."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — hardcoded allowed origins (Render env override workaround).
ALLOWED_CORS_ORIGINS = [
    "https://nayaksomkar.github.io",
    "http://localhost:5173",
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Routers
app.include_router(overview_router)
app.include_router(chat_router)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "orchestrator", "version": "2.0.0"}


@app.get("/", tags=["root"])
async def root():
    return {
        "service": "CompetitorEngine",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "overview": "POST /api/v1/analyze",
            "chat": "POST /api/v1/chat",
        },
        "upstream": {
            "llmping": settings.llmping_url,
            "webhunter": settings.webhunter_url,
        },
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    service_config = settings.get_service_config()
    uvicorn.run(
        "app.main:app",
        host=service_config.get("host", settings.service_host),
        port=service_config.get("port", settings.service_port),
        reload=True,
        log_level=service_config.get("log_level", settings.log_level).lower(),
    )
