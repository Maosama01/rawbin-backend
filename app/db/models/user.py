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
    Registered end-users who can be linked to one or more Rawbin composters.

    Device access is a many-to-many relationship expressed through the
    `user_devices` association table (see UserDevice) — every linked user has
    equal, full access to the device.

    Columns
    -------
    id                  UUID primary key (generated client-side for idempotency)
    email               Unique, lowercase-normalised email address
    phone               Unique E.164 phone number for SMS-OTP login; nullable
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
    # E.164 format (e.g. +14155552671). Used as the lookup key for SMS-OTP login.
    phone: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        index=True,
        nullable=True,
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
    # Association rows linking this user to devices they can access.
    device_links: Mapped[list["UserDevice"]] = relationship(  # noqa: F821
        "UserDevice",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    # Convenience read-only view of the linked Device objects.
    devices: Mapped[list["Device"]] = relationship(  # noqa: F821
        "Device",
        secondary="user_devices",
        viewonly=True,
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r}>"
