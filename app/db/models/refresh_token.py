"""
app/db/models/refresh_token.py
────────────────────────────────
SQLAlchemy 2.0 ORM model for the `refresh_tokens` table.

Design notes
────────────
- Raw refresh tokens are never stored.  Only a SHA-256 hash is persisted,
  matching the approach used for API keys in production systems.
- The `revoked` flag allows explicit logout / token rotation without waiting
  for expiry.
- Expired or revoked tokens should be pruned periodically by a Celery beat task.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # FK → users.id  (cascade delete handles cleanup when a user is removed)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 hex digest of the opaque token string handed to the client.
    # Unique constraint prevents the astronomically unlikely duplicate.
    token_hash: Mapped[str] = mapped_column(
        String(64),  # SHA-256 hex = 64 chars
        unique=True,
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(  # noqa: F821
        "User",
        back_populates="refresh_tokens",
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def is_expired(self) -> bool:
        from datetime import timezone
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RefreshToken id={self.id} user_id={self.user_id} "
            f"revoked={self.revoked} expires_at={self.expires_at}>"
        )
