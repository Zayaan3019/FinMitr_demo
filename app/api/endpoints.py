"""
FastAPI endpoints for FinGuru.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from typing import Dict, Any
import pandas as pd
import io
import time

from app.models.schemas import (
    IngestRequest,
    IngestResponse,
    ChatRequest,
    ChatResponse,
    AgentStep,
    HealthResponse,
    ErrorResponse,
)
from app.db.vector_store import get_vector_store
from app.agents.workflow import run_workflow
from app.agents.budget import budget_agent
from app.agents.forecasting import forecasting_agent
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.
    """
    try:
        vector_store = get_vector_store()
        stats = vector_store.get_collection_stats()

        return HealthResponse(
            status="healthy",
            version=settings.app_version,
            components={
                "vector_store": "operational",
                "llm": "operational",
                "total_documents": str(stats.get("total_documents", 0)),
            },
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="degraded",
            version=settings.app_version,
            components={"error": str(e)},
        )


@router.post("/ingest", response_model=IngestResponse)
async def ingest_data(
    user_id: str, file: UploadFile = File(...), background_tasks: BackgroundTasks = None
) -> IngestResponse:
    """
    Ingest transaction data from CSV file.

    Args:
        user_id: Unique user identifier (query parameter)
        file: CSV file with columns: date, amount, description, category

    Returns:
        Ingestion status and statistics
    """
    start_time = time.time()

    try:
        # Validate file type
        if not file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only CSV files are supported")

        # Validate file size
        contents = await file.read()
        file_size_mb = len(contents) / (1024 * 1024)

        if file_size_mb > settings.max_file_size_mb:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds {settings.max_file_size_mb}MB limit",
            )

        # Read CSV
        try:
            df = pd.read_csv(io.BytesIO(contents))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid CSV format: {str(e)}")

        # Validate required columns
        required_columns = {"date", "amount", "description"}
        if not required_columns.issubset(df.columns):
            missing = required_columns - set(df.columns)
            raise HTTPException(
                status_code=400, detail=f"Missing required columns: {missing}"
            )

        # Add category if missing
        if "category" not in df.columns:
            df["category"] = ""

        # Convert date column
        try:
            df["date"] = pd.to_datetime(df["date"])
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid date format: {str(e)}"
            )

        # Ingest into vector store
        vector_store = get_vector_store()
        result = vector_store.ingest_user_data(user_id=user_id, dataframe=df)

        elapsed = time.time() - start_time

        logger.info(
            f"Ingested {len(df)} transactions for user {user_id} in {elapsed:.2f}s"
        )

        return IngestResponse(
            success=True,
            message=f"Successfully ingested {len(df)} transactions",
            user_id=user_id,
            transactions_count=len(df),
            embedding_time_seconds=round(elapsed, 2),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Process user query through the agentic workflow.

    Args:
        request: Chat request with user_id and query

    Returns:
        Complete workflow results with reasoning and advice
    """
    logger.info(f"Chat request from user {request.user_id}: {request.query}")

    try:
        # Run the agentic workflow
        result = run_workflow(user_id=request.user_id, query=request.query)

        # Convert reasoning steps to AgentStep models
        agent_steps = [
            AgentStep(
                agent=step["agent"],
                action=step["action"],
                result=step.get("result"),
                timestamp=step["timestamp"],
            )
            for step in result["reasoning_steps"]
        ]

        return ChatResponse(
            success=result["success"],
            user_id=result["user_id"],
            query=result["query"],
            reasoning_steps=agent_steps,
            final_answer=result["final_answer"],
            anomalies_detected=result["anomalies_detected"],
            context_retrieved=result["context_retrieved"],
            processing_time_seconds=result["processing_time_seconds"],
        )

    except Exception as e:
        logger.error(f"Chat processing failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to process query: {str(e)}"
        )


@router.delete("/user/{user_id}")
async def delete_user_data(user_id: str) -> Dict[str, Any]:
    """
    Delete all data for a specific user (GDPR compliance).

    Args:
        user_id: User identifier

    Returns:
        Deletion confirmation
    """
    try:
        vector_store = get_vector_store()
        vector_store.delete_user_data(user_id)

        logger.info(f"Deleted all data for user {user_id}")

        return {
            "success": True,
            "message": f"All data for user {user_id} has been deleted",
            "user_id": user_id,
        }

    except Exception as e:
        logger.error(f"Failed to delete user data: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to delete user data: {str(e)}"
        )


@router.get("/stats")
async def get_statistics() -> Dict[str, Any]:
    """
    Get overall system statistics.

    Returns:
        System statistics
    """
    try:
        vector_store = get_vector_store()
        stats = vector_store.get_collection_stats()

        return {"success": True, "statistics": stats}

    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        return {"success": False, "error": str(e)}


@router.post("/analyze/budget/{user_id}")
async def analyze_budget(user_id: str) -> Dict[str, Any]:
    """
    Analyze spending and suggest budgets for a user.

    Args:
        user_id: User identifier

    Returns:
        Budget analysis and suggestions
    """
    try:
        vector_store = get_vector_store()
        transactions_df = vector_store.get_user_transactions(user_id)

        if len(transactions_df) == 0:
            raise HTTPException(
                status_code=404, detail=f"No transactions found for user {user_id}"
            )

        result = budget_agent(user_id, transactions_df)

        logger.info(f"Budget analysis completed for user {user_id}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Budget analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Budget analysis failed: {str(e)}")


@router.post("/analyze/forecast/{user_id}")
async def forecast_spending(user_id: str, months_ahead: int = 3) -> Dict[str, Any]:
    """
    Forecast future spending for a user.

    Args:
        user_id: User identifier
        months_ahead: Number of months to forecast (default: 3)

    Returns:
        Spending forecast and seasonal patterns
    """
    try:
        if months_ahead < 1 or months_ahead > 12:
            raise HTTPException(
                status_code=400, detail="months_ahead must be between 1 and 12"
            )

        vector_store = get_vector_store()
        transactions_df = vector_store.get_user_transactions(user_id)

        if len(transactions_df) == 0:
            raise HTTPException(
                status_code=404, detail=f"No transactions found for user {user_id}"
            )

        result = forecasting_agent(transactions_df)

        logger.info(f"Forecast generated for user {user_id}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forecasting failed: {e}")
        raise HTTPException(status_code=500, detail=f"Forecasting failed: {str(e)}")


@router.get("/analyze/summary/{user_id}")
async def get_financial_summary(user_id: str) -> Dict[str, Any]:
    """
    Get comprehensive financial summary for a user.

    Args:
        user_id: User identifier

    Returns:
        Complete financial overview
    """
    try:
        vector_store = get_vector_store()
        transactions_df = vector_store.get_user_transactions(user_id)

        if len(transactions_df) == 0:
            raise HTTPException(
                status_code=404, detail=f"No transactions found for user {user_id}"
            )

        # Calculate summary statistics
        df = transactions_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        expenses = df[df["amount"] < 0]
        income = df[df["amount"] > 0]

        # Monthly breakdown
        df["month"] = df["date"].dt.to_period("M")
        monthly_summary = df.groupby("month")["amount"].sum()

        # Category breakdown
        category_summary = expenses.groupby("category")["amount"].sum().abs()

        summary = {
            "success": True,
            "user_id": user_id,
            "date_range": {
                "start": str(df["date"].min()),
                "end": str(df["date"].max()),
                "total_days": (df["date"].max() - df["date"].min()).days,
            },
            "transactions": {
                "total": len(df),
                "expenses": len(expenses),
                "income": len(income),
            },
            "financial_overview": {
                "total_income": float(income["amount"].sum()),
                "total_expenses": float(abs(expenses["amount"].sum())),
                "net_cashflow": float(df["amount"].sum()),
                "average_transaction": float(abs(df["amount"].mean())),
                "largest_expense": (
                    float(abs(expenses["amount"].min())) if len(expenses) > 0 else 0
                ),
                "largest_income": (
                    float(income["amount"].max()) if len(income) > 0 else 0
                ),
            },
            "category_breakdown": {
                k: float(v) for k, v in category_summary.nlargest(10).items()
            },
            "monthly_trend": {
                str(k): float(v) for k, v in monthly_summary.tail(6).items()
            },
        }

        logger.info(f"Financial summary generated for user {user_id}")

        return summary

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Summary generation failed: {str(e)}"
        )
