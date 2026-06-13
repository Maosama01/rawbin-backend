"""
app/api/v1/status.py
──────────────────────
Device status/snapshot route — returns the latest sensor reading plus
device metadata.

  GET /api/v1/status/{device_id}

The query uses TimescaleDB's time-series optimisation: selecting the
most recent row for a device is extremely fast on a hypertable because
TimescaleDB physically orders chunks by time — the query hits only the
latest chunk, not a full table scan.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.db.models.device import Device
from app.db.models.sensor_reading import SensorReading
from app.schemas.telemetry import DeviceSnapshotResponse, SensorReadingOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["Status"])


@router.get(
    "/{device_id}",
    response_model=DeviceSnapshotResponse,
    summary="Retrieve the latest device snapshot (sensors + metadata)",
)
async def device_snapshot(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> DeviceSnapshotResponse:
    """
    Return the most recent sensor reading for a device, along with device
    metadata (display name, pairing state, firmware version).

    TimescaleDB query pattern:
      SELECT * FROM sensor_readings
      WHERE device_id = $1
      ORDER BY time DESC
      LIMIT 1;

    On a hypertable this is O(1) against the latest chunk — no full scan.
    """
    # Verify device exists and belongs to the current user
    dev_result = await db.execute(
        select(Device).where(Device.id == device_id)
    )
    device: Device | None = dev_result.scalar_one_or_none()

    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    # Fetch latest reading
    reading_result = await db.execute(
        select(SensorReading)
        .where(SensorReading.device_id == device_id)
        .order_by(SensorReading.time.desc())
        .limit(1)
    )
    latest: SensorReading | None = reading_result.scalar_one_or_none()

    latest_out: SensorReadingOut | None = None
    age_seconds: float | None = None

    if latest is not None:
        latest_out = SensorReadingOut.model_validate(latest)
        # Make time timezone-aware if it came back naive from asyncpg
        reading_time = latest.time
        if reading_time.tzinfo is None:
            reading_time = reading_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - reading_time).total_seconds()

    return DeviceSnapshotResponse(
        device_id=device.id,
        hardware_uid=device.hardware_uid,
        display_name=device.display_name,
        is_paired=device.is_paired,
        firmware_version=device.firmware_version,
        latest_reading=latest_out,
        reading_age_seconds=age_seconds,
    )
