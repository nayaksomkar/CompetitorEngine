from fastapi import APIRouter, HTTPException, status
import structlog

from app.orchestrator import Orchestrator
from app.schemas.output import ChatRequest, ChatResponse
from app.services.llmping_client import LLMPingError
from app.services.webhunter_client import WebHunterError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat follow-up",
    description=(
        "Conversational follow-up after an Overview. CompetitorEngine "
        "asks LLMPing whether fresh research is needed, calls WebHunter "
        "if so, and returns a clean answer plus optional mini-charts."
    ),
)
async def chat(request: ChatRequest) -> ChatResponse:
    log = logger.bind(session_id=request.session_id)
    log.info("chat_request_received")
    try:
        orchestrator = Orchestrator()
        return await orchestrator.chat(
            session_id=request.session_id,
            message=request.message,
            current_analysis=request.current_analysis,
            fresh_research=request.fresh_research,
        )
    except (LLMPingError, WebHunterError) as e:
        log.error("chat_upstream_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream service error: {str(e)}",
        )
    except Exception as e:
        log.error("chat_unhandled_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat failed: {str(e)}",
        )
