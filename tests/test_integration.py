"""
End-to-end integration tests for complete workflows.
"""
import pytest
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import patch, Mock

from app.models.state import WorkflowState
from app.agents.workflow import (
    retrieve_context_node,
    categorization_node,
    anomaly_detection_node,
    advisor_node,
    should_run_analysis
)


@pytest.fixture
def sample_state():
    """Create a sample workflow state."""
    return {
        "user_id": "user_test",
        "query": "What are my spending patterns?",
        "transactions_df": None,
        "retrieved_context": [],
        "context_count": 0,
        "categorization_result": None,
        "anomaly_result": None,
        "anomalies_detected": [],
        "final_answer": "",
        "reasoning_steps": [],
        "sufficient_data": False,
        "error": None
    }


@pytest.fixture
def sample_transactions():
    """Create sample transaction DataFrame."""
    dates = [datetime.now() - timedelta(days=i) for i in range(50)]
    return pd.DataFrame({
        "date": dates,
        "amount": [-100] * 50,
        "description": ["Whole Foods Market"] * 50,
        "category": ["Groceries"] * 50,
        "user_id": ["user_test"] * 50
    })


class TestWorkflowNodes:
    """Test individual workflow nodes."""
    
    @patch('app.agents.workflow.get_vector_store')
    def test_retrieve_context_node(self, mock_vector_store, sample_state, sample_transactions):
        """Test context retrieval node."""
        # Mock vector store
        mock_store = Mock()
        mock_store.retrieve_context.return_value = {
            "documents": ["doc1", "doc2", "doc3"],
            "metadatas": [{}, {}, {}],
            "distances": [0.1, 0.2, 0.3],
            "count": 3
        }
        mock_store.get_user_transactions.return_value = sample_transactions
        mock_vector_store.return_value = mock_store
        
        # Run node
        result_state = retrieve_context_node(sample_state)
        
        # Assertions
        assert result_state["context_count"] == 3
        assert len(result_state["reasoning_steps"]) == 1
        assert result_state["reasoning_steps"][0]["agent"] == "Retrieval"
    
    def test_categorization_node(self, sample_state, sample_transactions):
        """Test categorization node."""
        # Set up state with transactions
        sample_state["transactions_df"] = sample_transactions.copy()
        sample_state["transactions_df"]["category"] = ""  # Uncategorized
        
        # Run node
        result_state = categorization_node(sample_state)
        
        # Assertions
        assert result_state["categorization_result"] is not None
        assert len(result_state["reasoning_steps"]) == 1
        assert result_state["reasoning_steps"][0]["agent"] == "Categorization"
    
    def test_anomaly_detection_node(self, sample_state, sample_transactions):
        """Test anomaly detection node."""
        # Set up state with transactions
        sample_state["transactions_df"] = sample_transactions
        
        # Run node
        result_state = anomaly_detection_node(sample_state)
        
        # Assertions
        assert result_state["anomaly_result"] is not None
        assert "anomaly_count" in result_state["anomaly_result"]
        assert len(result_state["reasoning_steps"]) == 1
    
    @patch('app.agents.advisor.get_llm')
    def test_advisor_node_sufficient_data(self, mock_llm, sample_state, sample_transactions):
        """Test advisor node with sufficient data."""
        # Mock LLM
        mock_llm_instance = Mock()
        mock_response = Mock()
        mock_response.content = "Test financial advice"
        mock_llm_instance.invoke.return_value = mock_response
        mock_llm.return_value = mock_llm_instance
        
        # Set up state
        sample_state["sufficient_data"] = True
        sample_state["transactions_df"] = sample_transactions
        sample_state["retrieved_context"] = ["context1", "context2"]
        sample_state["anomalies_detected"] = []
        
        # Run node
        result_state = advisor_node(sample_state)
        
        # Assertions
        assert result_state["final_answer"] != ""
        assert len(result_state["reasoning_steps"]) == 1
    
    def test_advisor_node_insufficient_data(self, sample_state):
        """Test advisor node with insufficient data."""
        # Set up state with insufficient data
        sample_state["sufficient_data"] = False
        
        # Run node
        result_state = advisor_node(sample_state)
        
        # Assertions
        assert "insufficient" in result_state["final_answer"].lower()


class TestWorkflowRouting:
    """Test workflow routing logic."""
    
    def test_should_run_analysis_sufficient_data(self, sample_state):
        """Test routing with sufficient data."""
        sample_state["sufficient_data"] = True
        result = should_run_analysis(sample_state)
        assert result == "analysis"
    
    def test_should_run_analysis_insufficient_data(self, sample_state):
        """Test routing with insufficient data."""
        sample_state["sufficient_data"] = False
        result = should_run_analysis(sample_state)
        assert result == "advisor"


class TestDataGenerator:
    """Test synthetic data generation."""
    
    def test_data_generator_import(self):
        """Test importing data generator."""
        from scripts.generate_data import FinancialDataGenerator
        generator = FinancialDataGenerator()
        assert generator is not None
    
    def test_generate_transactions(self):
        """Test generating transactions."""
        from scripts.generate_data import FinancialDataGenerator
        
        generator = FinancialDataGenerator(seed=42)
        transactions = generator.generate_user_transactions(
            user_id="test_user",
            num_months=1,
            transactions_per_month=10
        )
        
        assert len(transactions) >= 5  # Should generate some transactions
        assert all("user_id" in t for t in transactions)
        assert all("amount" in t for t in transactions)
    
    def test_anomaly_injection(self):
        """Test anomaly injection."""
        from scripts.generate_data import FinancialDataGenerator
        
        generator = FinancialDataGenerator(seed=42)
        transactions = [
            {"amount": -100, "description": "Normal"}
            for _ in range(20)
        ]
        
        result = generator.inject_anomalies(transactions, anomaly_rate=0.2)
        
        # Check that some anomalies were injected
        anomalous = [t for t in result if "UNUSUAL" in t["description"]]
        assert len(anomalous) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
