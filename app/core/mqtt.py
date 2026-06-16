"""
app/core/mqtt.py
────────────────
MQTT ingestion client: subscribes to telemetry topics and writes readings to
the TimescaleDB hypertable.

This is the **only** ingestion path for sensor data.  It is intended to run in
a dedicated, standalone process (see app/workers/mqtt_listener.py) — NOT inside
the API process — so that a single subscriber owns the broker connection and
scaling/restarting the API never duplicates or interrupts ingestion.

Concurrency model
─────────────────
paho-mqtt is synchronous and runs its network loop on its own background
thread.  The database layer is async (asyncpg) and lives on an event loop.
Incoming messages therefore cross from the paho thread to the event loop via
`asyncio.run_coroutine_threadsafe`, which is the one thread-safe doorway into
a running loop.  The owning process must pass in the event loop via `start()`.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.device import Device
from app.db.models.sensor_reading import SensorReading
from app.db.session import AsyncSessionLocal
from app.schemas.telemetry import SensorReadingIn

logger = logging.getLogger(__name__)
settings = get_settings()

TELEMETRY_TOPIC = "rawbin/telemetry/+"


class MQTTIngestionClient:
    """Subscribes to telemetry topics and inserts sensor readings."""

    def __init__(self) -> None:
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.MQTT_CLIENT_ID,
        )
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── paho callbacks (run on the paho network thread) ───────────────────────

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Connected to MQTT broker successfully.")
            client.subscribe(TELEMETRY_TOPIC)
        else:
            logger.error("Failed to connect to MQTT broker, reason_code=%s", reason_code)

    def on_message(self, client, userdata, msg):
        """Handle one MQTT message. Runs on the paho network thread."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        try:
            device_id = uuid.UUID(topic.split("/")[-1])
        except Exception:
            logger.warning("Invalid MQTT topic (cannot extract device UUID): %s", topic)
            return

        try:
            data = json.loads(payload)
            reading = SensorReadingIn(**data)  # `time` is required (see schema)
        except Exception as e:
            logger.warning("Invalid MQTT payload for %s: %s", device_id, e)
            return

        if self._loop is None or not self._loop.is_running():
            logger.error("Event loop not available — dropping reading for %s", device_id)
            return

        asyncio.run_coroutine_threadsafe(
            self.process_telemetry_async(device_id, reading), self._loop
        )

    # ── async DB write (runs on the event loop) ───────────────────────────────

    async def process_telemetry_async(
        self, device_id: uuid.UUID, reading: SensorReadingIn
    ) -> None:
        async with AsyncSessionLocal() as db:
            try:
                device = (
                    await db.execute(select(Device).where(Device.id == device_id))
                ).scalar_one_or_none()
                if device is None:
                    logger.warning("Telemetry for unknown device %s — dropped", device_id)
                    return

                row = SensorReading(
                    time=reading.time,
                    device_id=device_id,
                    temperature_c=reading.temperature_c,
                    humidity_pct=reading.humidity_pct,
                    co2_ppm=reading.co2_ppm,
                    ph_level=reading.ph_level,
                    ambient_temp_c=reading.ambient_temp_c,
                    fan_speed_rpm=reading.fan_speed_rpm,
                    fill_level_pct=reading.fill_level_pct,
                    weight_kg=reading.weight_kg,
                    firmware_version=reading.firmware_version,
                )
                if reading.firmware_version:
                    device.firmware_version = reading.firmware_version

                db.add(row)
                await db.commit()
                logger.info("Inserted telemetry for %s @ %s", device_id, reading.time)

                # Fire the post-ingestion alert check (Celery). Failure to enqueue
                # must not lose the already-committed reading, but it IS logged
                # (previously swallowed silently).
                self._dispatch_alert_check(device_id, reading)
            except Exception:
                logger.error("Error processing telemetry for %s", device_id, exc_info=True)
                await db.rollback()

    @staticmethod
    def _dispatch_alert_check(device_id: uuid.UUID, reading: SensorReadingIn) -> None:
        from app.workers.tasks.telemetry import process_telemetry_alert_check

        try:
            process_telemetry_alert_check.apply_async(
                args=[str(device_id)],
                kwargs={
                    "temperature_c": reading.temperature_c,
                    "co2_ppm": reading.co2_ppm,
                    "humidity_pct": reading.humidity_pct,
                    "ph_level": reading.ph_level,
                    "reading_time": reading.time.isoformat(),
                },
                queue="telemetry",
            )
        except Exception:
            logger.warning(
                "Could not enqueue alert check for %s (reading was still saved)",
                device_id,
                exc_info=True,
            )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Connect and start paho's network thread, bridging to *loop*."""
        self._loop = loop
        logger.info(
            "Connecting to MQTT broker at %s:%s",
            settings.MQTT_BROKER_HOST,
            settings.MQTT_BROKER_PORT,
        )
        self.client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, 60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
