"""
LangGraph Workflow Orchestration.
Coordinates multiple agents in a stateful graph.
"""

from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from datetime import datetime
import time

from app.models.state import WorkflowState
from app.db.vector_store import get_vector_store
from app.agents.categorization import categorization_agent
from app.agents.anomaly_detection import anomaly_detection_agent, get_spending_insights
from app.agents.advisor import advisor_agent, insufficient_data_response
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============= Node Functions =============


def retrieve_context_node(state: WorkflowState) -> WorkflowState:
    """
    Node: Retrieve relevant transaction context from vector store.
    """
    logger.info(f"[Retrieve] Starting for user {state['user_id']}")

    try:
        vector_store = get_vector_store()

        # Retrieve context
        results = vector_store.retrieve_context(
            user_id=state["user_id"], query=state["query"], top_k=20
        )

        # Get full transaction data for analysis
        transactions_df = vector_store.get_user_transactions(state["user_id"])

        # Check if sufficient data
        sufficient = len(results["documents"]) >= 5 and len(transactions_df) >= 10

        state["retrieved_context"] = results["documents"]
        state["context_count"] = results["count"]
        state["transactions_df"] = transactions_df
        state["sufficient_data"] = sufficient

        step = {
            "agent": "Retrieval",
            "action": f"Retrieved {results['count']} relevant transactions",
            "result": f"Found {len(transactions_df)} total transactions for user",
            "timestamp": datetime.now(),
        }
        state["reasoning_steps"].append(step)

        logger.info(f"[Retrieve] Found {results['count']} relevant transactions")

    except Exception as e:
        logger.error(f"[Retrieve] Failed: {e}")
        state["sufficient_data"] = False
        state["error"] = str(e)

    return state


def categorization_node(state: WorkflowState) -> WorkflowState:
    """
    Node: Categorize any uncategorized transactions.
    """
    logger.info("[Categorization] Starting...")

    try:
        if state["transactions_df"] is None or len(state["transactions_df"]) == 0:
            logger.warning("[Categorization] No transactions to categorize")
            return state

        result = categorization_agent(state["transactions_df"])
        state["categorization_result"] = result

        step = {
            "agent": "Categorization",
            "action": "Categorized uncategorized transactions",
            "result": result.get("message", "Completed"),
            "timestamp": datetime.now(),
        }
        state["reasoning_steps"].append(step)

        logger.info(f"[Categorization] {result.get('message')}")

    except Exception as e:
        logger.error(f"[Categorization] Failed: {e}")
        state["error"] = str(e)

    return state


def anomaly_detection_node(state: WorkflowState) -> WorkflowState:
    """
    Node: Detect spending anomalies using Isolation Forest.
    """
    logger.info("[Anomaly Detection] Starting...")

    try:
        if state["transactions_df"] is None or len(state["transactions_df"]) == 0:
            logger.warning("[Anomaly Detection] No transactions to analyze")
            return state

        # Run anomaly detection
        anomaly_result = anomaly_detection_agent(state["transactions_df"])
        state["anomaly_result"] = anomaly_result
        state["anomalies_detected"] = anomaly_result.get("anomalies_detected", [])

        step = {
            "agent": "Anomaly Detection",
            "action": "Analyzed transactions for anomalies",
            "result": f"Found {anomaly_result.get('anomaly_count', 0)} anomalies",
            "timestamp": datetime.now(),
        }
        state["reasoning_steps"].append(step)

        logger.info(
            f"[Anomaly Detection] Found {len(state['anomalies_detected'])} anomalies"
        )

    except Exception as e:
        logger.error(f"[Anomaly Detection] Failed: {e}")
        state["error"] = str(e)

    return state


def advisor_node(state: WorkflowState) -> WorkflowState:
    """
    Node: Generate financial advice using LLM (RAG).
    """
    logger.info("[Advisor] Starting...")

    try:
        # Check if we have sufficient data
        if not state.get("sufficient_data", False):
            state["final_answer"] = insufficient_data_response(state["query"])
            step = {
                "agent": "Advisor",
                "action": "Generated insufficient data response",
                "result": "Insufficient data for analysis",
                "timestamp": datetime.now(),
            }
            state["reasoning_steps"].append(step)
            return state

        # Get spending insights
        spending_insights = get_spending_insights(state["transactions_df"])

        # Generate advice using LLM
        advice = advisor_agent(
            query=state["query"],
            context=state["retrieved_context"],
            anomalies=state["anomalies_detected"],
            spending_insights=spending_insights,
        )

        state["final_answer"] = advice

        step = {
            "agent": "Advisor (RAG)",
            "action": "Generated personalized financial advice",
            "result": "Advice generated successfully",
            "timestamp": datetime.now(),
        }
        state["reasoning_steps"].append(step)

        logger.info("[Advisor] Advice generated successfully")

    except Exception as e:
        logger.error(f"[Advisor] Failed: {e}")
        state["final_answer"] = f"Error generating advice: {str(e)}"
        state["error"] = str(e)

    return state


# ============= Conditional Edges =============


def should_run_analysis(state: WorkflowState) -> str:
    """
    Supervisor: Decide whether to run full analysis or skip to advisor.
    """
    if not state.get("sufficient_data", False):
        logger.info("[Supervisor] Insufficient data, skipping to advisor")
        return "advisor"

    logger.info("[Supervisor] Sufficient data, running full analysis")
    return "analysis"


# ============= Workflow Construction =============


def create_workflow() -> StateGraph:
    """
    Create the LangGraph workflow with all agents.

    Returns:
        Compiled StateGraph
    """
    # Initialize graph
    workflow = StateGraph(WorkflowState)

    # Add nodes
    workflow.add_node("retrieve", retrieve_context_node)
    workflow.add_node("categorize", categorization_node)
    workflow.add_node("detect_anomalies", anomaly_detection_node)
    workflow.add_node("advisor", advisor_node)

    # Define edges
    workflow.set_entry_point("retrieve")

    # Conditional routing after retrieval
    workflow.add_conditional_edges(
        "retrieve",
        should_run_analysis,
        {"analysis": "categorize", "advisor": "advisor"},
    )

    # Sequential analysis pipeline
    workflow.add_edge("categorize", "detect_anomalies")
    workflow.add_edge("detect_anomalies", "advisor")

    # End after advisor
    workflow.add_edge("advisor", END)

    # Compile
    app = workflow.compile()

    logger.info("Workflow graph created successfully")

    return app


def run_workflow(user_id: str, query: str) -> Dict[str, Any]:
    """
    Execute the complete workflow for a user query.

    Args:
        user_id: User identifier
        query: User's financial query

    Returns:
        Complete workflow results
    """
    logger.info(f"Starting workflow for user {user_id}")
    start_time = time.time()

    # Initialize state
    initial_state: WorkflowState = {
        "user_id": user_id,
        "query": query,
        "transactions_df": None,
        "retrieved_context": [],
        "context_count": 0,
        "categorization_result": None,
        "anomaly_result": None,
        "anomalies_detected": [],
        "final_answer": "",
        "reasoning_steps": [],
        "sufficient_data": False,
        "error": None,
    }

    # Create and run workflow
    app = create_workflow()
    final_state = app.invoke(initial_state)

    elapsed = time.time() - start_time

    logger.info(f"Workflow completed in {elapsed:.2f}s")

    return {
        "success": final_state.get("error") is None,
        "user_id": user_id,
        "query": query,
        "reasoning_steps": final_state["reasoning_steps"],
        "final_answer": final_state["final_answer"],
        "anomalies_detected": final_state["anomalies_detected"],
        "context_retrieved": final_state["context_count"],
        "processing_time_seconds": round(elapsed, 2),
    }
