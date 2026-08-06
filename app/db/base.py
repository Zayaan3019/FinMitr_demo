"""
SQLAlchemy declarative base and shared column conventions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable,
# human-readable constraint names instead of database-assigned ones.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for all FinGuru ORM models."""

    metadata = metadata_obj


def utcnow() -> datetime:
    """Timezone-aware UTC now (never naive -- naive timestamps in a financial
    ledger are a source of silent, unrecoverable off-by-hours bugs)."""
    return datetime.now(timezone.utc)


def uuid_pk():
    """Standard UUID primary key column."""
    from sqlalchemy.dialects.postgresql import UUID

    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)


def created_at_col():
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
