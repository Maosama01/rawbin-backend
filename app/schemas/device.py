"""
app/schemas/device.py
──────────────────────
Pydantic v2 schemas for Device Pairing API.

The pairing flow:
  1. App sends hardware_uid  →  server stores device stub, returns challenge nonce.
  2. Firmware signs the nonce with HMAC-SHA256 using the device secret.
  3. App forwards the HMAC response  →  server verifies and marks device as paired.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Pairing: Challenge ────────────────────────────────────────────────────────

class PairingChallengeRequest(BaseModel):
    """Body for POST /api/v1/devices/pair/challenge"""

    hardware_uid: str = Field(
        ...,
        max_length=128,
        description="Immutable hardware identifier burned into the device firmware.",
        examples=["RWB-A1B2C3D4E5F6"],
    )
    display_name: str = Field(
        default="My Rawbin",
        max_length=100,
        description="User-editable name for this composter.",
    )


class PairingChallengeResponse(BaseModel):
    """
    Server response to a pairing challenge request.

    `device_id`  – The UUID assigned to this device in the Rawbin database.
                   The app should persist this locally.
    `nonce`      – A cryptographically random string the device must sign.
    `expires_at` – UTC timestamp after which the nonce is invalid.
    """

    device_id: uuid.UUID
    nonce: str
    expires_at: datetime


# ── Pairing: Confirm ──────────────────────────────────────────────────────────

class PairingConfirmRequest(BaseModel):
    """Body for POST /api/v1/devices/pair/confirm"""

    device_id: uuid.UUID = Field(
        ...,
        description="The device UUID returned by the challenge endpoint.",
    )
    nonce: str = Field(
        ...,
        description="The same nonce that was issued in the challenge response.",
    )
    hmac_response: str = Field(
        ...,
        description=(
            "Hex-encoded HMAC-SHA256(key=device_secret, msg=nonce) "
            "computed by the device firmware."
        ),
        examples=["4e99d..."],
    )


class PairingConfirmResponse(BaseModel):
    """Returned when pairing is successfully confirmed."""

    device_id: uuid.UUID
    hardware_uid: str
    display_name: str
    paired_at: datetime


# ── Device Detail ─────────────────────────────────────────────────────────────

class DeviceResponse(BaseModel):
    """General device representation (used in list/get endpoints)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    hardware_uid: str
    display_name: str
    is_paired: bool
    firmware_version: str | None
    created_at: datetime


# ── Device Config (per-device alert thresholds) ───────────────────────────────

class DeviceConfigOut(BaseModel):
    """
    Resolved per-device alert thresholds.
    All values are *effective* — NULL DB fields are pre-filled with global defaults,
    so the mobile app always receives a complete config object.
    """
    device_id: uuid.UUID
    temperature_c_max: float
    temperature_c_min: float
    co2_ppm_max: float
    humidity_pct_min: float
    humidity_pct_max: float
    ph_min: float
    ph_max: float
    is_custom: bool = Field(
        description="True if a device_configs row exists for this device."
    )


class DeviceConfigIn(BaseModel):
    """
    Partial update for per-device alert thresholds.
    Any omitted / null field retains the global default.
    """
    temperature_c_max: float | None = Field(default=None, gt=0, le=120)
    temperature_c_min: float | None = Field(default=None, ge=0, lt=120)
    co2_ppm_max: float | None = Field(default=None, gt=0, le=50_000)
    humidity_pct_min: float | None = Field(default=None, ge=0, le=100)
    humidity_pct_max: float | None = Field(default=None, ge=0, le=100)
    ph_min: float | None = Field(default=None, ge=0, le=14)
    ph_max: float | None = Field(default=None, ge=0, le=14)
