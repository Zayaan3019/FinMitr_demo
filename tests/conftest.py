"""
Test configuration and fixtures for pytest.
"""
import pytest
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set test environment variables
os.environ["GROQ_API_KEY"] = "test_key_for_testing"
os.environ["DEBUG"] = "True"
os.environ["CHROMA_PERSIST_DIR"] = "./test_data/chromadb"


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup test environment before running tests."""
    # Create test data directory
    test_data_dir = Path("./test_data")
    test_data_dir.mkdir(exist_ok=True)
    
    yield
    
    # Cleanup after tests
    import shutil
    if test_data_dir.exists():
        shutil.rmtree(test_data_dir, ignore_errors=True)


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    monkeypatch.setenv("GROQ_API_KEY", "test_groq_key")
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


# Pytest configuration
def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
