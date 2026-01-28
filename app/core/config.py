"""
Core configuration management for FinGuru.
Loads environment variables and provides centralized settings.
"""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator
import os


class Settings(BaseSettings):
    """Application settings with validation."""

    # Application
    app_name: str = Field(default="FinGuru", env="APP_NAME")
    app_version: str = Field(default="1.0.0", env="APP_VERSION")
    debug: bool = Field(default=False, env="DEBUG")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # API Configuration
    api_host: str = Field(default="0.0.0.0", env="API_HOST")
    api_port: int = Field(default=8000, env="API_PORT")

    # GROQ API
    groq_api_key: str = Field(..., env="GROQ_API_KEY")
    llm_model: str = Field(default="llama3-70b-8192", env="LLM_MODEL")
    llm_temperature: float = Field(default=0.1, env="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2048, env="LLM_MAX_TOKENS")

    # Embedding
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", env="EMBEDDING_MODEL"
    )

    # ChromaDB
    chroma_persist_dir: str = Field(default="./data/chromadb", env="CHROMA_PERSIST_DIR")
    vector_collection_name: str = Field(
        default="finguru_transactions", env="VECTOR_COLLECTION_NAME"
    )

    # Security
    max_file_size_mb: int = Field(default=10, env="MAX_FILE_SIZE_MB")
    allowed_file_types: str = Field(default="csv", env="ALLOWED_FILE_TYPES")

    @validator("groq_api_key")
    def validate_api_key(cls, v: str) -> str:
        """Ensure API key is not placeholder."""
        if not v or v == "your_groq_api_key_here":
            raise ValueError(
                "GROQ_API_KEY must be set. Get your free key from https://console.groq.com"
            )
        return v

    @validator("chroma_persist_dir")
    def create_persist_dir(cls, v: str) -> str:
        """Ensure the ChromaDB directory exists."""
        os.makedirs(v, exist_ok=True)
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()
