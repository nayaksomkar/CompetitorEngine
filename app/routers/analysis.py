from fastapi import APIRouter, HTTPException, status
import structlog

from app.orchestrator import Orchestrator
from app.schemas.business import FormInput
from app.schemas.output import AnalysisResult

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.post(
    "/analyze",
    response_model=AnalysisResult,
    status_code=status.HTTP_200_OK,
    summary="Run competitive analysis",
    description="Submit business form data to receive a complete competitive analysis",
)
async def analyze_business(form_input: FormInput) -> AnalysisResult:
    """
    Main analysis endpoint.
    Accepts business form data and optional user query.
    Returns structured competitive analysis with competitors, SWOT,
    recommendations, charts, and action plan.
    """
    log = logger.bind(business=form_input.business_name)
    log.info("analysis_request_received")

    try:
        orchestrator = Orchestrator()
        result = await orchestrator.run_analysis(form_input)
        return result
    except Exception as e:
        log.error("analysis_endpoint_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )


@router.post(
    "/analyze/async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit analysis job (async placeholder)",
)
async def analyze_async(form_input: FormInput):
    """Placeholder for future async job submission."""
    return {
        "status": "accepted",
        "message": "Async processing not yet implemented. Use /analyze for synchronous processing.",
        "business_name": form_input.business_name,
    }
