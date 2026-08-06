"""
FastAPI endpoints for Self-Correcting Causal Financial Agent.

Add these endpoints to your existing FastAPI app to expose causal analysis functionality.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.agents.causal import run_causal_analysis, run_causal_analysis_sync
from app.core.logging import get_logger

logger = get_logger(__name__)

# Create router
causal_router = APIRouter(prefix="/causal", tags=["Causal Analysis"])


# ============= Request/Response Models =============


class CausalAnalysisRequest(BaseModel):
    """Request model for causal analysis."""

    query: str = Field(
        ...,
        description="Financial hypothesis or question to analyze",
        example="How do oil prices affect airline stocks?",
    )
    user_id: str = Field(
        ..., description="Unique user identifier for data isolation", example="user_123"
    )
    verbose: bool = Field(default=False, description="Enable detailed logging")


class HypothesisResponse(BaseModel):
    """Structured hypothesis data."""

    claim: str
    variables: List[str]
    relationship: str
    confidence: float


class EvidenceResponse(BaseModel):
    """Statistical evidence data."""

    test_type: str
    p_value: Optional[float]
    correlation: Optional[float]
    interpretation: str
    supports_hypothesis: bool


class CritiqueResponse(BaseModel):
    """Critique and verdict data."""

    verdict: str
    reasoning: str
    evidence_quality: str
    recommended_action: str
    confidence_score: float


class CausalAnalysisResponse(BaseModel):
    """Response model for causal analysis."""

    status: str = Field(..., description="Analysis status: verified, rejected, or max_iterations")
    final_output: str = Field(..., description="Human-readable result message")
    hypothesis: Optional[Dict[str, Any]] = Field(None, description="Final hypothesis (structured)")
    evidence: Optional[Dict[str, Any]] = Field(
        None, description="Statistical evidence (structured)"
    )
    critique: Optional[Dict[str, Any]] = Field(None, description="Risk critique (structured)")
    iteration_count: int = Field(..., description="Number of refinement iterations")
    execution_time_seconds: float = Field(..., description="Total execution time")
    reasoning_steps: List[Dict[str, Any]] = Field(
        default_factory=list, description="Audit trail of agent decisions"
    )
    error: Optional[str] = Field(None, description="Error message if analysis failed")


class AnalysisStatusResponse(BaseModel):
    """Response for analysis status check."""

    message: str
    timestamp: str
    version: str = "1.0.0"


# ============= Endpoints =============


@causal_router.get("/status", response_model=AnalysisStatusResponse)
async def get_status():
    """
    Check if the causal analysis service is running.

    Returns:
        Service status and metadata
    """
    return {
        "message": "Causal Analysis Service is running",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
    }


@causal_router.post("/analyze", response_model=CausalAnalysisResponse)
async def analyze_causality(request: CausalAnalysisRequest):
    """
    Analyze causal relationships in financial markets.

    This endpoint:
    1. Generates a testable hypothesis from your query
    2. Validates it using statistical tests (Pearson correlation, Granger causality)
    3. Critiques the evidence quality
    4. Refines rejected hypotheses (up to 3 iterations)

    Args:
        request: Analysis request with query and user_id

    Returns:
        Detailed analysis result with hypothesis, evidence, and verdict

    Example:
        ```json
        {
            "query": "How do oil prices affect airline stocks?",
            "user_id": "user_123",
            "verbose": false
        }
        ```

    Raises:
        HTTPException: If analysis fails due to invalid input or system error
    """
    logger.info(f"[/analyze] Request from user {request.user_id}: {request.query[:50]}...")

    try:
        # Validate query
        if not request.query or len(request.query.strip()) < 10:
            raise HTTPException(status_code=400, detail="Query must be at least 10 characters")

        # Run analysis
        result = await run_causal_analysis(
            query=request.query, user_id=request.user_id, verbose=request.verbose
        )

        # Log result
        logger.info(
            f"[/analyze] Completed for user {request.user_id}. "
            f"Status: {result['status']}, Iterations: {result['iteration_count']}"
        )

        return CausalAnalysisResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[/analyze] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@causal_router.post("/analyze-sync", response_model=CausalAnalysisResponse)
def analyze_causality_sync(request: CausalAnalysisRequest):
    """
    Synchronous version of /analyze endpoint.

    Use this if your application prefers synchronous handlers.

    Args:
        request: Analysis request with query and user_id

    Returns:
        Same as /analyze endpoint
    """
    logger.info(f"[/analyze-sync] Request from user {request.user_id}")

    try:
        result = run_causal_analysis_sync(
            query=request.query, user_id=request.user_id, verbose=request.verbose
        )

        return CausalAnalysisResponse(**result)

    except Exception as e:
        logger.error(f"[/analyze-sync] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@causal_router.post("/validate-hypothesis")
async def validate_existing_hypothesis(
    hypothesis_claim: str = Field(..., description="Hypothesis to validate"),
    variable1: str = Field(..., description="First market variable"),
    variable2: str = Field(..., description="Second market variable"),
    expected_relationship: str = Field(
        ..., description="Expected relationship: direct, inverse, or neutral"
    ),
    user_id: str = Field(..., description="User identifier"),
):
    """
    Validate a pre-existing hypothesis without generating a new one.

    This endpoint skips the market_analyst node and directly validates
    a user-provided hypothesis.

    Args:
        hypothesis_claim: The hypothesis statement
        variable1: First market variable (e.g., "Oil Price")
        variable2: Second market variable (e.g., "Airline Stocks")
        expected_relationship: "direct", "inverse", or "neutral"
        user_id: User identifier

    Returns:
        Validation results with statistical evidence
    """
    logger.info(f"[/validate-hypothesis] Validating: {hypothesis_claim[:50]}...")

    try:
        from app.agents.causal.state import (
            HypothesisModel,
            create_initial_state,
            serialize_hypothesis,
        )
        from app.agents.causal.nodes import causal_validator, risk_critic

        # Create hypothesis
        hypothesis = HypothesisModel(
            claim=hypothesis_claim,
            variables=[variable1, variable2],
            relationship=expected_relationship,
            confidence=0.5,
        )

        # Create state
        state = create_initial_state(query=hypothesis_claim, user_id=user_id)
        state["hypothesis"] = serialize_hypothesis(hypothesis)

        # Run validation
        state = await causal_validator(state)
        state = await risk_critic(state)

        return {
            "status": state["status"],
            "evidence": state.get("causal_evidence"),
            "critique": state.get("critique"),
            "reasoning_steps": state.get("reasoning_steps", []),
        }

    except Exception as e:
        logger.error(f"[/validate-hypothesis] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


@causal_router.get("/examples")
async def get_example_queries():
    """
    Get example queries for causal analysis.

    Returns:
        List of example queries with descriptions
    """
    return {
        "examples": [
            {
                "query": "How do oil prices affect airline stocks?",
                "description": "Classic inverse relationship example",
                "expected_result": "Inverse correlation (oil ↑ → airlines ↓)",
            },
            {
                "query": "Do interest rate increases affect bond prices?",
                "description": "Fixed-income causality",
                "expected_result": "Inverse relationship (rates ↑ → bonds ↓)",
            },
            {
                "query": "How does the VIX volatility index correlate with S&P 500?",
                "description": "Volatility vs. market performance",
                "expected_result": "Inverse correlation (VIX ↑ → S&P ↓)",
            },
            {
                "query": "Do gold prices hedge against inflation?",
                "description": "Inflation hedge hypothesis",
                "expected_result": "Direct correlation (inflation ↑ → gold ↑)",
            },
            {
                "query": "Will semiconductor shortages affect automobile stocks?",
                "description": "Supply chain causality",
                "expected_result": "Inverse relationship (shortages ↑ → auto stocks ↓)",
            },
        ]
    }


# ============= Integration Helper =============


def register_causal_endpoints(app):
    """
    Register causal analysis endpoints with the main FastAPI app.

    Usage in main.py:
        from app.agents.causal.api import register_causal_endpoints

        app = FastAPI()
        register_causal_endpoints(app)

    Args:
        app: FastAPI application instance
    """
    app.include_router(causal_router)
    logger.info("Causal Analysis endpoints registered at /causal")


# ============= Batch Analysis (Optional) =============


@causal_router.post("/batch-analyze")
async def batch_analyze(
    queries: List[str] = Field(..., description="List of queries to analyze"),
    user_id: str = Field(..., description="User identifier"),
    background_tasks: BackgroundTasks = None,
):
    """
    Analyze multiple causal queries in parallel.

    Note: This can be resource-intensive. Consider using background tasks
    for large batches.

    Args:
        queries: List of financial questions
        user_id: User identifier
        background_tasks: FastAPI background tasks

    Returns:
        List of analysis results
    """
    logger.info(f"[/batch-analyze] Processing {len(queries)} queries for user {user_id}")

    if len(queries) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 queries per batch")

    try:
        import asyncio

        # Run analyses in parallel
        tasks = [run_causal_analysis(query, user_id, verbose=False) for query in queries]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Format results
        formatted_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                formatted_results.append(
                    {"query": queries[i], "status": "error", "error": str(result)}
                )
            else:
                formatted_results.append(
                    {
                        "query": queries[i],
                        "status": result["status"],
                        "final_output": result["final_output"],
                        "iteration_count": result["iteration_count"],
                    }
                )

        return {
            "total": len(queries),
            "completed": sum(1 for r in formatted_results if r["status"] != "error"),
            "results": formatted_results,
        }

    except Exception as e:
        logger.error(f"[/batch-analyze] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch analysis failed: {str(e)}")
