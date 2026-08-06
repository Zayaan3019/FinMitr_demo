"""
LLM initialization and management using GROQ.
Provides centralized LLM access for all agents.
"""

from typing import Optional
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage
from tenacity import retry, stop_after_attempt, wait_exponential
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMManager:
    """Singleton manager for LLM instances."""

    _instance: Optional["LLMManager"] = None
    _llm: Optional[ChatGroq] = None

    def __new__(cls) -> "LLMManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize LLM if not already done."""
        if self._llm is None:
            self._initialize_llm()

    def _initialize_llm(self) -> None:
        """Initialize the GROQ LLM client."""
        try:
            self._llm = ChatGroq(
                api_key=settings.groq_api_key,
                model_name=settings.llm_model,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
            )
            logger.info(f"LLM initialized: {settings.llm_model}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def invoke(self, messages: list[BaseMessage]) -> str:
        """
        Invoke the LLM with retry logic.

        Args:
            messages: List of LangChain messages

        Returns:
            LLM response content
        """
        try:
            response = self._llm.invoke(messages)
            return response.content
        except Exception as e:
            logger.error(f"LLM invocation error: {e}")
            raise

    @property
    def llm(self) -> ChatGroq:
        """Get the LLM instance."""
        return self._llm


# Global LLM manager instance
def get_llm() -> ChatGroq:
    """Get the global LLM instance."""
    manager = LLMManager()
    return manager.llm
