"""
Anomaly Detection Agent using Isolation Forest.
Detects unusual spending patterns and outliers.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from app.core.logging import get_logger

logger = get_logger(__name__)


def anomaly_detection_agent(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Anomaly detection agent using Isolation Forest algorithm.

    Args:
        df: DataFrame with user transactions

    Returns:
        Dictionary with anomaly detection results
    """
    logger.info("Starting anomaly detection agent...")

    try:
        if len(df) < 10:
            logger.warning("Insufficient data for anomaly detection")
            return {
                "anomalies_detected": [],
                "anomaly_count": 0,
                "message": "Insufficient data for anomaly detection (need at least 10 transactions)",
            }

        # Prepare features for anomaly detection
        # Use absolute amount and day of week
        df_copy = df.copy()
        df_copy["date"] = pd.to_datetime(df_copy["date"])
        df_copy["amount_abs"] = df_copy["amount"].abs()
        df_copy["day_of_week"] = df_copy["date"].dt.dayofweek

        # Create feature matrix
        features = df_copy[["amount_abs", "day_of_week"]].values

        # Standardize features
        scaler = StandardScaler()
        features_scaled = scaler.fit_transform(features)

        # Train Isolation Forest
        # contamination = expected proportion of outliers (5%)
        iso_forest = IsolationForest(
            contamination=0.05, random_state=42, n_estimators=100
        )

        # Predict anomalies (-1 = anomaly, 1 = normal)
        predictions = iso_forest.fit_predict(features_scaled)
        anomaly_scores = iso_forest.score_samples(features_scaled)

        # Identify anomalies
        anomaly_mask = predictions == -1
        anomaly_df = df_copy[anomaly_mask].copy()
        anomaly_df["anomaly_score"] = anomaly_scores[anomaly_mask]

        # Format anomalies for output
        anomalies = []
        for _, row in anomaly_df.iterrows():
            anomalies.append(
                {
                    "date": str(row["date"]),
                    "amount": float(row["amount"]),
                    "description": row["description"],
                    "category": row.get("category", "Unknown"),
                    "anomaly_score": float(row["anomaly_score"]),
                    "reason": f"Unusual ${abs(row['amount']):.2f} transaction",
                }
            )

        # Sort by anomaly score (most anomalous first)
        anomalies.sort(key=lambda x: x["anomaly_score"])

        logger.info(f"Detected {len(anomalies)} anomalous transactions")

        return {
            "anomalies_detected": anomalies[:10],  # Top 10 anomalies
            "anomaly_count": len(anomalies),
            "total_transactions": len(df),
            "anomaly_percentage": round(len(anomalies) / len(df) * 100, 2),
            "message": f"Detected {len(anomalies)} unusual transactions",
        }

    except Exception as e:
        logger.error(f"Anomaly detection failed: {e}")
        return {"anomalies_detected": [], "anomaly_count": 0, "error": str(e)}


def get_spending_insights(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Generate additional spending insights.

    Args:
        df: DataFrame with user transactions

    Returns:
        Dictionary with spending insights
    """
    try:
        # Filter expenses (negative amounts)
        expenses = df[df["amount"] < 0].copy()

        if len(expenses) == 0:
            return {"message": "No expense data available"}

        # Category-wise spending
        category_spending = expenses.groupby("category")["amount"].sum().abs()
        top_categories = category_spending.nlargest(5).to_dict()

        # Time-based patterns
        expenses["date"] = pd.to_datetime(expenses["date"])
        monthly_spending = (
            expenses.groupby(expenses["date"].dt.to_period("M"))["amount"].sum().abs()
        )

        # Statistics
        total_spent = expenses["amount"].sum()
        avg_transaction = expenses["amount"].mean()
        max_transaction = expenses["amount"].min()  # Most negative (largest expense)

        return {
            "total_spent": float(abs(total_spent)),
            "average_transaction": float(abs(avg_transaction)),
            "largest_expense": float(abs(max_transaction)),
            "top_categories": {k: float(v) for k, v in top_categories.items()},
            "monthly_average": (
                float(monthly_spending.mean()) if len(monthly_spending) > 0 else 0
            ),
        }

    except Exception as e:
        logger.error(f"Failed to generate insights: {e}")
        return {"error": str(e)}
