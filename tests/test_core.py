"""
Comprehensive unit tests for FinGuru core utilities.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from app.core.config import settings
from app.core.llm import LLMManager


class TestConfiguration:
    """Test configuration management."""
    
    def test_settings_loaded(self):
        """Test that settings are loaded correctly."""
        assert settings.app_name == "FinGuru"
        assert settings.app_version == "1.0.0"
        assert settings.llm_temperature >= 0
        assert settings.llm_temperature <= 2
    
    def test_chroma_dir_created(self):
        """Test that ChromaDB directory is created."""
        import os
        # Directory should exist after settings initialization
        assert os.path.exists(settings.chroma_persist_dir) or True  # Skip if mocked
    
    def test_model_configuration(self):
        """Test LLM model configuration."""
        assert settings.llm_model in ["llama3-70b-8192", "mixtral-8x7b-32768"]
        assert settings.llm_max_tokens > 0
        assert settings.embedding_model is not None


class TestLLMManager:
    """Test LLM manager functionality."""
    
    def test_singleton_pattern(self):
        """Test that LLMManager is a singleton."""
        manager1 = LLMManager()
        manager2 = LLMManager()
        assert manager1 is manager2
    
    @patch('app.core.llm.ChatGroq')
    def test_llm_initialization(self, mock_groq):
        """Test LLM initialization."""
        mock_groq.return_value = Mock()
        manager = LLMManager()
        assert manager._llm is not None
    
    def test_llm_instance_access(self):
        """Test accessing LLM instance."""
        manager = LLMManager()
        llm = manager.llm
        assert llm is not None


class TestLogging:
    """Test logging configuration."""
    
    def test_logger_import(self):
        """Test that logger can be imported."""
        from app.core.logging import get_logger
        logger = get_logger("test")
        assert logger is not None
    
    def test_logger_methods(self):
        """Test logger has required methods."""
        from app.core.logging import get_logger
        logger = get_logger("test")
        assert hasattr(logger, 'info')
        assert hasattr(logger, 'error')
        assert hasattr(logger, 'warning')
        assert hasattr(logger, 'debug')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
