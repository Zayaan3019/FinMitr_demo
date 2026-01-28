"""
Unit tests for Pydantic models and schemas.
"""
import pytest
from datetime import datetime
from pydantic import ValidationError

from app.models.schemas import (
    Transaction,
    IngestRequest,
    ChatRequest,
    AgentStep,
    TransactionCategory
)


class TestTransaction:
    """Test Transaction model."""
    
    def test_valid_transaction(self):
        """Test creating a valid transaction."""
        transaction = Transaction(
            date=datetime.now(),
            amount=-150.50,
            description="Whole Foods Market",
            category="Groceries",
            user_id="user_001"
        )
        assert transaction.amount == -150.50
        assert transaction.description == "Whole Foods Market"
        assert transaction.user_id == "user_001"
    
    def test_amount_validation(self):
        """Test amount cannot be zero."""
        with pytest.raises(ValidationError):
            Transaction(
                date=datetime.now(),
                amount=0,  # Invalid
                description="Test",
                user_id="user_001"
            )
    
    def test_empty_description_fails(self):
        """Test that empty description fails."""
        with pytest.raises(ValidationError):
            Transaction(
                date=datetime.now(),
                amount=-100,
                description="   ",  # Empty after strip
                user_id="user_001"
            )
    
    def test_amount_rounding(self):
        """Test amount is rounded to 2 decimals."""
        transaction = Transaction(
            date=datetime.now(),
            amount=-150.5555,
            description="Test",
            user_id="user_001"
        )
        assert transaction.amount == -150.56


class TestIngestRequest:
    """Test IngestRequest model."""
    
    def test_valid_request(self):
        """Test valid ingest request."""
        request = IngestRequest(user_id="user_001")
        assert request.user_id == "user_001"
    
    def test_short_user_id_fails(self):
        """Test that short user_id fails."""
        with pytest.raises(ValidationError):
            IngestRequest(user_id="ab")  # Too short
    
    def test_user_id_stripped(self):
        """Test user_id is stripped."""
        request = IngestRequest(user_id="  user_001  ")
        assert request.user_id == "user_001"


class TestChatRequest:
    """Test ChatRequest model."""
    
    def test_valid_chat_request(self):
        """Test valid chat request."""
        request = ChatRequest(
            user_id="user_001",
            query="What are my expenses?"
        )
        assert request.user_id == "user_001"
        assert request.query == "What are my expenses?"
    
    def test_short_query_fails(self):
        """Test that short query fails."""
        with pytest.raises(ValidationError):
            ChatRequest(user_id="user_001", query="Hi")  # Too short
    
    def test_query_stripped(self):
        """Test query is stripped."""
        request = ChatRequest(
            user_id="user_001",
            query="  What are my expenses?  "
        )
        assert request.query == "What are my expenses?"


class TestAgentStep:
    """Test AgentStep model."""
    
    def test_agent_step_creation(self):
        """Test creating an agent step."""
        step = AgentStep(
            agent="TestAgent",
            action="Test action",
            result="Test result"
        )
        assert step.agent == "TestAgent"
        assert step.action == "Test action"
        assert step.result == "Test result"
        assert isinstance(step.timestamp, datetime)


class TestTransactionCategory:
    """Test TransactionCategory enum."""
    
    def test_category_values(self):
        """Test category enum values."""
        assert TransactionCategory.GROCERIES == "Groceries"
        assert TransactionCategory.UTILITIES == "Utilities"
        assert TransactionCategory.SALARY == "Salary"
    
    def test_category_list(self):
        """Test getting all categories."""
        categories = [c.value for c in TransactionCategory]
        assert "Groceries" in categories
        assert "Salary" in categories
        assert len(categories) == 12


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
