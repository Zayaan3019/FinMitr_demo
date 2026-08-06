"""
LangGraph orchestration for Self-Correcting Causal Financial Agent.

This module defines the state graph that coordinates the causal verification loop,
implementing conditional edges for self-correction and iteration control.
"""

from typing import Dict, Any, Literal
from langgraph.graph import StateGraph, END
from datetime import datetime

from app.agents.causal.state import AgentState, create_initial_state
from app.agents.causal.nodes import (
    market_analyst,
    causal_validator,
    risk_critic,
    hypothesis_refiner,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============= Conditional Edge Functions =============


def route_after_critic(state: AgentState) -> Literal["refine", "end"]:
    """
    Conditional edge: Determine next step after risk_critic evaluation.

    Routing Logic:
    - If hypothesis is VERIFIED -> End workflow (success)
    - If hypothesis is REJECTED and iterations < 3 -> Refine hypothesis
    - If hypothesis is REJECTED and iterations >= 3 -> End workflow (max iterations)
    - If any error occurred -> End workflow (failure)

    Args:
        state: Current AgentState with critique

    Returns:
        "refine" to continue to hypothesis_refiner, "end" to terminate
    """
    status = state.get("status", "pending")
    iteration_count = state.get("iteration_count", 0)
    error = state.get("error")

    logger.info(f"[route_after_critic] Status: {status}, Iteration: {iteration_count}")

    # If verified, end successfully
    if status == "verified":
        logger.info("[route_after_critic] Hypothesis verified -> END")
        return "end"

    # If error occurred, end with failure
    if error:
        logger.warning(f"[route_after_critic] Error detected -> END: {error}")
        return "end"

    # If max iterations reached, end
    if status == "max_iterations" or iteration_count >= 3:
        logger.warning(f"[route_after_critic] Max iterations reached -> END")
        return "end"

    # If rejected, refine hypothesis
    if status == "rejected":
        logger.info(
            f"[route_after_critic] Hypothesis rejected -> REFINE (iteration {iteration_count + 1})"
        )
        return "refine"

    # Default: end for any unexpected status
    logger.warning(f"[route_after_critic] Unexpected status '{status}' -> END")
    return "end"


# ============= Graph Construction =============


def create_causal_agent_graph() -> StateGraph:
    """
    Construct the LangGraph state machine for causal verification.

    Graph Structure:
    ```
    START
      ↓
    market_analyst (Generate hypothesis)
      ↓
    causal_validator (Run statistical tests)
      ↓
    risk_critic (Evaluate evidence)
      ↓
    [Conditional Edge]
      ├─ verified → END
      └─ rejected → hypothesis_refiner
                      ↓
                    causal_validator (Re-test)
                      ↓
                    (Loop back to risk_critic)
    ```

    Returns:
        Compiled StateGraph ready for execution

    Example:
        >>> graph = create_causal_agent_graph()
        >>> result = graph.invoke({
        ...     "query": "Will oil prices affect airline stocks?",
        ...     "user_id": "user_123"
        ... })
    """
    logger.info("[create_causal_agent_graph] Building graph...")

    # Initialize graph with AgentState type
    workflow = StateGraph(AgentState)

    # ===== Add Nodes =====
    workflow.add_node("market_analyst", market_analyst)
    workflow.add_node("causal_validator", causal_validator)
    workflow.add_node("risk_critic", risk_critic)
    workflow.add_node("hypothesis_refiner", hypothesis_refiner)

    # ===== Define Entry Point =====
    workflow.set_entry_point("market_analyst")

    # ===== Add Sequential Edges =====
    # market_analyst → causal_validator
    workflow.add_edge("market_analyst", "causal_validator")

    # causal_validator → risk_critic
    workflow.add_edge("causal_validator", "risk_critic")

    # ===== Add Conditional Edge After Critic =====
    workflow.add_conditional_edges(
        "risk_critic",
        route_after_critic,
        {
            "refine": "hypothesis_refiner",  # Go to refiner if rejected
            "end": END,  # End if verified or max iterations
        },
    )

    # ===== Loop: Refiner → Validator =====
    # After refinement, re-validate the hypothesis
    workflow.add_edge("hypothesis_refiner", "causal_validator")

    # ===== Compile Graph =====
    compiled_graph = workflow.compile()

    logger.info("[create_causal_agent_graph] Graph compiled successfully")
    return compiled_graph


# ============= Graph Singleton =============


_causal_graph = None


def get_causal_agent_graph() -> StateGraph:
    """
    Get or create the causal agent graph (singleton pattern).

    This ensures the graph is compiled only once for efficiency.

    Returns:
        Compiled StateGraph instance
    """
    global _causal_graph
    if _causal_graph is None:
        _causal_graph = create_causal_agent_graph()
    return _causal_graph


# ============= Convenience Execution Function =============


async def run_causal_analysis(query: str, user_id: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Execute the complete causal verification workflow.

    This is the main entry point for running causal analysis on a financial query.

    Args:
        query: User's financial question or hypothesis (e.g., "How do oil prices affect airlines?")
        user_id: Unique identifier for the user (for data isolation)
        verbose: If True, log detailed reasoning steps

    Returns:
        Dictionary containing:
            - status: "verified", "rejected", or "max_iterations"
            - final_output: Human-readable result message
            - hypothesis: Final hypothesis (verified or last attempted)
            - evidence: Statistical evidence from validation
            - critique: Risk critic's evaluation
            - reasoning_steps: Full audit trail of agent decisions
            - iteration_count: Number of refinement iterations

    Example:
        >>> result = await run_causal_analysis(
        ...     query="Do rising oil prices hurt airline stocks?",
        ...     user_id="user_123"
        ... )
        >>> print(result["status"])
        'verified'
        >>> print(result["final_output"])
        '✓ VERIFIED: Rising oil prices lead to declining airline stock prices...'

    Raises:
        Exception: If critical error occurs during workflow execution
    """
    logger.info(f"[run_causal_analysis] Starting for query: '{query[:60]}...'")
    start_time = datetime.now()

    try:
        # Initialize state
        initial_state = create_initial_state(query=query, user_id=user_id)

        # Get graph
        graph = get_causal_agent_graph()

        # Execute workflow
        logger.info("[run_causal_analysis] Invoking graph...")
        final_state = await graph.ainvoke(initial_state)

        # Calculate execution time
        execution_time = (datetime.now() - start_time).total_seconds()

        # Log reasoning steps if verbose
        if verbose:
            logger.info("[run_causal_analysis] Reasoning steps:")
            for i, step in enumerate(final_state.get("reasoning_steps", []), 1):
                logger.info(f"  {i}. [{step.get('node')}] {step.get('action')}")

        # Prepare result
        result = {
            "status": final_state.get("status", "unknown"),
            "final_output": final_state.get("final_output", "No final output generated"),
            "hypothesis": final_state.get("hypothesis"),
            "evidence": final_state.get("causal_evidence"),
            "critique": final_state.get("critique"),
            "reasoning_steps": final_state.get("reasoning_steps", []),
            "iteration_count": final_state.get("iteration_count", 0),
            "execution_time_seconds": execution_time,
            "error": final_state.get("error"),
        }

        logger.info(
            f"[run_causal_analysis] Completed in {execution_time:.2f}s. "
            f"Status: {result['status']}, Iterations: {result['iteration_count']}"
        )

        return result

    except Exception as e:
        logger.error(f"[run_causal_analysis] Critical error: {e}", exc_info=True)
        return {
            "status": "error",
            "final_output": f"Error during causal analysis: {str(e)}",
            "error": str(e),
            "execution_time_seconds": (datetime.now() - start_time).total_seconds(),
        }


# ============= Synchronous Wrapper (Optional) =============


def run_causal_analysis_sync(query: str, user_id: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Synchronous wrapper for run_causal_analysis.

    Useful for non-async contexts (e.g., FastAPI endpoints with sync handlers).

    Args:
        query: User's financial question
        user_id: User identifier
        verbose: Enable detailed logging

    Returns:
        Same as run_causal_analysis
    """
    import asyncio

    # Check if event loop is running
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running, safe to use asyncio.run
        return asyncio.run(run_causal_analysis(query, user_id, verbose))
    else:
        # Event loop already running (e.g., in pytest-asyncio)
        # Create a new task
        return loop.run_until_complete(run_causal_analysis(query, user_id, verbose))


# ============= Graph Visualization (Optional) =============


def visualize_graph(output_path: str = "causal_agent_graph.png") -> None:
    """
    Generate a visual representation of the graph structure.

    Requires graphviz to be installed:
        pip install graphviz

    Args:
        output_path: Path to save the visualization

    Example:
        >>> visualize_graph("./docs/causal_graph.png")
    """
    try:
        from langgraph.graph import Graph

        graph = get_causal_agent_graph()

        # LangGraph has built-in visualization
        # Note: This requires graphviz system package
        graph_image = graph.get_graph().draw_mermaid_png()

        with open(output_path, "wb") as f:
            f.write(graph_image)

        logger.info(f"[visualize_graph] Graph saved to {output_path}")

    except ImportError:
        logger.warning("[visualize_graph] graphviz not installed, skipping visualization")
    except Exception as e:
        logger.error(f"[visualize_graph] Failed: {e}")
