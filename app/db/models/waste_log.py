"""
app/db/models/waste_log.py
────────────────────────────
A record of material a user added to the bin.

Powers "what went into this batch" and green:brown ratio tracking.

Design notes
────────────
- `device_id` is denormalised onto the row (not only reachable via the cycle)
  so monthly-summary queries ("how much did device X take in this month?")
  stay a single-table scan and work even for material logged before any cycle
  was started.
- `compost_cycle_id` is a NULLable FK: material can be logged before a cycle
  exists; it can be attributed to a cycle later.
- `user_id` records who logged it; ON DELETE SET NULL keeps the log if the
  user account is removed.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

WASTE_TYPES = ("greens", "browns", "food", "other")


class WasteLog(Base, TimestampMixin):
    __tablename__ = "waste_logs"

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
    compost_cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compost_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    waste_type: Mapped[str] = mapped_column(String(16), nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    device: Mapped["Device"] = relationship("Device", lazy="select")  # noqa: F821
    compost_cycle: Mapped["CompostCycle | None"] = relationship(  # noqa: F821
        "CompostCycle", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WasteLog id={self.id} device_id={self.device_id} "
            f"type={self.waste_type} weight={self.weight_kg}>"
        )
