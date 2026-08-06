"""
State models for Self-Correcting Causal Financial Agent.

This module defines the state structure for the causal verification loop,
ensuring type safety and validation throughout the agent workflow.
"""

from typing import Dict, Any, List, Optional, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field, validator
from datetime import datetime


class HypothesisModel(BaseModel):
    """
    Structured representation of a financial hypothesis.

    Attributes:
        claim: The investment thesis or market prediction
        variables: Key market variables involved (e.g., ["Oil Price", "DAL Stock"])
        relationship: Expected causal relationship (e.g., "inverse", "direct")
        confidence: LLM's initial confidence score (0.0-1.0)
        timestamp: When the hypothesis was generated
    """

    claim: str = Field(..., description="The financial hypothesis or trading thesis")
    variables: List[str] = Field(..., description="Market variables involved in the hypothesis")
    relationship: Literal["direct", "inverse", "neutral"] = Field(
        ..., description="Expected causal relationship between variables"
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Initial confidence score")
    timestamp: datetime = Field(default_factory=datetime.now)

    @validator("variables")
    def validate_variables(cls, v: List[str]) -> List[str]:
        """Ensure at least two variables are specified."""
        if len(v) < 2:
            raise ValueError("Hypothesis must involve at least two variables")
        return v


class CausalEvidenceModel(BaseModel):
    """
    Statistical evidence from causal validation checks.

    Attributes:
        test_type: Type of statistical test performed
        p_value: Statistical significance (lower = stronger evidence)
        correlation: Pearson correlation coefficient (-1 to 1)
        granger_causality: Dictionary with Granger test results
        interpretation: Human-readable interpretation of results
        supports_hypothesis: Whether evidence supports the original claim
    """

    test_type: Literal["pearson", "granger", "both"] = Field(
        ..., description="Statistical test performed"
    )
    p_value: Optional[float] = Field(None, ge=0.0, le=1.0, description="P-value from test")
    correlation: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="Pearson correlation coefficient"
    )
    granger_causality: Optional[Dict[str, Any]] = Field(
        None, description="Granger causality test results"
    )
    interpretation: str = Field(..., description="Interpretation of statistical results")
    supports_hypothesis: bool = Field(..., description="Whether evidence supports hypothesis")
    confidence_adjustment: float = Field(
        default=0.0, ge=-1.0, le=1.0, description="Confidence adjustment based on evidence"
    )


class CritiqueModel(BaseModel):
    """
    Structured critique from the risk_critic node.

    Attributes:
        verdict: Whether hypothesis is verified or rejected
        reasoning: Detailed explanation of the verdict
        evidence_quality: Quality assessment of statistical evidence
        recommended_action: Suggested next action
        confidence_score: Final confidence after critique (0.0-1.0)
    """

    verdict: Literal["verified", "rejected", "inconclusive"] = Field(
        ..., description="Final verdict on hypothesis"
    )
    reasoning: str = Field(..., description="Detailed reasoning for verdict")
    evidence_quality: Literal["strong", "moderate", "weak"] = Field(
        ..., description="Quality of statistical evidence"
    )
    recommended_action: str = Field(..., description="Recommended next steps")
    confidence_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Final confidence score"
    )


class AgentState(TypedDict):
    """
    State object passed between LangGraph nodes in the causal verification loop.

    This state maintains all context throughout the self-correcting workflow,
    from initial hypothesis generation to final verification or refinement.

    Attributes:
        query: Original user query (e.g., "What happens to airline stocks if oil prices rise?")
        hypothesis: Current investment thesis as structured HypothesisModel
        causal_evidence: Statistical evidence from validation checks
        critique: Feedback from risk_critic node
        iteration_count: Number of refinement iterations (prevents infinite loops)
        status: Current verification status
        retrieved_context: Market context retrieved from vector store
        reasoning_steps: Audit trail of all agent decisions
        final_output: Verified hypothesis or rejection message
        error: Any error messages encountered during processing
        user_id: User identifier for data isolation
    """

    # Core Input
    query: str
    user_id: str

    # Hypothesis Evolution
    hypothesis: Optional[Dict[str, Any]]  # Serialized HypothesisModel
    causal_evidence: Optional[Dict[str, Any]]  # Serialized CausalEvidenceModel
    critique: Optional[Dict[str, Any]]  # Serialized CritiqueModel

    # Loop Control
    iteration_count: int
    status: Literal["pending", "verified", "rejected", "max_iterations"]

    # Context & Reasoning
    retrieved_context: List[str]
    reasoning_steps: List[Dict[str, Any]]

    # Output
    final_output: Optional[str]
    error: Optional[str]


# ============= Helper Functions =============


def create_initial_state(query: str, user_id: str) -> AgentState:
    """
    Create an initial AgentState for a new causal verification session.

    Args:
        query: User's financial question or hypothesis prompt
        user_id: Unique identifier for the user (for data isolation)

    Returns:
        Initialized AgentState ready for processing

    Example:
        >>> state = create_initial_state(
        ...     query="Will airline stocks drop if oil prices increase?",
        ...     user_id="user_123"
        ... )
        >>> state["iteration_count"]
        0
    """
    return AgentState(
        query=query,
        user_id=user_id,
        hypothesis=None,
        causal_evidence=None,
        critique=None,
        iteration_count=0,
        status="pending",
        retrieved_context=[],
        reasoning_steps=[],
        final_output=None,
        error=None,
    )


def serialize_hypothesis(hypothesis: HypothesisModel) -> Dict[str, Any]:
    """
    Serialize a HypothesisModel to dict for state storage.

    Args:
        hypothesis: Pydantic model instance

    Returns:
        Dictionary representation suitable for AgentState
    """
    return hypothesis.model_dump(mode="json")


def deserialize_hypothesis(data: Dict[str, Any]) -> HypothesisModel:
    """
    Deserialize a dictionary to HypothesisModel.

    Args:
        data: Dictionary from AgentState

    Returns:
        Validated HypothesisModel instance
    """
    return HypothesisModel(**data)


def serialize_evidence(evidence: CausalEvidenceModel) -> Dict[str, Any]:
    """
    Serialize a CausalEvidenceModel to dict for state storage.

    Args:
        evidence: Pydantic model instance

    Returns:
        Dictionary representation suitable for AgentState
    """
    return evidence.model_dump(mode="json")


def deserialize_evidence(data: Dict[str, Any]) -> CausalEvidenceModel:
    """
    Deserialize a dictionary to CausalEvidenceModel.

    Args:
        data: Dictionary from AgentState

    Returns:
        Validated CausalEvidenceModel instance
    """
    return CausalEvidenceModel(**data)


def serialize_critique(critique: CritiqueModel) -> Dict[str, Any]:
    """
    Serialize a CritiqueModel to dict for state storage.

    Args:
        critique: Pydantic model instance

    Returns:
        Dictionary representation suitable for AgentState
    """
    return critique.model_dump(mode="json")


def deserialize_critique(data: Dict[str, Any]) -> CritiqueModel:
    """
    Deserialize a dictionary to CritiqueModel.

    Args:
        data: Dictionary from AgentState

    Returns:
        Validated CritiqueModel instance
    """
    return CritiqueModel(**data)
