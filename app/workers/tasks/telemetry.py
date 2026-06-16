"""
app/workers/tasks/telemetry.py
────────────────────────────────
Celery tasks for post-ingestion telemetry processing.

Pipeline:
  1. process_telemetry_alert_check  — called after every reading ingest
       a. Load per-device thresholds (Redis cache → DB → global defaults)
       b. Compare each metric against thresholds
       c. Persist AlertEvent rows for every breach
       d. Dispatch send_push_notification for the device owner
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Global defaults ───────────────────────────────────────────────────────────
# Used when no per-device config row exists (or a specific threshold is NULL).

ALERT_THRESHOLDS: dict[str, float] = {
    "temperature_c_max":  80.0,   # °C  — too hot: pile combustion risk
    "temperature_c_min":  40.0,   # °C  — too cold: microbial activity stops
    "co2_ppm_max":      5000.0,   # ppm — inadequate aeration
    "humidity_pct_min":   40.0,   # %   — too dry
    "humidity_pct_max":   80.0,   # %   — anaerobic / too wet
    "ph_min":              5.0,   # pH  — too acidic
    "ph_max":              8.5,   # pH  — too alkaline
}

# Redis TTL for cached device config (5 minutes)
_CONFIG_CACHE_TTL = 300


# ── Threshold loader ──────────────────────────────────────────────────────────

def _load_thresholds(device_id: str) -> dict[str, float]:
    """
    Return the effective thresholds for a device.

    Resolution order:
      1. Redis cache  (key: alert_config:<device_id>)
      2. device_configs table row
      3. Global ALERT_THRESHOLDS defaults

    Per-device NULL values fall back to the global default for that key.
    Synchronous — runs inside a Celery worker thread (not async).
    """
    import json

    import redis
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models.device_config import DeviceConfig
    from app.workers.db import worker_session

    settings = get_settings()
    cache_key = f"alert_config:{device_id}"

    # ── 1. Try Redis cache ────────────────────────────────────────────────────
    try:
        r = redis.from_url(settings.REDIS_URL, decode_responses=True)
        cached = r.get(cache_key)
        if cached:
            logger.debug("Device thresholds cache hit", extra={"device_id": device_id})
            return json.loads(cached)
    except Exception:
        logger.warning("Redis unavailable — skipping cache read", exc_info=True)

    # ── 2. Load from DB (shared worker engine) ────────────────────────────────
    thresholds = dict(ALERT_THRESHOLDS)  # start with globals as defaults

    try:
        with worker_session() as session:
            result = session.execute(
                select(DeviceConfig).where(
                    DeviceConfig.device_id == uuid.UUID(device_id)
                )
            ).scalar_one_or_none()

            if result is not None:
                # Override globals with per-device values where set
                overrides: dict[str, Optional[float]] = {
                    "temperature_c_max": result.temperature_c_max,
                    "temperature_c_min": result.temperature_c_min,
                    "co2_ppm_max":       result.co2_ppm_max,
                    "humidity_pct_min":  result.humidity_pct_min,
                    "humidity_pct_max":  result.humidity_pct_max,
                    "ph_min":            result.ph_min,
                    "ph_max":            result.ph_max,
                }
                for key, val in overrides.items():
                    if val is not None:
                        thresholds[key] = val
                logger.debug(
                    "Device thresholds loaded from DB",
                    extra={"device_id": device_id},
                )
    except Exception:
        logger.warning(
            "Could not load device config from DB — using globals",
            exc_info=True,
        )

    # ── 3. Populate Redis cache ───────────────────────────────────────────────
    try:
        r.setex(cache_key, _CONFIG_CACHE_TTL, json.dumps(thresholds))
    except Exception:
        pass  # Cache write failure is non-fatal

    return thresholds


# ── Alert persistence ─────────────────────────────────────────────────────────

def _persist_alerts(
    device_id: str,
    alerts: list[dict],
    reading_time: datetime,
) -> None:
    """
    Write AlertEvent rows to the database for every breach.
    Runs synchronously inside the Celery worker.
    """
    from app.db.models.alert_event import AlertEvent
    from app.workers.db import worker_session

    if not alerts:
        return

    try:
        with worker_session() as session:
            for a in alerts:
                session.add(
                    AlertEvent(
                        device_id=uuid.UUID(device_id),
                        metric=a["metric"],
                        severity=a["severity"],
                        value=a["value"],
                        threshold=a["threshold"],
                        message=a["message"],
                        reading_time=reading_time,
                        notified=False,
                    )
                )
            logger.info(
                "Alert events persisted",
                extra={"device_id": device_id, "count": len(alerts)},
            )
    except Exception:
        logger.error(
            "Failed to persist alert events",
            extra={"device_id": device_id},
            exc_info=True,
        )


# ── Alert evaluation ──────────────────────────────────────────────────────────

def _evaluate(
    thresholds: dict[str, float],
    *,
    temperature_c: Optional[float],
    co2_ppm: Optional[float],
    humidity_pct: Optional[float],
    ph_level: Optional[float],
) -> list[dict]:
    """
    Compare readings against thresholds. Returns a list of alert dicts.
    Each dict: {metric, severity, value, threshold, message}
    """
    alerts: list[dict] = []

    def _add(metric: str, severity: str, value: float, threshold: float, msg: str):
        alerts.append({
            "metric": metric,
            "severity": severity,
            "value": value,
            "threshold": threshold,
            "message": msg,
        })

    if temperature_c is not None:
        if temperature_c > thresholds["temperature_c_max"]:
            _add(
                "temperature_c", "CRITICAL", temperature_c,
                thresholds["temperature_c_max"],
                f"🌡️ Pile temperature {temperature_c:.1f}°C is too high "
                f"(max {thresholds['temperature_c_max']:.0f}°C)",
            )
        elif temperature_c < thresholds["temperature_c_min"]:
            _add(
                "temperature_c", "WARNING", temperature_c,
                thresholds["temperature_c_min"],
                f"🌡️ Pile temperature {temperature_c:.1f}°C is too low "
                f"(min {thresholds['temperature_c_min']:.0f}°C) — composting may have stalled",
            )

    if co2_ppm is not None and co2_ppm > thresholds["co2_ppm_max"]:
        _add(
            "co2_ppm", "WARNING", co2_ppm,
            thresholds["co2_ppm_max"],
            f"💨 CO₂ at {co2_ppm:.0f} ppm — open the lid to improve aeration",
        )

    if humidity_pct is not None:
        if humidity_pct < thresholds["humidity_pct_min"]:
            _add(
                "humidity_pct", "WARNING", humidity_pct,
                thresholds["humidity_pct_min"],
                f"💧 Humidity {humidity_pct:.0f}% is too low — add moisture to your pile",
            )
        elif humidity_pct > thresholds["humidity_pct_max"]:
            _add(
                "humidity_pct", "WARNING", humidity_pct,
                thresholds["humidity_pct_max"],
                f"💧 Humidity {humidity_pct:.0f}% is too high — add dry browns to balance",
            )

    if ph_level is not None:
        if ph_level < thresholds["ph_min"]:
            _add(
                "ph_level", "WARNING", ph_level,
                thresholds["ph_min"],
                f"⚗️ pH {ph_level:.1f} is too acidic (min {thresholds['ph_min']:.1f}) "
                "— add lime or wood ash",
            )
        elif ph_level > thresholds["ph_max"]:
            _add(
                "ph_level", "WARNING", ph_level,
                thresholds["ph_max"],
                f"⚗️ pH {ph_level:.1f} is too alkaline (max {thresholds['ph_max']:.1f}) "
                "— add coffee grounds or acidic material",
            )

    return alerts


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    queue="telemetry",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="telemetry.process_alert_check",
)
def process_telemetry_alert_check(
    self,
    device_id: str,
    *,
    temperature_c: Optional[float] = None,
    co2_ppm: Optional[float] = None,
    humidity_pct: Optional[float] = None,
    ph_level: Optional[float] = None,
    reading_time: Optional[str] = None,   # ISO-8601 string (JSON-serialisable)
) -> dict:
    """
    Full alert pipeline for a single telemetry reading.

    Steps:
      1. Load per-device thresholds (Redis → DB → defaults)
      2. Evaluate all metrics
      3. Persist AlertEvent rows
      4. Dispatch send_push_notification for each breach

    Returns a dict of triggered alerts (empty = all clear).
    """
    try:
        thresholds = _load_thresholds(device_id)
        alerts = _evaluate(
            thresholds,
            temperature_c=temperature_c,
            co2_ppm=co2_ppm,
            humidity_pct=humidity_pct,
            ph_level=ph_level,
        )

        if not alerts:
            return {"device_id": device_id, "alerts": []}

        ts = (
            datetime.fromisoformat(reading_time)
            if reading_time
            else datetime.now(timezone.utc)
        )

        # Persist to alert_events table
        _persist_alerts(device_id, alerts, ts)

        # Dispatch push notifications to ALL members of the device (equal-access
        # sharing model), not just a single owner.
        from sqlalchemy import select

        from app.db.models.user import User
        from app.db.models.user_device import UserDevice
        from app.workers.db import worker_session
        from app.workers.tasks.notifications import send_push_notification

        try:
            with worker_session() as session:
                member_tokens = session.execute(
                    select(User.id, User.firebase_push_token)
                    .join(UserDevice, UserDevice.user_id == User.id)
                    .where(
                        UserDevice.device_id == uuid.UUID(device_id),
                        User.firebase_push_token.isnot(None),
                    )
                ).all()

            if member_tokens:
                titles = [a["message"][:60] for a in alerts]
                body = " | ".join(titles) if len(titles) > 1 else titles[0]
                severity = (
                    "CRITICAL"
                    if any(a["severity"] == "CRITICAL" for a in alerts)
                    else "WARNING"
                )
                title = (
                    "⚠️ Rawbin Alert"
                    if severity == "WARNING"
                    else "🚨 Rawbin Critical Alert"
                )
                for member_id, token in member_tokens:
                    send_push_notification.apply_async(
                        kwargs={
                            "user_id": str(member_id),
                            "fcm_token": token,
                            "title": title,
                            "body": body,
                            "data": {
                                "device_id": device_id,
                                "alert_count": str(len(alerts)),
                            },
                        },
                        queue="default",
                    )
        except Exception:
            logger.warning(
                "Could not dispatch push notifications",
                extra={"device_id": device_id},
                exc_info=True,
            )

        logger.warning(
            "Thresholds breached",
            extra={"device_id": device_id, "alert_count": len(alerts)},
        )
        return {"device_id": device_id, "alerts": alerts}

    except Exception as exc:
        logger.error(
            "Alert check task failed — retrying",
            extra={"device_id": device_id},
            exc_info=True,
        )
        raise self.retry(exc=exc)
