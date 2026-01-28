"""
State models for LangGraph workflow.
"""

from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict
import pandas as pd


class WorkflowState(TypedDict):
    """
    State object passed between LangGraph nodes.

    This state maintains all context throughout the agentic workflow.
    """

    # Input
    user_id: str
    query: str

    # Retrieved Context
    transactions_df: Optional[pd.DataFrame]
    retrieved_context: List[str]
    context_count: int

    # Agent Outputs
    categorization_result: Optional[Dict[str, Any]]
    anomaly_result: Optional[Dict[str, Any]]
    anomalies_detected: List[Dict[str, Any]]

    # Final Output
    final_answer: str
    reasoning_steps: List[Dict[str, Any]]

    # Metadata
    sufficient_data: bool
    error: Optional[str]
