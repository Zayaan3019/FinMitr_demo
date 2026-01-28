"""
Integration tests for FastAPI endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
import pandas as pd
from io import BytesIO
from datetime import datetime

from main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_csv():
    """Create sample CSV data."""
    df = pd.DataFrame({
        "date": [datetime.now()] * 10,
        "amount": [-100, -200, -150, -80, -120, -300, -90, -110, -180, -140],
        "description": ["Store A"] * 10,
        "category": ["Groceries"] * 10
    })
    csv_buffer = BytesIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)
    return csv_buffer


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    def test_health_check_success(self, client):
        """Test health check returns success."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]
        assert "version" in data
        assert "components" in data
    
    def test_health_check_structure(self, client):
        """Test health check response structure."""
        response = client.get("/api/v1/health")
        data = response.json()
        
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert "components" in data


class TestRootEndpoint:
    """Test root endpoint."""
    
    def test_root_endpoint(self, client):
        """Test root endpoint returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == "FinGuru"
        assert "version" in data
        assert "endpoints" in data


class TestIngestEndpoint:
    """Test data ingestion endpoint."""
    
    def test_ingest_missing_user_id(self, client, sample_csv):
        """Test ingest fails without user_id."""
        files = {"file": ("transactions.csv", sample_csv, "text/csv")}
        response = client.post("/api/v1/ingest", files=files)
        assert response.status_code == 422  # Validation error
    
    def test_ingest_invalid_file_type(self, client):
        """Test ingest fails with invalid file type."""
        files = {"file": ("test.txt", BytesIO(b"invalid"), "text/plain")}
        response = client.post(
            "/api/v1/ingest",
            params={"user_id": "user_001"},
            files=files
        )
        assert response.status_code == 400
    
    @patch('app.db.vector_store.VectorStoreManager.ingest_user_data')
    def test_ingest_success(self, mock_ingest, client, sample_csv):
        """Test successful ingestion."""
        mock_ingest.return_value = {
            "success": True,
            "user_id": "user_001",
            "transactions_count": 10,
            "embedding_time_seconds": 1.5
        }
        
        files = {"file": ("transactions.csv", sample_csv, "text/csv")}
        response = client.post(
            "/api/v1/ingest",
            params={"user_id": "user_001"},
            files=files
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user_id"] == "user_001"


class TestChatEndpoint:
    """Test chat endpoint."""
    
    def test_chat_missing_fields(self, client):
        """Test chat fails with missing fields."""
        response = client.post("/api/v1/chat", json={})
        assert response.status_code == 422
    
    def test_chat_invalid_query(self, client):
        """Test chat fails with invalid query."""
        response = client.post(
            "/api/v1/chat",
            json={"user_id": "user_001", "query": "Hi"}  # Too short
        )
        assert response.status_code == 422
    
    @patch('app.agents.workflow.run_workflow')
    def test_chat_success(self, mock_workflow, client):
        """Test successful chat request."""
        mock_workflow.return_value = {
            "success": True,
            "user_id": "user_001",
            "query": "What are my expenses?",
            "reasoning_steps": [
                {
                    "agent": "Test",
                    "action": "Test action",
                    "result": "Test result",
                    "timestamp": datetime.now()
                }
            ],
            "final_answer": "Test answer",
            "anomalies_detected": [],
            "context_retrieved": 10,
            "processing_time_seconds": 5.2
        }
        
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "user_001",
                "query": "What are my expenses?"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user_id"] == "user_001"
        assert "reasoning_steps" in data
        assert "final_answer" in data


class TestStatsEndpoint:
    """Test statistics endpoint."""
    
    @patch('app.db.vector_store.VectorStoreManager.get_collection_stats')
    def test_stats_success(self, mock_stats, client):
        """Test getting statistics."""
        mock_stats.return_value = {
            "collection_name": "finguru_transactions",
            "total_documents": 1000
        }
        
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "statistics" in data


class TestDeleteUserEndpoint:
    """Test user deletion endpoint."""
    
    @patch('app.db.vector_store.VectorStoreManager.delete_user_data')
    def test_delete_user_success(self, mock_delete, client):
        """Test deleting user data."""
        mock_delete.return_value = True
        
        response = client.delete("/api/v1/user/user_001")
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert data["user_id"] == "user_001"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
