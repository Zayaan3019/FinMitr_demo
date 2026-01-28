"""
Categorization Agent for uncategorized transactions.
Uses pattern matching and zero-shot classification.
"""

import pandas as pd
import re
from typing import Dict, Any, List
from app.core.logging import get_logger

logger = get_logger(__name__)


# Keyword patterns for automatic categorization
CATEGORY_PATTERNS = {
    "Groceries": [
        r"whole foods",
        r"trader joe",
        r"safeway",
        r"costco",
        r"walmart",
        r"target",
        r"kroger",
        r"grocery",
        r"market",
    ],
    "Utilities": [
        r"pg&e",
        r"electric",
        r"water",
        r"gas company",
        r"internet",
        r"comcast",
        r"verizon",
        r"at&t",
        r"utility",
    ],
    "Transportation": [
        r"shell",
        r"chevron",
        r"uber",
        r"lyft",
        r"gas station",
        r"parking",
        r"transit",
        r"car insurance",
        r"vehicle",
    ],
    "Entertainment": [
        r"netflix",
        r"spotify",
        r"theater",
        r"cinema",
        r"amc",
        r"concert",
        r"steam",
        r"playstation",
        r"entertainment",
    ],
    "Healthcare": [
        r"cvs",
        r"walgreens",
        r"pharmacy",
        r"doctor",
        r"dental",
        r"hospital",
        r"medical",
        r"health insurance",
    ],
    "Shopping": [
        r"amazon",
        r"best buy",
        r"home depot",
        r"ikea",
        r"nike",
        r"apple store",
        r"mall",
        r"shop",
    ],
    "Dining": [
        r"starbucks",
        r"chipotle",
        r"mcdonald",
        r"restaurant",
        r"cafe",
        r"pizza",
        r"doordash",
        r"grubhub",
        r"uber eats",
    ],
    "Salary": [r"direct deposit", r"payroll", r"salary", r"employer"],
    "Investment": [
        r"fidelity",
        r"vanguard",
        r"401k",
        r"robinhood",
        r"investment",
        r"crypto",
        r"coinbase",
        r"stock",
    ],
    "Rent": [r"rent payment", r"apartment", r"housing"],
    "Insurance": [r"insurance premium", r"life insurance", r"auto insurance"],
}


def categorize_transaction(description: str) -> str:
    """
    Categorize a single transaction based on description.

    Args:
        description: Transaction description text

    Returns:
        Predicted category or "Other"
    """
    description_lower = description.lower()

    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, description_lower):
                return category

    return "Other"


def categorization_agent(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Categorization agent that tags uncategorized transactions.

    Args:
        df: DataFrame with transactions

    Returns:
        Dictionary with categorization results
    """
    logger.info("Starting categorization agent...")

    try:
        # Find uncategorized transactions
        uncategorized = df[df["category"].isna() | (df["category"] == "")]

        if len(uncategorized) == 0:
            logger.info("All transactions already categorized")
            return {
                "categorized_count": 0,
                "total_transactions": len(df),
                "message": "All transactions are already categorized",
            }

        # Categorize each transaction
        categories_assigned = []
        for idx, row in uncategorized.iterrows():
            predicted_category = categorize_transaction(row["description"])
            df.at[idx, "category"] = predicted_category
            categories_assigned.append(predicted_category)

        # Statistics
        category_distribution = pd.Series(categories_assigned).value_counts().to_dict()

        logger.info(
            f"Categorized {len(uncategorized)} transactions: {category_distribution}"
        )

        return {
            "categorized_count": len(uncategorized),
            "total_transactions": len(df),
            "category_distribution": category_distribution,
            "message": f"Successfully categorized {len(uncategorized)} transactions",
        }

    except Exception as e:
        logger.error(f"Categorization failed: {e}")
        return {"categorized_count": 0, "error": str(e)}
