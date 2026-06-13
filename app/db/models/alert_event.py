"""
app/db/models/alert_event.py
─────────────────────────────
Persists every threshold breach to the database for audit,
analytics, and in-app alert history display.

Each row represents one sensor metric crossing a threshold on
one reading for one device.  Multiple alerts from a single
telemetry batch appear as separate rows.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AlertEvent(Base):
    """
    Immutable log of threshold-breach alerts.

    Columns
    -------
    id              UUID PK
    device_id       FK → devices.id (CASCADE delete)
    metric          Name of the sensor field that breached (e.g. "temperature_c")
    severity        "WARNING" | "CRITICAL"
    value           The actual sensor value that triggered the alert
    threshold       The threshold value that was breached
    message         Human-readable description sent to the user
    reading_time    Timestamp of the originating sensor reading
    notified        True once a push notification was successfully dispatched
    created_at      Row insertion time (immutable — use created_at not updated_at)
    """

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        String(16),   # "WARNING" | "CRITICAL"
        nullable=False,
    )
    value: Mapped[float] = mapped_column(nullable=False)
    threshold: Mapped[float] = mapped_column(nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    reading_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    notified: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    # ── Relationship ──────────────────────────────────────────────────────────
    device: Mapped["Device"] = relationship("Device", lazy="select")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AlertEvent id={self.id} device={self.device_id} "
            f"metric={self.metric!r} severity={self.severity}>"
        )
