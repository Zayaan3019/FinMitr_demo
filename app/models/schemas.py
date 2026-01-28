"""
Pydantic models for transaction data and API schemas.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, validator
from enum import Enum


class TransactionCategory(str, Enum):
    """Predefined transaction categories."""

    GROCERIES = "Groceries"
    UTILITIES = "Utilities"
    TRANSPORTATION = "Transportation"
    ENTERTAINMENT = "Entertainment"
    HEALTHCARE = "Healthcare"
    SHOPPING = "Shopping"
    DINING = "Dining"
    SALARY = "Salary"
    INVESTMENT = "Investment"
    RENT = "Rent"
    INSURANCE = "Insurance"
    OTHER = "Other"


class Transaction(BaseModel):
    """Individual transaction model."""

    date: datetime
    amount: float
    description: str
    category: Optional[str] = None
    user_id: str

    @validator("amount")
    def validate_amount(cls, v: float) -> float:
        """Ensure amount is a valid number."""
        if v == 0:
            raise ValueError("Amount cannot be zero")
        return round(v, 2)

    @validator("description")
    def validate_description(cls, v: str) -> str:
        """Ensure description is not empty."""
        if not v or not v.strip():
            raise ValueError("Description cannot be empty")
        return v.strip()

    class Config:
        json_schema_extra = {
            "example": {
                "date": "2026-01-15T10:30:00",
                "amount": 150.50,
                "description": "Whole Foods Market",
                "category": "Groceries",
                "user_id": "user_001",
            }
        }


class IngestRequest(BaseModel):
    """Request model for data ingestion."""

    user_id: str = Field(..., description="Unique user identifier")

    @validator("user_id")
    def validate_user_id(cls, v: str) -> str:
        """Validate user ID format."""
        if not v or len(v) < 3:
            raise ValueError("user_id must be at least 3 characters")
        return v.strip()

    class Config:
        json_schema_extra = {"example": {"user_id": "user_001"}}


class IngestResponse(BaseModel):
    """Response model for data ingestion."""

    success: bool
    message: str
    user_id: str
    transactions_count: int
    embedding_time_seconds: float


class ChatRequest(BaseModel):
    """Request model for chat/query endpoint."""

    user_id: str = Field(..., description="Unique user identifier")
    query: str = Field(..., description="User's financial query")

    @validator("query")
    def validate_query(cls, v: str) -> str:
        """Ensure query is not empty."""
        if not v or len(v.strip()) < 5:
            raise ValueError("Query must be at least 5 characters")
        return v.strip()

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_001",
                "query": "What are my spending patterns this month?",
            }
        }


class AgentStep(BaseModel):
    """Individual agent step in the workflow."""

    agent: str
    action: str
    result: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ChatResponse(BaseModel):
    """Response model for chat/query endpoint."""

    success: bool
    user_id: str
    query: str
    reasoning_steps: List[AgentStep]
    final_answer: str
    anomalies_detected: List[Dict[str, Any]]
    context_retrieved: int
    processing_time_seconds: float


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime = Field(default_factory=datetime.now)
    components: Dict[str, str]


class ErrorResponse(BaseModel):
    """Standard error response."""

    success: bool = False
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
