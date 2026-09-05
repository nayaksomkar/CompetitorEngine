from fastapi import APIRouter, HTTPException, status
import structlog

from app.orchestrator import Orchestrator
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult
from app.services.llmping_client import LLMPingError
from app.services.webhunter_client import WebHunterError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["overview"])


@router.post(
    "/analyze",
    response_model=AnalysisResult,
    status_code=status.HTTP_200_OK,
    summary="Run competitive analysis (Overview)",
    description=(
        "Submit business form data. CompetitorEngine orchestrates "
        "WebHunter for fresh research and LLMPing for reasoning, "
        "then returns a complete structured analysis."
    ),
)
async def analyze_business(form_input: FormInput) -> AnalysisResult:
    log = logger.bind(business=form_input.business_name)
    log.info("overview_request_received")
    try:
        orchestrator = Orchestrator()
        return await orchestrator.run_overview(form_input)
    except (LLMPingError, WebHunterError) as e:
        log.error("overview_upstream_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream service error: {str(e)}",
        )
    except Exception as e:
        log.error("overview_unhandled_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )
