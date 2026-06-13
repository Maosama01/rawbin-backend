"""
app/db/models/device_config.py
────────────────────────────────
Per-device alert threshold configuration.

Defaults to the global ALERT_THRESHOLDS constants if no row exists.
A single row per device — UPSERT semantics on write.

All threshold values are nullable: NULL means "use global default".
This lets the mobile app configure only the metrics the user cares about.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DeviceConfig(Base, TimestampMixin):
    """
    Per-device sensor alert thresholds.

    Any NULL column falls back to the global default in ALERT_THRESHOLDS.
    """

    __tablename__ = "device_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # One config row per device
        index=True,
    )

    # Temperature thresholds (°C)
    temperature_c_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CO₂ threshold (ppm)
    co2_ppm_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Humidity thresholds (%)
    humidity_pct_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    # pH thresholds
    ph_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    ph_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Relationship ──────────────────────────────────────────────────────────
    device: Mapped["Device"] = relationship("Device", lazy="select")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeviceConfig device_id={self.device_id}>"
