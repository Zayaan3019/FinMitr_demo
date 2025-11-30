from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    user_id: str
    message: str
    conversation_history: Optional[List[ChatMessage]] = []

class ChatResponse(BaseModel):
    answer: str
    confidence: Optional[float] = 0.95
    sources: Optional[List[str]] = []
    suggested_actions: Optional[List[str]] = []
    debug_info: Optional[Dict[str, Any]] = {}

class BudgetAnalysisRequest(BaseModel):
    user_id: str
    month: Optional[str] = None

class BudgetInsight(BaseModel):
    category: str
    spent: float
    limit: float
    percentage: float
    status: str  # "good", "warning", "exceeded"
    recommendation: str

class BudgetAnalysisResponse(BaseModel):
    total_spent: float
    total_limit: float
    insights: List[BudgetInsight]
    overall_status: str
    recommendations: List[str]
