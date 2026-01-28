"""
Budget Management Agent - Creates and monitors budgets.
"""

import pandas as pd
from typing import Dict, Any, List
from datetime import datetime
from app.core.logging import get_logger

logger = get_logger(__name__)


class BudgetManager:
    """Manages budget creation, tracking, and alerts."""

    def __init__(self):
        """Initialize budget manager."""
        self.budgets: Dict[str, Dict[str, Any]] = {}

    def create_budget(
        self,
        user_id: str,
        category: str,
        monthly_limit: float,
        start_date: datetime = None,
    ) -> Dict[str, Any]:
        """
        Create a budget for a category.

        Args:
            user_id: User identifier
            category: Transaction category
            monthly_limit: Monthly spending limit
            start_date: Budget start date

        Returns:
            Budget creation result
        """
        if start_date is None:
            start_date = datetime.now()

        budget_id = f"{user_id}_{category}_{start_date.strftime('%Y%m')}"

        self.budgets[budget_id] = {
            "user_id": user_id,
            "category": category,
            "monthly_limit": monthly_limit,
            "start_date": start_date,
            "current_spending": 0,
            "status": "active",
        }

        logger.info(f"Created budget: {budget_id} with limit ${monthly_limit}")

        return {
            "success": True,
            "budget_id": budget_id,
            "category": category,
            "monthly_limit": monthly_limit,
            "message": f"Budget created for {category}: ${monthly_limit}/month",
        }

    def check_budget_status(
        self, user_id: str, transactions_df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Check budget status against actual spending.

        Args:
            user_id: User identifier
            transactions_df: DataFrame with transactions

        Returns:
            Budget status report
        """
        if len(transactions_df) == 0:
            return {"message": "No transactions to analyze"}

        # Get current month transactions
        df = transactions_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        current_month = datetime.now().month
        current_year = datetime.now().year

        df_current = df[
            (df["date"].dt.month == current_month)
            & (df["date"].dt.year == current_year)
            & (df["amount"] < 0)  # Only expenses
        ]

        # Calculate spending by category
        category_spending = df_current.groupby("category")["amount"].sum().abs()

        # Check against budgets
        alerts = []
        budget_status = []

        for budget_id, budget in self.budgets.items():
            if budget["user_id"] != user_id:
                continue

            category = budget["category"]
            limit = budget["monthly_limit"]
            spent = category_spending.get(category, 0)
            remaining = limit - spent
            percentage = (spent / limit * 100) if limit > 0 else 0

            status = {
                "category": category,
                "budget": limit,
                "spent": float(spent),
                "remaining": float(remaining),
                "percentage": round(percentage, 1),
                "status": (
                    "on_track"
                    if percentage < 80
                    else "warning" if percentage < 100 else "exceeded"
                ),
            }

            budget_status.append(status)

            # Generate alerts
            if percentage >= 100:
                alerts.append(f"⚠️ EXCEEDED: {category} budget by ${spent - limit:.2f}")
            elif percentage >= 80:
                alerts.append(f"⚠️ WARNING: {category} at {percentage:.0f}% of budget")

        return {
            "budget_status": budget_status,
            "alerts": alerts,
            "total_budgets": len(budget_status),
            "exceeded_count": sum(
                1 for s in budget_status if s["status"] == "exceeded"
            ),
        }

    def suggest_budgets(self, transactions_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyze spending and suggest realistic budgets.

        Args:
            transactions_df: Historical transaction data

        Returns:
            Budget suggestions
        """
        if len(transactions_df) < 30:
            return {"message": "Need at least 30 days of data for suggestions"}

        df = transactions_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        # Get expenses only
        expenses = df[df["amount"] < 0].copy()
        expenses["amount_abs"] = expenses["amount"].abs()

        # Calculate average monthly spending per category
        expenses["month"] = expenses["date"].dt.to_period("M")
        monthly_category = expenses.groupby(["month", "category"])["amount_abs"].sum()
        avg_monthly = monthly_category.groupby("category").mean()

        # Suggest budgets with 10% buffer
        suggestions = []
        for category, avg_spending in avg_monthly.items():
            suggested_limit = round(avg_spending * 1.1, 2)  # 10% buffer
            suggestions.append(
                {
                    "category": category,
                    "average_monthly_spending": round(float(avg_spending), 2),
                    "suggested_budget": float(suggested_limit),
                    "reasoning": f"Based on ${avg_spending:.2f} average + 10% buffer",
                }
            )

        # Sort by spending amount
        suggestions.sort(key=lambda x: x["average_monthly_spending"], reverse=True)

        return {
            "suggestions": suggestions,
            "total_suggested_budget": sum(s["suggested_budget"] for s in suggestions),
            "based_on_months": len(monthly_category.index.get_level_values(0).unique()),
        }


def budget_agent(user_id: str, transactions_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Budget analysis agent.

    Args:
        user_id: User identifier
        transactions_df: User transactions

    Returns:
        Budget analysis results
    """
    logger.info(f"Starting budget agent for user {user_id}...")

    try:
        manager = BudgetManager()

        # Get budget suggestions
        suggestions = manager.suggest_budgets(transactions_df)

        return {
            "success": True,
            "budget_suggestions": suggestions.get("suggestions", []),
            "total_suggested_budget": suggestions.get("total_suggested_budget", 0),
            "message": f"Generated {len(suggestions.get('suggestions', []))} budget suggestions",
        }

    except Exception as e:
        logger.error(f"Budget agent failed: {e}")
        return {"success": False, "error": str(e)}
