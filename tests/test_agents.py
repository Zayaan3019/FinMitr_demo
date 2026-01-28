"""
Unit tests for agent functions.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.agents.categorization import categorization_agent, categorize_transaction
from app.agents.anomaly_detection import anomaly_detection_agent, get_spending_insights
from app.agents.advisor import insufficient_data_response


class TestCategorizationAgent:
    """Test categorization agent."""
    
    def test_categorize_groceries(self):
        """Test categorizing grocery transactions."""
        assert categorize_transaction("Whole Foods Market") == "Groceries"
        assert categorize_transaction("Trader Joe's") == "Groceries"
        assert categorize_transaction("Costco Wholesale") == "Groceries"
    
    def test_categorize_utilities(self):
        """Test categorizing utility transactions."""
        assert categorize_transaction("PG&E Electric Bill") == "Utilities"
        assert categorize_transaction("Comcast Internet") == "Utilities"
    
    def test_categorize_transportation(self):
        """Test categorizing transportation."""
        assert categorize_transaction("Shell Gas Station") == "Transportation"
        assert categorize_transaction("Uber Ride") == "Transportation"
    
    def test_categorize_unknown(self):
        """Test unknown category defaults to Other."""
        result = categorize_transaction("Random Unknown Store XYZ123")
        assert result == "Other"
    
    def test_categorization_agent_no_uncategorized(self):
        """Test agent with all categorized transactions."""
        df = pd.DataFrame({
            "date": [datetime.now()] * 3,
            "amount": [-100, -200, -300],
            "description": ["Store A", "Store B", "Store C"],
            "category": ["Groceries", "Dining", "Shopping"]
        })
        
        result = categorization_agent(df)
        assert result["categorized_count"] == 0
        assert "already categorized" in result["message"].lower()
    
    def test_categorization_agent_with_uncategorized(self):
        """Test agent with uncategorized transactions."""
        df = pd.DataFrame({
            "date": [datetime.now()] * 3,
            "amount": [-100, -200, -300],
            "description": ["Whole Foods", "Starbucks", "Unknown Store"],
            "category": ["", None, ""]
        })
        
        result = categorization_agent(df)
        assert result["categorized_count"] == 3
        assert "Groceries" in result["category_distribution"]


class TestAnomalyDetectionAgent:
    """Test anomaly detection agent."""
    
    def test_insufficient_data(self):
        """Test with insufficient data."""
        df = pd.DataFrame({
            "date": [datetime.now()] * 5,
            "amount": [-100, -110, -105, -108, -102],
            "description": ["Store"] * 5,
            "category": ["Groceries"] * 5
        })
        
        result = anomaly_detection_agent(df)
        assert "Insufficient data" in result["message"]
        assert result["anomaly_count"] == 0
    
    def test_anomaly_detection_normal_data(self):
        """Test with normal spending patterns."""
        dates = [datetime.now() - timedelta(days=i) for i in range(50)]
        amounts = np.random.normal(-100, 20, 50)
        
        df = pd.DataFrame({
            "date": dates,
            "amount": amounts,
            "description": ["Store"] * 50,
            "category": ["Groceries"] * 50
        })
        
        result = anomaly_detection_agent(df)
        assert result["anomaly_count"] >= 0
        assert result["total_transactions"] == 50
        assert "anomaly_percentage" in result
    
    def test_anomaly_detection_with_outliers(self):
        """Test with obvious outliers."""
        dates = [datetime.now() - timedelta(days=i) for i in range(50)]
        amounts = [-100] * 45 + [-5000, -4500, -6000, -4800, -5200]  # Outliers
        
        df = pd.DataFrame({
            "date": dates,
            "amount": amounts,
            "description": ["Store"] * 50,
            "category": ["Groceries"] * 50
        })
        
        result = anomaly_detection_agent(df)
        assert result["anomaly_count"] > 0
        assert len(result["anomalies_detected"]) > 0


class TestSpendingInsights:
    """Test spending insights generation."""
    
    def test_get_spending_insights(self):
        """Test generating spending insights."""
        df = pd.DataFrame({
            "date": [datetime.now()] * 10,
            "amount": [-100, -200, -150, -80, -120, -300, -90, -110, -180, -140],
            "description": ["Store"] * 10,
            "category": ["Groceries"] * 5 + ["Dining"] * 5
        })
        
        insights = get_spending_insights(df)
        
        assert "total_spent" in insights
        assert "average_transaction" in insights
        assert "largest_expense" in insights
        assert "top_categories" in insights
        assert insights["total_spent"] > 0
    
    def test_insights_no_expenses(self):
        """Test insights with no expenses."""
        df = pd.DataFrame({
            "date": [datetime.now()] * 5,
            "amount": [1000, 2000, 1500, 1800, 1200],  # All income
            "description": ["Salary"] * 5,
            "category": ["Salary"] * 5
        })
        
        insights = get_spending_insights(df)
        assert "message" in insights or insights["total_spent"] == 0


class TestAdvisorAgent:
    """Test advisor agent."""
    
    def test_insufficient_data_response(self):
        """Test insufficient data response generation."""
        query = "What are my expenses?"
        response = insufficient_data_response(query)
        
        assert query in response
        assert "insufficient" in response.lower()
        assert "upload" in response.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
