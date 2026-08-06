"""
Node implementations for Self-Correcting Causal Financial Agent.

This module implements the four core nodes of the causal verification loop:
1. market_analyst: Generates hypotheses with vector store context
2. causal_validator: Performs statistical causality tests
3. risk_critic: Evaluates hypothesis against evidence
4. hypothesis_refiner: Refines rejected hypotheses

All nodes are async and include comprehensive error handling.
"""

import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import json
import numpy as np
from scipy import stats
from statsmodels.tsa.stattools import grangercausalitytests
from statsmodels.tsa.stattools import adfuller

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from app.agents.causal.state import (
    AgentState,
    HypothesisModel,
    CausalEvidenceModel,
    CritiqueModel,
    serialize_hypothesis,
    serialize_evidence,
    serialize_critique,
    deserialize_hypothesis,
)
from app.core.llm import LLMManager
from app.db.vector_store import get_vector_store
from app.core.logging import get_logger

logger = get_logger(__name__)

# Initialize LLM manager
llm_manager = LLMManager()


# ============= Node 1: Market Analyst =============


async def market_analyst(state: AgentState) -> AgentState:
    """
    Node 1: Generate initial trading hypothesis based on user query.

    This node:
    1. Retrieves relevant financial context from the vector store
    2. Prompts the LLM to generate a structured hypothesis
    3. Validates and stores the hypothesis in state

    Args:
        state: Current AgentState with user query

    Returns:
        Updated AgentState with hypothesis and retrieved context

    Raises:
        Exception: Logged and stored in state.error if critical failure occurs
    """
    logger.info(f"[market_analyst] Starting for query: {state['query'][:50]}...")

    try:
        # Step 1: Retrieve financial context from vector store
        vector_store = get_vector_store()
        retrieval_results = vector_store.retrieve_context(
            user_id=state["user_id"], query=state["query"], top_k=10
        )

        retrieved_docs = retrieval_results.get("documents", [])
        state["retrieved_context"] = retrieved_docs

        logger.info(f"[market_analyst] Retrieved {len(retrieved_docs)} context documents")

        # Step 2: Build context-aware prompt
        context_str = "\n".join([f"- {doc}" for doc in retrieved_docs[:5]])

        system_prompt = """You are an expert financial analyst specializing in market causality.
Your task is to generate a testable hypothesis about market relationships based on the user's query.

Guidelines:
1. Identify exactly TWO market variables (e.g., "Oil Price", "Airline Stocks")
2. Specify the expected relationship: direct, inverse, or neutral
3. Make the hypothesis specific and testable
4. Provide initial confidence based on market theory

Output ONLY valid JSON matching this schema:
{
    "claim": "Clear statement of the hypothesis",
    "variables": ["Variable1", "Variable2"],
    "relationship": "direct|inverse|neutral",
    "confidence": 0.7
}

Example output for "Oil prices affect airline stocks":
{
    "claim": "Rising oil prices lead to declining airline stock prices due to increased operating costs",
    "variables": ["Oil Price", "Airline Stocks"],
    "relationship": "inverse",
    "confidence": 0.75
}"""

        human_prompt = f"""User Query: {state['query']}

Relevant Financial Context:
{context_str if context_str else "No specific historical context available"}

Generate a structured hypothesis as JSON:"""

        # Step 3: Invoke LLM
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

        response = llm_manager.invoke(messages)
        logger.info(f"[market_analyst] LLM response received: {response[:100]}...")

        # Step 4: Parse and validate response
        try:
            # Extract JSON from response (handle markdown code blocks)
            response_clean = response.strip()
            if response_clean.startswith("```"):
                # Remove markdown code block markers
                lines = response_clean.split("\n")
                response_clean = "\n".join([l for l in lines if not l.startswith("```")])

            hypothesis_data = json.loads(response_clean)
            hypothesis = HypothesisModel(**hypothesis_data)

            state["hypothesis"] = serialize_hypothesis(hypothesis)
            logger.info(f"[market_analyst] Hypothesis generated: {hypothesis.claim[:80]}...")

        except json.JSONDecodeError as e:
            logger.error(f"[market_analyst] Failed to parse LLM response as JSON: {e}")
            # Fallback: create generic hypothesis
            hypothesis = HypothesisModel(
                claim=f"Market relationship analysis for: {state['query']}",
                variables=["Market Factor A", "Market Factor B"],
                relationship="direct",
                confidence=0.5,
            )
            state["hypothesis"] = serialize_hypothesis(hypothesis)

        except Exception as e:
            logger.error(f"[market_analyst] Failed to validate hypothesis: {e}")
            raise

        # Step 5: Add reasoning step
        state["reasoning_steps"].append(
            {
                "node": "market_analyst",
                "action": "Generated initial hypothesis",
                "details": {
                    "claim": hypothesis.claim,
                    "variables": hypothesis.variables,
                    "confidence": hypothesis.confidence,
                },
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"[market_analyst] Critical error: {e}")
        state["error"] = f"market_analyst failed: {str(e)}"
        state["status"] = "rejected"

    return state


# ============= Node 2: Causal Validator =============


def _perform_pearson_correlation(series1: np.ndarray, series2: np.ndarray) -> Tuple[float, float]:
    """
    Calculate Pearson correlation coefficient and p-value.

    Args:
        series1: First time series
        series2: Second time series

    Returns:
        Tuple of (correlation coefficient, p-value)
    """
    correlation, p_value = stats.pearsonr(series1, series2)
    return float(correlation), float(p_value)


def _perform_granger_causality(
    series1: np.ndarray, series2: np.ndarray, max_lag: int = 5
) -> Dict[str, Any]:
    """
    Perform Granger causality test.

    Tests if series1 Granger-causes series2 (i.e., past values of series1
    help predict future values of series2).

    Args:
        series1: Predictor time series
        series2: Target time series
        max_lag: Maximum lag order to test

    Returns:
        Dictionary with test results including p-values for each lag
    """
    try:
        # Prepare data for Granger test (combine series)
        data = np.column_stack([series2, series1])

        # Perform test
        test_result = grangercausalitytests(data, max_lag, verbose=False)

        # Extract p-values for each lag
        p_values = {}
        for lag in range(1, max_lag + 1):
            ssr_ftest = test_result[lag][0]["ssr_ftest"]
            p_values[f"lag_{lag}"] = float(ssr_ftest[1])

        # Overall result: if any lag has p < 0.05, causality is suggested
        min_p_value = min(p_values.values())
        granger_significant = min_p_value < 0.05

        return {
            "p_values": p_values,
            "min_p_value": min_p_value,
            "granger_causality_detected": granger_significant,
        }

    except Exception as e:
        logger.warning(f"Granger causality test failed: {e}")
        return {"error": str(e), "granger_causality_detected": False}


def _generate_mock_time_series(
    variable_name: str, length: int = 100, relationship: str = "inverse"
) -> np.ndarray:
    """
    Generate mock time series data for testing.

    In production, this would be replaced by actual market data retrieval.

    Args:
        variable_name: Name of the variable (for seeding)
        length: Number of data points
        relationship: Type of relationship to simulate

    Returns:
        Numpy array of simulated time series data
    """
    np.random.seed(hash(variable_name) % 2**32)

    # Generate base trend
    trend = np.linspace(100, 120, length)

    # Add noise
    noise = np.random.normal(0, 5, length)

    # Add cyclical component
    cycle = 10 * np.sin(np.linspace(0, 4 * np.pi, length))

    series = trend + noise + cycle

    return series


async def causal_validator(state: AgentState) -> AgentState:
    """
    Node 2: Validate hypothesis using statistical causality tests.

    This node:
    1. Extracts variables from hypothesis
    2. Retrieves or generates time series data (mocked for now)
    3. Performs Pearson correlation and Granger causality tests
    4. Interprets results and determines if evidence supports hypothesis

    Args:
        state: AgentState with hypothesis to validate

    Returns:
        Updated AgentState with causal evidence
    """
    logger.info("[causal_validator] Starting validation...")

    try:
        if not state["hypothesis"]:
            raise ValueError("No hypothesis to validate")

        hypothesis = deserialize_hypothesis(state["hypothesis"])

        # Step 1: Extract variables
        if len(hypothesis.variables) < 2:
            raise ValueError("Hypothesis must have at least 2 variables")

        var1_name = hypothesis.variables[0]
        var2_name = hypothesis.variables[1]

        logger.info(f"[causal_validator] Testing causality: {var1_name} -> {var2_name}")

        # Step 2: Retrieve or generate time series data
        # TODO: In production, replace with actual market data API call
        series1 = _generate_mock_time_series(var1_name, length=100)
        series2 = _generate_mock_time_series(
            var2_name, length=100, relationship=hypothesis.relationship
        )

        # For inverse relationship, invert series2
        if hypothesis.relationship == "inverse":
            series2 = 150 - series2

        logger.info(f"[causal_validator] Generated time series: {len(series1)} data points")

        # Step 3: Perform statistical tests
        # Pearson correlation
        correlation, pearson_p = _perform_pearson_correlation(series1, series2)

        # Granger causality
        granger_results = _perform_granger_causality(series1, series2, max_lag=5)

        logger.info(f"[causal_validator] Correlation: {correlation:.3f}, p={pearson_p:.4f}")
        logger.info(
            f"[causal_validator] Granger: {granger_results.get('granger_causality_detected')}"
        )

        # Step 4: Interpret results
        pearson_significant = pearson_p < 0.05
        granger_significant = granger_results.get("granger_causality_detected", False)

        # Check if correlation direction matches hypothesis
        if hypothesis.relationship == "direct":
            direction_matches = correlation > 0.3
        elif hypothesis.relationship == "inverse":
            direction_matches = correlation < -0.3
        else:  # neutral
            direction_matches = abs(correlation) < 0.3

        # Overall support determination
        supports_hypothesis = pearson_significant and direction_matches and granger_significant

        # Generate interpretation
        if supports_hypothesis:
            interpretation = (
                f"Strong statistical evidence supports the hypothesis. "
                f"Correlation: {correlation:.3f} (p={pearson_p:.4f}), "
                f"Granger causality detected with min p-value: {granger_results.get('min_p_value', 1.0):.4f}"
            )
            confidence_adj = 0.3
        elif pearson_significant and direction_matches:
            interpretation = (
                f"Moderate evidence: Correlation is significant ({correlation:.3f}, p={pearson_p:.4f}), "
                f"but Granger causality not confirmed."
            )
            confidence_adj = 0.1
            supports_hypothesis = True  # Partial support
        else:
            interpretation = (
                f"Evidence does NOT support hypothesis. "
                f"Correlation: {correlation:.3f} (p={pearson_p:.4f}), "
                f"Direction match: {direction_matches}, "
                f"Granger causality: {granger_significant}"
            )
            confidence_adj = -0.4

        # Step 5: Create evidence model
        evidence = CausalEvidenceModel(
            test_type="both",
            p_value=pearson_p,
            correlation=correlation,
            granger_causality=granger_results,
            interpretation=interpretation,
            supports_hypothesis=supports_hypothesis,
            confidence_adjustment=confidence_adj,
        )

        state["causal_evidence"] = serialize_evidence(evidence)

        # Step 6: Add reasoning step
        state["reasoning_steps"].append(
            {
                "node": "causal_validator",
                "action": "Performed statistical validation",
                "details": {
                    "correlation": correlation,
                    "pearson_p_value": pearson_p,
                    "granger_causality": granger_significant,
                    "supports_hypothesis": supports_hypothesis,
                },
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.info(f"[causal_validator] Validation complete: {supports_hypothesis}")

    except Exception as e:
        logger.error(f"[causal_validator] Error: {e}")
        state["error"] = f"causal_validator failed: {str(e)}"
        state["status"] = "rejected"

    return state


# ============= Node 3: Risk Critic =============


async def risk_critic(state: AgentState) -> AgentState:
    """
    Node 3: Evaluate hypothesis against evidence and issue verdict.

    This node:
    1. Compares hypothesis against statistical evidence
    2. Assesses evidence quality
    3. Issues verdict: verified, rejected, or inconclusive
    4. Provides detailed critique and recommended actions

    Args:
        state: AgentState with hypothesis and evidence

    Returns:
        Updated AgentState with critique and updated status
    """
    logger.info("[risk_critic] Starting critical evaluation...")

    try:
        if not state["hypothesis"] or not state["causal_evidence"]:
            raise ValueError("Missing hypothesis or evidence for critique")

        hypothesis = deserialize_hypothesis(state["hypothesis"])
        evidence = CausalEvidenceModel(**state["causal_evidence"])

        # Step 1: Build critique prompt
        system_prompt = """You are a rigorous risk management expert evaluating financial hypotheses.

Your task:
1. Compare the hypothesis claim against statistical evidence
2. Assess quality and strength of evidence
3. Issue a clear verdict: verified, rejected, or inconclusive

Be conservative: Only verify if evidence is strong and clearly supports the claim.

Output ONLY valid JSON matching this schema:
{
    "verdict": "verified|rejected|inconclusive",
    "reasoning": "Detailed explanation",
    "evidence_quality": "strong|moderate|weak",
    "recommended_action": "Specific next steps",
    "confidence_score": 0.75
}"""

        human_prompt = f"""Hypothesis:
Claim: {hypothesis.claim}
Variables: {', '.join(hypothesis.variables)}
Expected Relationship: {hypothesis.relationship}
Initial Confidence: {hypothesis.confidence}

Statistical Evidence:
{evidence.interpretation}
P-value: {evidence.p_value}
Correlation: {evidence.correlation}
Supports Hypothesis: {evidence.supports_hypothesis}

Provide your critique as JSON:"""

        # Step 2: Invoke LLM for critique
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

        response = llm_manager.invoke(messages)
        logger.info(f"[risk_critic] LLM critique received")

        # Step 3: Parse critique
        try:
            response_clean = response.strip()
            if response_clean.startswith("```"):
                lines = response_clean.split("\n")
                response_clean = "\n".join([l for l in lines if not l.startswith("```")])

            critique_data = json.loads(response_clean)
            critique = CritiqueModel(**critique_data)

        except json.JSONDecodeError as e:
            logger.error(f"[risk_critic] Failed to parse critique as JSON: {e}")
            # Fallback critique based on evidence
            if evidence.supports_hypothesis:
                critique = CritiqueModel(
                    verdict="verified",
                    reasoning="Statistical evidence supports the hypothesis with significant correlation and causality.",
                    evidence_quality="moderate",
                    recommended_action="Proceed with investment analysis incorporating this relationship.",
                    confidence_score=min(
                        0.95, hypothesis.confidence + evidence.confidence_adjustment
                    ),
                )
            else:
                critique = CritiqueModel(
                    verdict="rejected",
                    reasoning="Statistical evidence does not support the claimed relationship.",
                    evidence_quality="weak",
                    recommended_action="Revise hypothesis to align with observed data patterns.",
                    confidence_score=max(
                        0.1, hypothesis.confidence + evidence.confidence_adjustment
                    ),
                )

        state["critique"] = serialize_critique(critique)

        # Step 4: Update state status based on verdict
        if critique.verdict == "verified":
            state["status"] = "verified"
            state["final_output"] = (
                f"✓ VERIFIED: {hypothesis.claim}\n\nEvidence: {evidence.interpretation}\n\nConfidence: {critique.confidence_score:.2%}"
            )
        elif critique.verdict == "rejected":
            state["status"] = "rejected"
        else:  # inconclusive
            state["status"] = "rejected"  # Treat inconclusive as rejected for refinement

        # Step 5: Add reasoning step
        state["reasoning_steps"].append(
            {
                "node": "risk_critic",
                "action": f"Issued verdict: {critique.verdict}",
                "details": {
                    "verdict": critique.verdict,
                    "evidence_quality": critique.evidence_quality,
                    "confidence_score": critique.confidence_score,
                },
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.info(f"[risk_critic] Verdict: {critique.verdict}")

    except Exception as e:
        logger.error(f"[risk_critic] Error: {e}")
        state["error"] = f"risk_critic failed: {str(e)}"
        state["status"] = "rejected"

    return state


# ============= Node 4: Hypothesis Refiner =============


async def hypothesis_refiner(state: AgentState) -> AgentState:
    """
    Node 4: Refine rejected hypothesis based on critique.

    This node:
    1. Analyzes the critique feedback
    2. Generates a revised hypothesis addressing the issues
    3. Increments iteration count
    4. Prepares state for re-validation

    Args:
        state: AgentState with rejected hypothesis and critique

    Returns:
        Updated AgentState with refined hypothesis
    """
    logger.info("[hypothesis_refiner] Starting hypothesis refinement...")

    try:
        if not state["hypothesis"] or not state["critique"]:
            raise ValueError("Missing hypothesis or critique for refinement")

        # Increment iteration count
        state["iteration_count"] += 1

        # Check max iterations
        if state["iteration_count"] > 3:
            logger.warning("[hypothesis_refiner] Max iterations reached")
            state["status"] = "max_iterations"
            state["final_output"] = (
                f"Unable to verify hypothesis after {state['iteration_count']} iterations. "
                f"Original query: {state['query']}"
            )
            return state

        hypothesis = deserialize_hypothesis(state["hypothesis"])
        critique = CritiqueModel(**state["critique"])
        evidence = CausalEvidenceModel(**state["causal_evidence"])

        # Step 1: Build refinement prompt
        system_prompt = """You are an expert financial analyst tasked with refining hypotheses.

Given a rejected hypothesis and critique, generate an IMPROVED hypothesis that:
1. Addresses the critique's concerns
2. Aligns with the statistical evidence
3. Maintains testability

Output ONLY valid JSON matching this schema:
{
    "claim": "Refined hypothesis statement",
    "variables": ["Variable1", "Variable2"],
    "relationship": "direct|inverse|neutral",
    "confidence": 0.6
}"""

        human_prompt = f"""Original Hypothesis:
{hypothesis.claim}
Variables: {', '.join(hypothesis.variables)}
Relationship: {hypothesis.relationship}

Critique:
Verdict: {critique.verdict}
Reasoning: {critique.reasoning}
Recommended Action: {critique.recommended_action}

Statistical Evidence:
{evidence.interpretation}
Correlation: {evidence.correlation}

Generate a refined hypothesis as JSON:"""

        # Step 2: Invoke LLM for refinement
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

        response = llm_manager.invoke(messages)
        logger.info(f"[hypothesis_refiner] Refined hypothesis received")

        # Step 3: Parse refined hypothesis
        try:
            response_clean = response.strip()
            if response_clean.startswith("```"):
                lines = response_clean.split("\n")
                response_clean = "\n".join([l for l in lines if not l.startswith("```")])

            refined_data = json.loads(response_clean)
            refined_hypothesis = HypothesisModel(**refined_data)

            state["hypothesis"] = serialize_hypothesis(refined_hypothesis)
            logger.info(f"[hypothesis_refiner] Refined: {refined_hypothesis.claim[:80]}...")

        except json.JSONDecodeError as e:
            logger.error(f"[hypothesis_refiner] Failed to parse refined hypothesis: {e}")
            # Fallback: adjust relationship type
            new_relationship = "inverse" if hypothesis.relationship == "direct" else "direct"
            refined_hypothesis = HypothesisModel(
                claim=f"Revised: {hypothesis.claim} (relationship: {new_relationship})",
                variables=hypothesis.variables,
                relationship=new_relationship,
                confidence=0.5,
            )
            state["hypothesis"] = serialize_hypothesis(refined_hypothesis)

        # Step 4: Reset status for re-validation
        state["status"] = "pending"
        state["causal_evidence"] = None  # Clear old evidence

        # Step 5: Add reasoning step
        state["reasoning_steps"].append(
            {
                "node": "hypothesis_refiner",
                "action": f"Refined hypothesis (iteration {state['iteration_count']})",
                "details": {
                    "original_claim": hypothesis.claim,
                    "refined_claim": refined_hypothesis.claim,
                    "iteration": state["iteration_count"],
                },
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.info(
            f"[hypothesis_refiner] Refinement complete (iteration {state['iteration_count']})"
        )

    except Exception as e:
        logger.error(f"[hypothesis_refiner] Error: {e}")
        state["error"] = f"hypothesis_refiner failed: {str(e)}"
        state["status"] = "max_iterations"

    return state
