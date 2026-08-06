"""
Core configuration management for FinGuru.
Loads environment variables and provides centralized settings.
"""

from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    """Application settings with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_name: str = Field(default="FinGuru")
    app_version: str = Field(default="3.0.0")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")

    # API Configuration
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    cors_allow_origins: str = Field(default="")

    # ------------------------------------------------------------------
    # GROQ / LLM
    # ------------------------------------------------------------------
    groq_api_key: str = Field(default="")
    llm_model: str = Field(default="llama-3.3-70b-versatile")
    llm_temperature: float = Field(default=0.1)
    llm_max_tokens: int = Field(default=2048)

    # Embedding
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")

    # ChromaDB
    chroma_persist_dir: str = Field(default="./data/chromadb")
    vector_collection_name: str = Field(default="finguru_transactions")

    # ------------------------------------------------------------------
    # PostgreSQL (PHASE 2)
    # ------------------------------------------------------------------
    # `database_url` is the *application* connection. It MUST point at a role
    # that is neither superuser nor the table owner, otherwise PostgreSQL
    # silently bypasses Row-Level Security and the tenancy guarantee evaporates.
    database_url: str = Field(
        default="postgresql+asyncpg://finguru_app:finguru_app@localhost:5432/finguru"
    )
    # `database_migration_url` is the owner/DDL connection used by Alembic only.
    database_migration_url: str = Field(
        default="postgresql+asyncpg://finguru:finguru@localhost:5432/finguru"
    )
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=20)
    db_pool_timeout: int = Field(default=30)
    db_echo: bool = Field(default=False)
    # Role the migration creates for the application to use.
    db_app_role: str = Field(default="finguru_app")
    db_app_password: str = Field(default="finguru_app")

    # Number of future monthly partitions kept ahead of "now".
    txn_partition_lookahead_months: int = Field(default=3)

    # ------------------------------------------------------------------
    # Redis (PHASE 1 - jti denylist, rate limiting, token budgets)
    # ------------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_required: bool = Field(default=False)

    # ------------------------------------------------------------------
    # Identity / JWT (PHASE 1)
    # ------------------------------------------------------------------
    jwt_secret: str = Field(default="")
    jwt_algorithm: str = Field(default="HS256")
    jwt_issuer: str = Field(default="finguru")
    jwt_audience: str = Field(default="finguru-api")
    access_token_ttl_seconds: int = Field(default=900)  # 15 minutes
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14)  # 14 days

    # Argon2id parameters. Memory-hardness is the point: a GPU/ASIC attacker
    # gains far less than against bcrypt because each guess must hold
    # `argon2_memory_cost` KiB of state simultaneously.
    argon2_time_cost: int = Field(default=3)
    argon2_memory_cost: int = Field(default=65536)  # 64 MiB
    argon2_parallelism: int = Field(default=4)
    argon2_hash_len: int = Field(default=32)
    argon2_salt_len: int = Field(default=16)

    # Field-level encryption key for mfa_secret_enc (32 bytes, base64/urlsafe).
    field_encryption_key: str = Field(default="")

    # Account lockout
    auth_max_failed_attempts: int = Field(default=5)
    auth_lockout_seconds: int = Field(default=900)
    # Auth endpoints are limited separately and harder than the general limiter.
    auth_rate_limit_per_minute: int = Field(default=10)
    auth_rate_limit_per_hour: int = Field(default=60)
    general_rate_limit_per_minute: int = Field(default=60)
    general_rate_limit_per_hour: int = Field(default=1000)

    # MFA
    mfa_issuer: str = Field(default="FinGuru")
    mfa_totp_window: int = Field(default=1)
    # Mandatory before any bank linkage (PHASE 1 / PHASE 3).
    mfa_required_for_bank_linkage: bool = Field(default=True)

    # ------------------------------------------------------------------
    # Account Aggregator / FIU (PHASE 3)
    # ------------------------------------------------------------------
    aa_enabled: bool = Field(default=True)
    aa_provider: str = Field(default="setu")  # setu | finvu | onemoney | mock
    aa_base_url: str = Field(default="https://fiu-sandbox.setu.co")
    aa_client_id: str = Field(default="")
    aa_client_secret: str = Field(default="")
    aa_fiu_id: str = Field(default="finguru-sandbox")
    aa_product_instance_id: str = Field(default="")
    aa_webhook_secret: str = Field(default="finguru-aa-webhook-secret")
    aa_request_timeout_seconds: float = Field(default=20.0)
    # Sandbox-only. A student cannot become an RBI-registered FIU; see README.
    aa_sandbox_only: bool = Field(default=True)
    aa_use_mock_transport: bool = Field(default=True)

    # ------------------------------------------------------------------
    # ML (PHASE 4)
    # ------------------------------------------------------------------
    model_registry_dir: str = Field(default="./data/models")
    categorizer_model_version: str = Field(default="")
    categorizer_base_model: str = Field(default="google/bert_uncased_L-2_H-128_A-2")
    categorizer_confidence_floor: float = Field(default=0.45)
    # Held-out gate for retraining: PSI alone never triggers a retrain.
    categorizer_min_macro_f1: float = Field(default=0.80)
    psi_alert_threshold: float = Field(default=0.2)
    psi_retrain_threshold: float = Field(default=0.25)

    # ------------------------------------------------------------------
    # LLM safety (PHASE 5)
    # ------------------------------------------------------------------
    llm_redaction_enabled: bool = Field(default=True)
    llm_injection_defence_enabled: bool = Field(default=True)
    llm_user_daily_token_budget: int = Field(default=50_000)
    llm_max_context_transactions: int = Field(default=25)
    # Fail closed: if redaction cannot run, do not call the provider at all.
    llm_fail_closed_on_redaction_error: bool = Field(default=True)

    # ------------------------------------------------------------------
    # Legacy upload limits
    # ------------------------------------------------------------------
    max_file_size_mb: int = Field(default=10)
    allowed_file_types: str = Field(default="csv")

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("chroma_persist_dir", "model_registry_dir")
    @classmethod
    def create_dir(cls, v: str) -> str:
        """Ensure the directory exists."""
        os.makedirs(v, exist_ok=True)
        return v

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """A weak or missing JWT secret makes every other control decorative."""
        if not v:
            # Development/test convenience only; production start-up refuses
            # this value via `validate_production_settings()`.
            return "dev-only-insecure-jwt-secret-change-me"
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def cors_origins(self) -> List[str]:
        if not self.cors_allow_origins:
            return ["*"] if self.debug else []
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        """psycopg/sync form of the app URL (used by Alembic offline mode)."""
        return self.database_url.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg://"
        )

    @property
    def sync_migration_url(self) -> str:
        return self.database_migration_url.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg://"
        )

    def validate_production_settings(self) -> List[str]:
        """
        Return a list of fatal misconfigurations for production.

        Called at startup; an empty list means the deployment is safe to serve.
        """
        problems: List[str] = []
        if not self.is_production:
            return problems

        if self.jwt_secret == "dev-only-insecure-jwt-secret-change-me":
            problems.append("JWT_SECRET is unset (development placeholder in use)")
        if not self.field_encryption_key:
            problems.append(
                "FIELD_ENCRYPTION_KEY is unset; MFA secrets would be stored unencrypted"
            )
        if self.debug:
            problems.append("DEBUG must be False in production")
        if "*" in self.cors_origins:
            problems.append("CORS allow_origins must not be '*' in production")
        if self.db_app_password in {"finguru_app", "", "postgres"}:
            problems.append("DB_APP_PASSWORD is a default/placeholder value")
        if not self.groq_api_key:
            problems.append("GROQ_API_KEY is unset")
        if self.aa_use_mock_transport:
            problems.append("AA_USE_MOCK_TRANSPORT is True; no real AA connectivity")
        return problems


# Global settings instance
settings = Settings()
