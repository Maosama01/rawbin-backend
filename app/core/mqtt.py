"""
app/core/mqtt.py
────────────────
MQTT Client that connects to Mosquitto and ingests telemetry.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import paho.mqtt.client as mqtt

from app.core.config import get_settings
from app.db.models.device import Device
from app.db.models.sensor_reading import SensorReading
from app.db.session import AsyncSessionLocal
from app.schemas.telemetry import SensorReadingIn
from app.workers.tasks.telemetry import process_telemetry_alert_check

logger = logging.getLogger(__name__)
settings = get_settings()


class MQTTIngestionClient:
    """Background MQTT client to subscribe to telemetry topics and insert rows."""
    
    def __init__(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.MQTT_CLIENT_ID
        )
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self._loop = None

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Connected to MQTT broker successfully.")
            # Subscribe to rawbin/telemetry/<device_id>
            client.subscribe("rawbin/telemetry/+")
        else:
            logger.error(f"Failed to connect to MQTT broker, reason_code: {reason_code}")

    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT message. Runs in the paho-mqtt network thread."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        
        try:
            device_id_str = topic.split("/")[-1]
            device_id = uuid.UUID(device_id_str)
        except Exception:
            logger.warning(f"Invalid MQTT topic (cannot extract UUID): {topic}")
            return
            
        try:
            data = json.loads(payload)
            reading = SensorReadingIn(**data)
        except Exception as e:
            logger.warning(f"Invalid MQTT payload for {device_id_str}: {e}")
            return

        # Since paho-mqtt runs synchronously, we schedule the async DB insertion
        # on the main event loop thread.
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.process_telemetry_async(device_id, reading),
                self._loop
            )

    async def process_telemetry_async(self, device_id: uuid.UUID, reading: SensorReadingIn):
        """Async function to insert reading and trigger alerts."""
        async with AsyncSessionLocal() as db:
            try:
                # 1. Ensure device exists
                from sqlalchemy import select
                result = await db.execute(select(Device).where(Device.id == device_id))
                device = result.scalar_one_or_none()
                if not device:
                    logger.warning(f"MQTT telemetry received for unknown device {device_id}")
                    return

                # 2. Insert row
                from datetime import datetime, timezone
                ts = reading.time or datetime.now(timezone.utc)
                row_data = {
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
                
                row = SensorReading(**row_data)
                if reading.firmware_version:
                    device.firmware_version = reading.firmware_version

                db.add(row)
                await db.commit()
                
                # 3. Fire alert task
                try:
                    process_telemetry_alert_check.apply_async(
                        args=[str(device_id)],
                        kwargs={
                            "temperature_c": reading.temperature_c,
                            "co2_ppm": reading.co2_ppm,
                            "humidity_pct": reading.humidity_pct,
                            "ph_level": reading.ph_level,
                            "reading_time": ts.isoformat(),
                        },
                        queue="telemetry",
                    )
                except Exception:
                    pass
                
                logger.info(f"Inserted MQTT telemetry for {device_id}")
            except Exception as e:
                logger.error(f"Error processing MQTT telemetry: {e}")
                await db.rollback()

    def start(self):
        """Connects and starts the MQTT loop in a background thread."""
        self._loop = asyncio.get_running_loop()
        
        try:
            logger.info(f"Connecting to MQTT broker at {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
            self.client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, 60)
            self.client.loop_start()  # Runs network loop in a background thread
        except Exception as e:
            logger.error(f"Could not connect to MQTT broker: {e}")

    def stop(self):
        """Stops the MQTT background thread."""
        self.client.loop_stop()
        self.client.disconnect()


mqtt_client = MQTTIngestionClient()
