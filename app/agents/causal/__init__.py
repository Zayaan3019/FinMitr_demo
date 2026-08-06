"""
Self-Correcting Causal Financial Agent Module.

This module provides a production-grade causal verification system for financial
hypotheses using LangGraph orchestration and statistical validation.

Main Components:
- State models (Pydantic-validated)
- Four-node verification loop (analyst → validator → critic → refiner)
- Statistical causality tests (Pearson correlation, Granger causality)
- Self-correction mechanism with iteration limits

Quick Start:
    >>> from app.agents.causal import run_causal_analysis
    >>>
    >>> result = await run_causal_analysis(
    ...     query="How do oil prices affect airline stocks?",
    ...     user_id="user_123"
    ... )
    >>>
    >>> print(result["status"])  # "verified", "rejected", or "max_iterations"
    >>> print(result["final_output"])

Architecture:
    1. market_analyst: Generates hypothesis with vector store context
    2. causal_validator: Performs statistical tests (Pearson, Granger)
    3. risk_critic: Evaluates evidence and issues verdict
    4. hypothesis_refiner: Refines rejected hypotheses (up to 3 iterations)

For more details, see:
- state.py: Type-safe state models
- nodes.py: Node implementations
- graph.py: LangGraph orchestration
"""

from app.agents.causal.state import (
    AgentState,
    HypothesisModel,
    CausalEvidenceModel,
    CritiqueModel,
    create_initial_state,
    serialize_hypothesis,
    deserialize_hypothesis,
    serialize_evidence,
    deserialize_evidence,
    serialize_critique,
    deserialize_critique,
)

from app.agents.causal.nodes import (
    market_analyst,
    causal_validator,
    risk_critic,
    hypothesis_refiner,
)

from app.agents.causal.graph import (
    create_causal_agent_graph,
    get_causal_agent_graph,
    run_causal_analysis,
    run_causal_analysis_sync,
    route_after_critic,
    visualize_graph,
)

# ============= Public API =============

__all__ = [
    # Main execution functions (most commonly used)
    "run_causal_analysis",
    "run_causal_analysis_sync",
    # State models
    "AgentState",
    "HypothesisModel",
    "CausalEvidenceModel",
    "CritiqueModel",
    "create_initial_state",
    # Graph construction
    "create_causal_agent_graph",
    "get_causal_agent_graph",
    # Individual nodes (for custom workflows)
    "market_analyst",
    "causal_validator",
    "risk_critic",
    "hypothesis_refiner",
    # Utilities
    "serialize_hypothesis",
    "deserialize_hypothesis",
    "serialize_evidence",
    "deserialize_evidence",
    "serialize_critique",
    "deserialize_critique",
    "route_after_critic",
    "visualize_graph",
]


# ============= Module Metadata =============

__version__ = "1.0.0"
__author__ = "FinGuru AI Engineering Team"
__description__ = "Self-Correcting Causal Financial Agent with LangGraph"
