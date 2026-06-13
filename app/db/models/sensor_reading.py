"""
app/db/models/sensor_reading.py
────────────────────────────────
SQLAlchemy 2.0 ORM model for the `sensor_readings` TimescaleDB hypertable.

TimescaleDB design notes
─────────────────────────
- `time` is the mandatory hypertable partition column.  It must be part of
  the primary key.  The composite PK (time, device_id) ensures row uniqueness
  while satisfying TimescaleDB's constraint requirements.
- Because this is a hypertable, Alembic autogenerate CANNOT create it with a
  plain CREATE TABLE — the migration must call
    SELECT create_hypertable('sensor_readings', 'time', if_not_exists => TRUE);
  after the table is created.  This is handled in the migration file.
- We use `DOUBLE PRECISION` (mapped to `Float`) for sensor values — precise
  enough for all composter readings, avoids NUMERIC overhead.
- `NULL` sensor readings are allowed; a sensor may be absent on some
  hardware variants or temporarily unavailable.

Sensor columns (all optional to accommodate firmware evolution)
────────────────────────────────────────────────────────────────
  temperature_c      – Core chamber temperature (°C)
  humidity_pct       – Relative humidity (%)
  co2_ppm            – CO₂ concentration (ppm)
  ph_level           – pH of composting material
  ambient_temp_c     – Ambient / external temperature (°C)
  fan_speed_rpm      – Active fan speed (RPM)
  fill_level_pct     – Bin fill level from ultrasonic sensor (%)
  weight_kg          – Material weight (kg)
  firmware_version   – Firmware version string at time of reading
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Double, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SensorReading(Base):
    """
    One sensor snapshot pushed by a device over BLE → HTTPS.

    This table is converted into a TimescaleDB hypertable partitioned by `time`
    in the Alembic migration.  Do NOT add created_at/updated_at here — `time`
    IS the event timestamp and is the partition key.
    """

    __tablename__ = "sensor_readings"

    # ── Primary key: (time, device_id) ───────────────────────────────────────
    # TimescaleDB requires the partition column (time) to be part of the PK.
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
        comment="UTC timestamp of the sensor reading (hypertable partition key)",
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="FK to devices.id",
    )

    # ── Sensor measurements ───────────────────────────────────────────────────
    temperature_c: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="Chamber temperature in °C"
    )
    humidity_pct: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="Relative humidity in %"
    )
    co2_ppm: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="CO₂ concentration in ppm"
    )
    ph_level: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="pH of composting material (0–14)"
    )
    ambient_temp_c: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="Ambient / external temperature in °C"
    )
    fan_speed_rpm: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Active fan speed in RPM"
    )
    fill_level_pct: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="Bin fill level from ultrasonic sensor in %"
    )
    weight_kg: Mapped[float | None] = mapped_column(
        Double, nullable=True, comment="Material weight in kg"
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    firmware_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="Firmware version at time of reading"
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    # TimescaleDB automatically creates a B-tree index on `time`.
    # We add a secondary index on device_id for per-device time-series queries.
    __table_args__ = (
        Index("ix_sensor_readings_device_id_time", "device_id", "time"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    device: Mapped["Device"] = relationship(  # noqa: F821
        "Device",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SensorReading device_id={self.device_id} time={self.time} "
            f"temp={self.temperature_c}°C co2={self.co2_ppm}ppm>"
        )
