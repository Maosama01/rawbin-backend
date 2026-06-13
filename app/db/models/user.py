"""
app/db/models/user.py
─────────────────────
SQLAlchemy 2.0 ORM model for the `users` table.
"""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """
    Registered end-users who own one or more Rawbin composters.

    Columns
    -------
    id                  UUID primary key (generated client-side for idempotency)
    email               Unique, lowercase-normalised email address
    password_hash       bcrypt hash — raw password never stored
    display_name        Human-readable name shown in the mobile app
    firebase_push_token FCM token for push notifications; nullable (user may
                        revoke notification permission)
    is_active           Soft-disable account without deleting history
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(320),  # RFC 5321 max
        unique=True,
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    firebase_push_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(  # noqa: F821
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    devices: Mapped[list["Device"]] = relationship(  # noqa: F821
        "Device",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r}>"
