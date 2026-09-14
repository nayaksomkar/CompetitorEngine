import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.orchestrator import get_resolved_upstreams
from app.routers.chat import router as chat_router
from app.routers.overview import router as overview_router
from app.routers.parser import router as parser_router
from app.services.llmping_client import LLMPingClient
from app.services.webhunter_client import WebHunterClient

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: resolve sibling service URLs via discovery.

    Discovery is non-blocking on the request path — clients will also
    resolve lazily on first call if startup resolution fails. The
    lifespan here just warms the cache so `/` can report resolved
    URLs and operators can see them in the logs immediately.
    """
    llmping = LLMPingClient()
    webhunter = WebHunterClient()
    try:
        llm_url = await llmping.resolve()
        wh_url = await webhunter.resolve()
        if llm_url:
            logger.info("upstream_resolved", service="llmping", url=llm_url)
        else:
            logger.warning("upstream_unresolved", service="llmping")
        if wh_url:
            logger.info("upstream_resolved", service="webhunter", url=wh_url)
        else:
            logger.warning("upstream_unresolved", service="webhunter")
    finally:
        await llmping.close()
        await webhunter.close()
    yield


app = FastAPI(
    title="CompetitorEngine",
    description=(
        "Pure orchestrator. Delegates research to WebHunter and "
        "reasoning to LLMPing via HTTP. Auto-discovers sibling "
        "services when LLMPING_URL / WEBHUNTER_URL are not set."
    ),
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
app.include_router(parser_router)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "orchestrator", "version": "2.1.0"}


@app.get("/", tags=["root"])
async def root():
    resolved = get_resolved_upstreams()
    return {
        "service": "CompetitorEngine",
        "version": "2.1.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "overview": "POST /api/v1/analyze",
            "chat": "POST /api/v1/chat",
            "parser": "POST /api/v1/parser/execute",
        },
        "upstream": {
            "llmping": settings.llmping_url,
            "webhunter": settings.webhunter_url,
        },
        "upstream_resolved": resolved,
        "discovery": {
            "enabled": not settings.require_service_urls,
            "timeout_seconds": settings.discovery_timeout_seconds,
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
