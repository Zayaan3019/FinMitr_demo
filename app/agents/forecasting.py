"""
Financial Forecasting Agent - Predicts future spending patterns.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List
from datetime import datetime, timedelta
from app.core.logging import get_logger

logger = get_logger(__name__)


class FinancialForecaster:
    """Forecasts future financial trends."""

    def forecast_spending(
        self, transactions_df: pd.DataFrame, months_ahead: int = 3
    ) -> Dict[str, Any]:
        """
        Forecast spending for next N months using time series analysis.

        Args:
            transactions_df: Historical transactions
            months_ahead: Number of months to forecast

        Returns:
            Spending forecast
        """
        if len(transactions_df) < 60:
            return {
                "success": False,
                "message": "Need at least 60 days of data for forecasting",
            }

        df = transactions_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        # Get expenses only
        expenses = df[df["amount"] < 0].copy()
        expenses["amount_abs"] = expenses["amount"].abs()

        # Group by month
        expenses["month"] = expenses["date"].dt.to_period("M")
        monthly_spending = expenses.groupby("month")["amount_abs"].sum()

        # Simple moving average forecast
        window = min(3, len(monthly_spending))
        avg_spending = monthly_spending.rolling(window=window).mean().iloc[-1]

        # Calculate trend
        if len(monthly_spending) >= 3:
            recent_trend = (monthly_spending.iloc[-1] - monthly_spending.iloc[-3]) / 3
        else:
            recent_trend = 0

        # Generate forecasts
        forecasts = []
        current_date = datetime.now()

        for i in range(1, months_ahead + 1):
            forecast_month = current_date + timedelta(days=30 * i)
            forecast_amount = avg_spending + (recent_trend * i)

            # Add some confidence bounds (±15%)
            lower_bound = forecast_amount * 0.85
            upper_bound = forecast_amount * 1.15

            forecasts.append(
                {
                    "month": forecast_month.strftime("%Y-%m"),
                    "forecast": round(float(forecast_amount), 2),
                    "lower_bound": round(float(lower_bound), 2),
                    "upper_bound": round(float(upper_bound), 2),
                    "confidence": "medium",
                }
            )

        return {
            "success": True,
            "forecasts": forecasts,
            "trend": (
                "increasing"
                if recent_trend > 0
                else "decreasing" if recent_trend < 0 else "stable"
            ),
            "average_monthly_spend": round(float(avg_spending), 2),
        }

    def forecast_by_category(
        self, transactions_df: pd.DataFrame, category: str, months_ahead: int = 3
    ) -> Dict[str, Any]:
        """
        Forecast spending for a specific category.

        Args:
            transactions_df: Historical transactions
            category: Category to forecast
            months_ahead: Number of months ahead

        Returns:
            Category forecast
        """
        df = transactions_df.copy()
        category_df = df[df["category"] == category]

        if len(category_df) < 10:
            return {"success": False, "message": f"Insufficient data for {category}"}

        return self.forecast_spending(category_df, months_ahead)

    def identify_seasonal_patterns(
        self, transactions_df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Identify seasonal spending patterns.

        Args:
            transactions_df: Historical transactions

        Returns:
            Seasonal pattern analysis
        """
        if len(transactions_df) < 180:  # Need at least 6 months
            return {
                "success": False,
                "message": "Need at least 6 months of data for seasonal analysis",
            }

        df = transactions_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        expenses = df[df["amount"] < 0].copy()
        expenses["amount_abs"] = expenses["amount"].abs()
        expenses["month_num"] = expenses["date"].dt.month

        # Average spending per month (across all years)
        monthly_avg = expenses.groupby("month_num")["amount_abs"].mean()

        # Find peak and low months
        peak_month = monthly_avg.idxmax()
        low_month = monthly_avg.idxmin()

        month_names = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]

        return {
            "success": True,
            "peak_spending_month": month_names[peak_month - 1],
            "peak_amount": round(float(monthly_avg[peak_month]), 2),
            "low_spending_month": month_names[low_month - 1],
            "low_amount": round(float(monthly_avg[low_month]), 2),
            "monthly_averages": {
                month_names[i - 1]: round(float(monthly_avg.get(i, 0)), 2)
                for i in range(1, 13)
                if i in monthly_avg.index
            },
        }


def forecasting_agent(transactions_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Financial forecasting agent.

    Args:
        transactions_df: Historical transactions

    Returns:
        Forecasting results
    """
    logger.info("Starting forecasting agent...")

    try:
        forecaster = FinancialForecaster()

        # Generate spending forecast
        forecast = forecaster.forecast_spending(transactions_df, months_ahead=3)

        # Identify seasonal patterns
        seasonal = forecaster.identify_seasonal_patterns(transactions_df)

        return {
            "success": True,
            "spending_forecast": forecast,
            "seasonal_patterns": seasonal,
            "message": "Forecast generated successfully",
        }

    except Exception as e:
        logger.error(f"Forecasting agent failed: {e}")
        return {"success": False, "error": str(e)}
