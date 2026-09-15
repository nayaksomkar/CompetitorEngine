from fastapi import APIRouter, HTTPException, status
import structlog

from app.orchestrator import Orchestrator
from app.schemas.output import AnalysisResult, ParserExecuteRequest, ParserOutput
from app.services.llmping_client import LLMPingError
from app.services.webhunter_client import WebHunterError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/parser", tags=["parser"])


@router.post(
    "/execute",
    response_model=ParserOutput,
    status_code=status.HTTP_200_OK,
    summary="Execute parser-driven analysis",
    description=(
        "Accepts structured parser output (intent, entities, "
        "constraints) and executes the required operations. "
        "Supports fault-tolerant execution with retries, partial "
        "results, and per-entity status tracking. Dynamic company "
        "data is capped at 3."
    ),
)
async def parser_execute(request: ParserExecuteRequest) -> ParserOutput:
    log = logger.bind(intent=request.parser_input.intent)
    log.info("parser_execute_request_received")
    try:
        orchestrator = Orchestrator()
        return await orchestrator.execute(request.parser_input)
    except (LLMPingError, WebHunterError) as e:
        log.error("parser_execute_upstream_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream service error: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("parser_execute_unhandled_error", error=str(e))
        # Return structured error rather than raising — keeps
        # the parser contract intact.
        return ParserOutput(
            intent=request.parser_input.intent,
            status="error",
            data=AnalysisResult(),
            error=f"Analysis failed: {str(e)}",
        )
