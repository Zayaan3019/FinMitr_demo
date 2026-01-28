"""
RAG Advisor Agent - Uses LLM to provide financial advice.
Grounds responses in retrieved transaction context.
"""

from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm import get_llm
from app.core.logging import get_logger

logger = get_logger(__name__)


SYSTEM_PROMPT = """You are FinGuru, an expert AI financial advisor specializing in personal finance management.

Your role is to:
1. Analyze user transaction data and spending patterns
2. Provide actionable financial advice based on their specific situation
3. Highlight anomalies and unusual spending patterns
4. Offer budgeting recommendations and savings strategies
5. Explain financial concepts in simple, understandable terms

Guidelines:
- Always ground your advice in the provided transaction context
- Be specific with numbers and percentages when analyzing spending
- Provide actionable, practical recommendations
- Maintain a professional yet friendly tone
- If data is insufficient, clearly state limitations
- Focus on helping users make better financial decisions

Format your response with:
1. **Summary**: Brief overview of their financial situation
2. **Key Insights**: Important patterns or findings
3. **Anomalies**: Any unusual transactions detected
4. **Recommendations**: Specific actionable advice
"""


def advisor_agent(
    query: str,
    context: List[str],
    anomalies: List[Dict[str, Any]],
    spending_insights: Dict[str, Any],
) -> str:
    """
    RAG-powered advisor agent that generates financial advice.

    Args:
        query: User's question/query
        context: Retrieved transaction context
        anomalies: Detected anomalous transactions
        spending_insights: Statistical spending insights

    Returns:
        LLM-generated financial advice
    """
    logger.info("Starting advisor agent...")

    try:
        # Build context string
        context_str = "\n\n".join(
            [
                f"Transaction {i+1}: {ctx}"
                for i, ctx in enumerate(context[:15])  # Limit to top 15
            ]
        )

        # Build anomalies string
        if anomalies:
            anomalies_str = "\n".join(
                [
                    f"- {a['date']}: ${abs(a['amount']):.2f} at {a['description']} (Score: {a['anomaly_score']:.3f})"
                    for a in anomalies[:5]
                ]
            )
        else:
            anomalies_str = "No significant anomalies detected."

        # Build insights string
        insights_str = f"""
Total Spent: ${spending_insights.get('total_spent', 0):.2f}
Average Transaction: ${spending_insights.get('average_transaction', 0):.2f}
Largest Expense: ${spending_insights.get('largest_expense', 0):.2f}

Top Spending Categories:
"""
        for category, amount in spending_insights.get("top_categories", {}).items():
            insights_str += f"- {category}: ${amount:.2f}\n"

        # Construct user message
        user_message = f"""
User Query: {query}

=== TRANSACTION CONTEXT ===
{context_str}

=== SPENDING INSIGHTS ===
{insights_str}

=== DETECTED ANOMALIES ===
{anomalies_str}

Please provide comprehensive financial advice based on the above data.
"""

        # Invoke LLM
        llm = get_llm()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]

        response = llm.invoke(messages)
        advice = response.content

        logger.info("Advisor agent completed successfully")

        return advice

    except Exception as e:
        logger.error(f"Advisor agent failed: {e}")
        return f"I apologize, but I encountered an error while analyzing your financial data: {str(e)}"


def insufficient_data_response(query: str) -> str:
    """
    Generate response when insufficient data is available.

    Args:
        query: User's query

    Returns:
        Helpful response about data availability
    """
    return f"""
I appreciate your question: "{query}"

However, I currently have insufficient transaction data in your account to provide accurate financial advice. 

To get started:
1. Upload your transaction data using the /ingest endpoint
2. Ensure your CSV includes: date, amount, description, and category columns
3. Include at least 30-60 days of transaction history for meaningful insights

Once your data is loaded, I'll be able to:
- Analyze your spending patterns
- Detect unusual transactions
- Provide personalized budgeting advice
- Track your financial progress

Please upload your transaction data and try again!
"""
