"""
app/api/v1/telemetry.py
────────────────────────
Telemetry HISTORY routes (read-only).

  GET /api/v1/telemetry/{device_id}/history  – time-series query (raw/hour/day)

Ingestion is MQTT-only: devices publish to the broker and the standalone
listener (app/workers/mqtt_listener.py) writes to the hypertable. The HTTP
POST ingestion endpoints were removed to make MQTT the single source of truth
and avoid a second, divergent write path.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.db.models.sensor_reading import SensorReading
from app.schemas.telemetry import (
    TelemetryHistoryPoint,
    TelemetryHistoryResponse,
    TelemetryRawPoint,
)
from app.services import device_access

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


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
    Time-series sensor data query with three resolution tiers (raw / hour / day).
    Access requires device membership.
    """
    await device_access.assert_device_member(db, device_id, current_user.id)

    now = datetime.now(timezone.utc)
    defaults = {"raw": timedelta(hours=24), "hour": timedelta(days=7), "day": timedelta(days=90)}
    window_start = from_ or (now - defaults[interval])
    window_end = to or now

    if window_end <= window_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be after `from`.",
        )
    if interval == "raw" and (window_end - window_start) > timedelta(hours=24):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Raw interval window cannot exceed 24 hours. Use interval=hour for longer ranges.",
        )

    readings: list = []

    if interval == "raw":
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
            for r in result.scalars().all()
        ]

    elif interval == "hour":
        result = await db.execute(
            text("""
                SELECT bucket, avg_temperature_c, min_temperature_c, max_temperature_c,
                       avg_humidity_pct, avg_co2_ppm, avg_ph_level, avg_fan_speed_rpm
                FROM sensor_readings_hourly
                WHERE device_id = :device_id AND bucket >= :from_ts AND bucket <= :to_ts
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
        result = await db.execute(
            text("""
                SELECT time_bucket('1 day', bucket) AS bucket,
                       AVG(avg_temperature_c) AS avg_temperature_c,
                       MIN(min_temperature_c) AS min_temperature_c,
                       MAX(max_temperature_c) AS max_temperature_c,
                       AVG(avg_humidity_pct)  AS avg_humidity_pct,
                       AVG(avg_co2_ppm)       AS avg_co2_ppm,
                       AVG(avg_ph_level)      AS avg_ph_level,
                       AVG(avg_fan_speed_rpm) AS avg_fan_speed_rpm
                FROM sensor_readings_hourly
                WHERE device_id = :device_id AND bucket >= :from_ts AND bucket <= :to_ts
                GROUP BY 1 ORDER BY 1 ASC
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
