"""
app/api/v1/telemetry.py
────────────────────────
Telemetry ingestion and history routes.

  POST /api/v1/telemetry/{device_id}          – single reading, immediate DB insert
  POST /api/v1/telemetry/{device_id}/batch    – up to 500 readings, bulk insert
  GET  /api/v1/telemetry/{device_id}/history  – time-series query (raw/hour/day)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.db.models.device import Device
from app.db.models.sensor_reading import SensorReading
from app.schemas.telemetry import (
    BatchSensorReadingIn,
    SensorReadingIn,
    TelemetryAcceptedResponse,
    TelemetryHistoryPoint,
    TelemetryHistoryResponse,
    TelemetryRawPoint,
)
from app.workers.tasks.telemetry import process_telemetry_alert_check

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])



# ── Auth helper ──────────────────────────────────────────────────────────────

async def _get_device_and_assert_access(
    device_id: uuid.UUID,
    db: AsyncSession,
    current_user,  # User ORM object from get_current_user
) -> Device:
    """
    Load the device and verify the caller is the owner.
    Device JWTs have subject "device:<uuid>" — the get_current_user dep
    will reject them since no matching User row exists, so devices must
    use the owner's user JWT to submit telemetry via this gateway.
    """
    result = await db.execute(select(Device).where(Device.id == device_id))
    device: Device | None = result.scalar_one_or_none()

    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    if not device.is_paired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Device must complete pairing before sending telemetry.",
        )

    if device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not device owner.")

    return device


def _reading_to_row(
    reading: SensorReadingIn, device_id: uuid.UUID, offset_us: int = 0
) -> dict:
    """Convert a Pydantic schema to a flat dict for DB insertion.

    Args:
        offset_us: microseconds to add to auto-generated timestamps.
            Used by the batch handler to ensure unique (time, device_id)
            PKs when the client does not supply explicit timestamps.
    """
    ts = reading.time
    if ts is None:
        from datetime import timedelta
        ts = datetime.now(timezone.utc) + timedelta(microseconds=offset_us)
    return {
        "time": ts,
        "device_id": device_id,
        "temperature_c": reading.temperature_c,
        "humidity_pct": reading.humidity_pct,
        "co2_ppm": reading.co2_ppm,
        "ph_level": reading.ph_level,
        "ambient_temp_c": reading.ambient_temp_c,
        "fan_speed_rpm": reading.fan_speed_rpm,
        "fill_level_pct": reading.fill_level_pct,
        "weight_kg": reading.weight_kg,
        "firmware_version": reading.firmware_version,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/{device_id}",
    response_model=TelemetryAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a single sensor reading",
)
async def ingest_reading(
    device_id: uuid.UUID,
    body: SensorReadingIn,
    db: DbSession,
    current_user: CurrentUser,
) -> TelemetryAcceptedResponse:
    """
    Accept a single sensor reading and persist it to the TimescaleDB hypertable.

    After commit, dispatches an async Celery task to check for alert conditions
    (out-of-range temperature, CO₂ spike, pH anomaly, etc.).
    """
    device = await _get_device_and_assert_access(device_id, db, current_user)

    row = SensorReading(**_reading_to_row(body, device_id))

    # Update firmware version on device record if provided
    if body.firmware_version:
        device.firmware_version = body.firmware_version

    db.add(row)
    await db.flush()

    # Dispatch Celery alert check (fire-and-forget; committed after return)
    try:
        process_telemetry_alert_check.apply_async(
            args=[str(device_id)],
            kwargs={
                "temperature_c": body.temperature_c,
                "co2_ppm": body.co2_ppm,
                "humidity_pct": body.humidity_pct,
                "ph_level": body.ph_level,
                "reading_time": row.time.isoformat(),
            },
            queue="telemetry",
        )
    except Exception:
        # Non-fatal — don't fail the ingest if Celery is temporarily unavailable
        logger.warning("Could not dispatch alert check task", exc_info=True)

    logger.debug("Single reading accepted", extra={"device_id": str(device_id)})

    return TelemetryAcceptedResponse(accepted=1, device_id=device_id)


@router.post(
    "/{device_id}/batch",
    response_model=TelemetryAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a batch of sensor readings (up to 500)",
)
async def ingest_batch(
    device_id: uuid.UUID,
    body: BatchSensorReadingIn,
    db: DbSession,
    current_user: CurrentUser,
) -> TelemetryAcceptedResponse:
    """
    Accept a batch of sensor readings buffered by the device while offline.

    Uses SQLAlchemy bulk insert for efficient multi-row inserts
    into the TimescaleDB hypertable.  All readings are inserted in a
    single transaction — partial failures roll back the entire batch.
    """
    device = await _get_device_and_assert_access(device_id, db, current_user)

    # Apply per-row microsecond offset to guarantee unique (time, device_id) PKs
    # when readings arrive without explicit timestamps in the same millisecond.
    rows = [
        _reading_to_row(r, device_id, offset_us=i)
        for i, r in enumerate(body.readings)
    ]

    # Update firmware version from the last reading that supplies it
    for r in reversed(body.readings):
        if r.firmware_version:
            device.firmware_version = r.firmware_version
            break

    # Bulk insert via executemany (much faster than individual ORM adds)
    await db.execute(
        SensorReading.__table__.insert(),
        rows,
    )

    logger.info(
        "Batch readings accepted",
        extra={"device_id": str(device_id), "count": len(rows)},
    )

    return TelemetryAcceptedResponse(accepted=len(rows), device_id=device_id)


# ── History ───────────────────────────────────────────────────────────────────

@router.get(
    "/{device_id}/history",
    response_model=TelemetryHistoryResponse,
    summary="Query historical sensor readings",
)
async def get_telemetry_history(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    interval: str = Query(
        default="hour",
        description=(
            "Time resolution. "
            "`raw` = exact rows (capped at 1000, max 24h window). "
            "`hour` = hourly aggregates from TimescaleDB continuous view (default). "
            "`day` = daily rollup of the hourly view."
        ),
        pattern="^(raw|hour|day)$",
    ),
    from_: datetime | None = Query(
        default=None,
        alias="from",
        description="Start of window (ISO-8601). Defaults to 24h ago for raw, 7d ago for hour, 90d for day.",
    ),
    to: datetime | None = Query(
        default=None,
        description="End of window (ISO-8601). Defaults to now.",
    ),
) -> TelemetryHistoryResponse:
    """
    Time-series sensor data query with three resolution tiers:

    | `interval` | Source | Default window | Max window |
    |---|---|---|---|
    | `raw` | `sensor_readings` | last 24h | 24h / 1000 rows |
    | `hour` | `sensor_readings_hourly` continuous agg | last 7d | unlimited |
    | `day` | daily rollup of hourly agg | last 90d | unlimited |

    The mobile app should use:
    - `raw` for the real-time detail chart (last few hours)
    - `hour` for the week/month trend chart
    - `day` for the 3-month / all-time summary
    """
    device = await _get_device_and_assert_access(device_id, db, current_user)

    now = datetime.now(timezone.utc)

    # Default windows per interval
    defaults = {"raw": timedelta(hours=24), "hour": timedelta(days=7), "day": timedelta(days=90)}
    window_start = from_ or (now - defaults[interval])
    window_end = to or now

    if window_end <= window_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be after `from`.",
        )

    # Enforce raw window cap
    if interval == "raw" and (window_end - window_start) > timedelta(hours=24):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Raw interval window cannot exceed 24 hours. Use interval=hour for longer ranges.",
        )

    readings: list = []

    if interval == "raw":
        # ── Direct hypertable query ───────────────────────────────────────────
        result = await db.execute(
            select(SensorReading)
            .where(
                SensorReading.device_id == device_id,
                SensorReading.time >= window_start,
                SensorReading.time <= window_end,
            )
            .order_by(SensorReading.time.asc())
            .limit(1000)
        )
        rows = result.scalars().all()
        readings = [
            TelemetryRawPoint(
                time=r.time,
                temperature_c=r.temperature_c,
                humidity_pct=r.humidity_pct,
                co2_ppm=r.co2_ppm,
                ph_level=r.ph_level,
                ambient_temp_c=r.ambient_temp_c,
                fan_speed_rpm=r.fan_speed_rpm,
                fill_level_pct=r.fill_level_pct,
                weight_kg=r.weight_kg,
                firmware_version=r.firmware_version,
            )
            for r in rows
        ]

    elif interval == "hour":
        # ── TimescaleDB continuous aggregate: sensor_readings_hourly ──────────
        result = await db.execute(
            text("""
                SELECT
                    bucket,
                    avg_temperature_c,
                    min_temperature_c,
                    max_temperature_c,
                    avg_humidity_pct,
                    avg_co2_ppm,
                    avg_ph_level,
                    avg_fan_speed_rpm
                FROM sensor_readings_hourly
                WHERE device_id = :device_id
                  AND bucket >= :from_ts
                  AND bucket <= :to_ts
                ORDER BY bucket ASC
            """),
            {"device_id": str(device_id), "from_ts": window_start, "to_ts": window_end},
        )
        readings = [
            TelemetryHistoryPoint(
                bucket=row.bucket,
                temperature_c_avg=row.avg_temperature_c,
                temperature_c_min=row.min_temperature_c,
                temperature_c_max=row.max_temperature_c,
                humidity_pct_avg=row.avg_humidity_pct,
                co2_ppm_avg=row.avg_co2_ppm,
                ph_level_avg=row.avg_ph_level,
                fan_speed_rpm_avg=row.avg_fan_speed_rpm,
            )
            for row in result
        ]

    else:  # day
        # ── Daily rollup from hourly aggregate ────────────────────────────────
        result = await db.execute(
            text("""
                SELECT
                    time_bucket('1 day', bucket) AS bucket,
                    AVG(avg_temperature_c)        AS avg_temperature_c,
                    MIN(min_temperature_c)        AS min_temperature_c,
                    MAX(max_temperature_c)        AS max_temperature_c,
                    AVG(avg_humidity_pct)         AS avg_humidity_pct,
                    AVG(avg_co2_ppm)              AS avg_co2_ppm,
                    AVG(avg_ph_level)             AS avg_ph_level,
                    AVG(avg_fan_speed_rpm)        AS avg_fan_speed_rpm
                FROM sensor_readings_hourly
                WHERE device_id = :device_id
                  AND bucket >= :from_ts
                  AND bucket <= :to_ts
                GROUP BY 1
                ORDER BY 1 ASC
            """),
            {"device_id": str(device_id), "from_ts": window_start, "to_ts": window_end},
        )
        readings = [
            TelemetryHistoryPoint(
                bucket=row.bucket,
                temperature_c_avg=row.avg_temperature_c,
                temperature_c_min=row.min_temperature_c,
                temperature_c_max=row.max_temperature_c,
                humidity_pct_avg=row.avg_humidity_pct,
                co2_ppm_avg=row.avg_co2_ppm,
                ph_level_avg=row.avg_ph_level,
                fan_speed_rpm_avg=row.avg_fan_speed_rpm,
            )
            for row in result
        ]

    return TelemetryHistoryResponse(
        device_id=device_id,
        interval=interval,
        **{"from": window_start},
        to=window_end,
        count=len(readings),
        readings=readings,
    )
