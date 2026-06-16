"""
app/db/models/compost_cycle.py
───────────────────────────────
A composting batch (cycle) for a single device.

Lean by design: this table tracks only the *lifecycle* of a batch.  Derived
metrics (total input weight, average temperature, output yield, etc.) are NOT
stored here — they are computed on demand from `waste_logs` and
`sensor_readings` so they never go stale.

Lifecycle
─────────
  active     → currently being fed / decomposing
  curing     → feeding stopped, maturing
  completed  → done; compost harvested

Invariant: at most one `active` cycle per device, enforced by a PostgreSQL
partial unique index on (device_id) WHERE status = 'active'.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# Allowed status values (kept in Python; DB column is a plain String for
# migration simplicity — swap for a native ENUM later if desired).
CYCLE_STATUSES = ("active", "curing", "completed")


class CompostCycle(Base, TimestampMixin):
    __tablename__ = "compost_cycles"

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

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Constraints / indexes ─────────────────────────────────────────────────
    __table_args__ = (
        # At most one active cycle per device.
        Index(
            "uq_compost_cycles_one_active_per_device",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    device: Mapped["Device"] = relationship("Device", lazy="select")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CompostCycle id={self.id} device_id={self.device_id} "
            f"status={self.status}>"
        )
