"""
app/schemas/telemetry.py
─────────────────────────
Pydantic v2 schemas for sensor telemetry ingestion and device snapshot.

A single reading is a sparse object — not all sensors are present on all
hardware variants, so every measurement field is optional. The API accepts
both single readings and batched arrays of readings.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Inbound: what the mobile app (BLE gateway) sends ─────────────────────────

class SensorReadingIn(BaseModel):
    """
    A single sensor snapshot published by a device over MQTT.

    `time` is REQUIRED: the device (or its BLE gateway) must stamp every reading
    with the moment it was captured. The server no longer invents timestamps —
    that previously caused primary-key collisions on the (time, device_id)
    hypertable and produced misleading "received-at" times. A payload without
    `time` is rejected as invalid.
    """

    time: datetime = Field(
        ...,
        description="UTC timestamp of the reading (required; set by the device).",
    )

    # ── Sensor measurements — all optional ────────────────────────────────────
    temperature_c: Optional[float] = Field(
        default=None,
        ge=-40.0,
        le=150.0,
        description="Chamber temperature in °C",
        examples=[58.3],
    )
    humidity_pct: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Relative humidity in %",
        examples=[72.5],
    )
    co2_ppm: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=50000.0,
        description="CO₂ concentration in ppm",
        examples=[1240.0],
    )
    ph_level: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=14.0,
        description="pH of composting material",
        examples=[6.8],
    )
    ambient_temp_c: Optional[float] = Field(
        default=None,
        ge=-40.0,
        le=85.0,
        description="Ambient / external temperature in °C",
        examples=[22.1],
    )
    fan_speed_rpm: Optional[int] = Field(
        default=None,
        ge=0,
        le=10000,
        description="Active fan speed in RPM",
        examples=[1200],
    )
    fill_level_pct: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Bin fill level from ultrasonic sensor in %",
        examples=[45.0],
    )
    weight_kg: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=50.0,
        description="Material weight in kg",
        examples=[3.2],
    )
    firmware_version: Optional[str] = Field(
        default=None,
        max_length=50,
        examples=["1.4.2"],
    )


# ── Outbound: what the API returns ────────────────────────────────────────────

class SensorReadingOut(BaseModel):
    """Persisted sensor reading, as returned by the snapshot endpoint."""

    model_config = {"from_attributes": True}

    time: datetime
    device_id: uuid.UUID
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    co2_ppm: Optional[float] = None
    ph_level: Optional[float] = None
    ambient_temp_c: Optional[float] = None
    fan_speed_rpm: Optional[int] = None
    fill_level_pct: Optional[float] = None
    weight_kg: Optional[float] = None
    firmware_version: Optional[str] = None


class DeviceSnapshotResponse(BaseModel):
    """Latest device status returned by GET /api/v1/status/{device_id}."""

    device_id: uuid.UUID
    hardware_uid: str
    display_name: str
    is_paired: bool
    firmware_version: Optional[str] = None
    latest_reading: Optional[SensorReadingOut] = None
    reading_age_seconds: Optional[float] = Field(
        default=None,
        description="Seconds since the most recent reading was received.",
    )


# ── History response ──────────────────────────────────────────────────────────

class TelemetryHistoryPoint(BaseModel):
    """
    One data point in a time-series history response.

    For `interval=raw`, each field is the exact sensor value.
    For `interval=hour` or `interval=day`, avg/min/max are provided.
    """
    bucket: datetime                          # start of the time bucket
    temperature_c_avg: Optional[float] = None
    temperature_c_min: Optional[float] = None
    temperature_c_max: Optional[float] = None
    humidity_pct_avg: Optional[float] = None
    co2_ppm_avg: Optional[float] = None
    ph_level_avg: Optional[float] = None
    fan_speed_rpm_avg: Optional[float] = None


class TelemetryRawPoint(BaseModel):
    """One raw sensor reading for the `interval=raw` tier."""
    time: datetime
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    co2_ppm: Optional[float] = None
    ph_level: Optional[float] = None
    ambient_temp_c: Optional[float] = None
    fan_speed_rpm: Optional[int] = None
    fill_level_pct: Optional[float] = None
    weight_kg: Optional[float] = None
    firmware_version: Optional[str] = None


class TelemetryHistoryResponse(BaseModel):
    """Response envelope for the telemetry history endpoint."""
    device_id: uuid.UUID
    interval: str           # "raw" | "hour" | "day"
    from_: datetime = Field(alias="from")
    to: datetime
    count: int
    readings: list          # TelemetryHistoryPoint | TelemetryRawPoint

    model_config = {"populate_by_name": True}
