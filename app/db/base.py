"""
app/db/base.py
──────────────
SQLAlchemy declarative base and common mixin shared by all models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base — import this in every model file."""
    pass


class TimestampMixin:
    """
    Adds created_at / updated_at columns to any model.

    Both columns are timezone-aware. updated_at is automatically kept
    current by the DB server via onupdate=func.now().
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
