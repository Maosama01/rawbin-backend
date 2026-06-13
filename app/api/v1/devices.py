"""
app/api/v1/devices.py
──────────────────────
Device Pairing routes:
  POST /api/v1/devices/pair/challenge  – register device + issue nonce
  POST /api/v1/devices/pair/confirm    – verify HMAC-SHA256 response, mark as paired
  GET  /api/v1/devices/               – list authenticated user's devices
  POST /api/v1/devices/{id}/provision  – set/replace device secret (admin helper)

HMAC Pairing Handshake
──────────────────────
Step 1 – Challenge:
  App → POST /pair/challenge { hardware_uid, display_name }
  Server → { device_id, nonce, expires_at }
  Nonce stored in Redis with TTL (PAIRING_CHALLENGE_TTL_SECONDS)

Step 2 – BLE:
  App forwards nonce to device firmware over BLE
  Firmware computes: HMAC-SHA256(key=device_secret, msg=nonce)

Step 3 – Confirm:
  App → POST /pair/confirm { device_id, nonce, hmac_response }
  Server: decrypts stored device_secret_enc → verifies HMAC → marks is_paired=True
          Nonce deleted from Redis (one-time use)
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_redis
from app.core.config import get_settings
from app.core.security import encrypt_device_secret, verify_device_hmac
from app.db.models.device import Device
from app.schemas.device import (
    DeviceConfigIn,
    DeviceConfigOut,
    DeviceResponse,
    PairingChallengeRequest,
    PairingChallengeResponse,
    PairingConfirmRequest,
    PairingConfirmResponse,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/devices", tags=["Devices"])

# Redis key pattern for challenge nonces
_CHALLENGE_KEY = "pairing:challenge:{device_id}"
_NONCE_SECRET_KEY = "pairing:secret:{device_id}"   # Stores encrypted secret during pairing


def _challenge_key(device_id: uuid.UUID) -> str:
    return _CHALLENGE_KEY.format(device_id=device_id)


def _secret_key(device_id: uuid.UUID) -> str:
    return _NONCE_SECRET_KEY.format(device_id=device_id)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/pair/challenge",
    response_model=PairingChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a device and receive a pairing challenge nonce",
)
async def pairing_challenge(
    body: PairingChallengeRequest,
    current_user: CurrentUser,
    db: DbSession,
    redis=Depends(get_redis),
) -> PairingChallengeResponse:
    """
    Step 1 of the HMAC pairing handshake.

    - Upserts the device record in the DB (creates on first call).
    - Generates a 256-bit cryptographically random nonce stored in Redis
      with a TTL of PAIRING_CHALLENGE_TTL_SECONDS.
    - Returns the nonce to the mobile app, which forwards it to the device
      over BLE.

    Device secret lifecycle:
      The device secret is factory-provisioned into the device firmware.
      During the challenge, the device secret MUST already be stored in the
      DB (set via the /provision endpoint or a factory import pipeline).
      If no secret is set, the challenge is still issued, but confirm will fail.
    """
    existing = await db.execute(
        select(Device).where(Device.hardware_uid == body.hardware_uid)
    )
    device: Device | None = existing.scalar_one_or_none()

    if device and device.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device is already registered to another account.",
        )

    if device is None:
        # First-time registration — secret must be provisioned separately
        device = Device(
            hardware_uid=body.hardware_uid,
            display_name=body.display_name,
            owner_id=current_user.id,
            # Sentinel: will be replaced by /provision endpoint
            device_secret_enc=encrypt_device_secret("UNPROVISIONED"),
        )
        db.add(device)
        await db.flush()  # populate device.id

    elif not device.is_paired:
        # Allow re-challenge for unpaired device (update display name)
        device.display_name = body.display_name

    # Issue nonce
    nonce = secrets.token_hex(32)  # 256 bits
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.PAIRING_CHALLENGE_TTL_SECONDS
    )

    await redis.setex(_challenge_key(device.id), settings.PAIRING_CHALLENGE_TTL_SECONDS, nonce)

    logger.info("Pairing challenge issued", extra={"device_id": str(device.id)})

    return PairingChallengeResponse(
        device_id=device.id,
        nonce=nonce,
        expires_at=expires_at,
    )


@router.post(
    "/pair/confirm",
    response_model=PairingConfirmResponse,
    summary="Confirm device pairing by verifying HMAC-SHA256 response",
)
async def pairing_confirm(
    body: PairingConfirmRequest,
    current_user: CurrentUser,
    db: DbSession,
    redis=Depends(get_redis),
) -> PairingConfirmResponse:
    """
    Step 2 of the HMAC pairing handshake.

    The server:
      1. Retrieves the nonce from Redis (validates it hasn't expired).
      2. Checks the nonce matches what was issued.
      3. Decrypts the stored device_secret_enc.
      4. Computes HMAC-SHA256(key=device_secret, msg=nonce).
      5. Constant-time compares against hmac_response from the app.
      6. On success: marks device as paired, deletes nonce (one-time use).
    """
    result = await db.execute(select(Device).where(Device.id == body.device_id))
    device: Device | None = result.scalar_one_or_none()

    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    if device.is_paired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device is already paired. Reset the device to re-pair.",
        )

    # Retrieve and validate nonce from Redis
    stored_nonce: str | None = await redis.get(_challenge_key(device.id))

    if stored_nonce is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                f"Pairing challenge has expired (TTL={settings.PAIRING_CHALLENGE_TTL_SECONDS}s). "
                "Initiate a new challenge."
            ),
        )

    if stored_nonce != body.nonce:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nonce mismatch.")

    # ── HMAC Verification ────────────────────────────────────────────────────
    if not verify_device_hmac(device.device_secret_enc, body.nonce, body.hmac_response):
        # Intentionally vague error — don't reveal whether secret or HMAC is wrong
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HMAC verification failed. Ensure the device secret is correctly provisioned.",
        )

    # ── Success: mark paired, consume nonce ──────────────────────────────────
    device.is_paired = True
    await redis.delete(_challenge_key(device.id))

    paired_at = datetime.now(timezone.utc)

    logger.info(
        "Device pairing confirmed via HMAC",
        extra={"device_id": str(device.id), "user_id": str(current_user.id)},
    )

    return PairingConfirmResponse(
        device_id=device.id,
        hardware_uid=device.hardware_uid,
        display_name=device.display_name,
        paired_at=paired_at,
    )


@router.post(
    "/{device_id}/provision",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Provision (or replace) the device secret for HMAC verification",
    description=(
        "Sets the factory shared secret for a device. "
        "The plaintext secret is encrypted with Fernet and stored in the DB. "
        "In production, this endpoint should be restricted to an admin role "
        "or replaced by a secure factory provisioning pipeline."
    ),
)
async def provision_device_secret(
    device_id: uuid.UUID,
    secret: str,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Set or replace the device secret. Forces re-pairing (resets is_paired)."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device: Device | None = result.scalar_one_or_none()

    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    device.device_secret_enc = encrypt_device_secret(secret)
    device.is_paired = False  # Require re-pairing with new secret

    logger.info("Device secret provisioned", extra={"device_id": str(device_id)})


@router.get(
    "/",
    response_model=list[DeviceResponse],
    summary="List all devices owned by the authenticated user",
)
async def list_devices(
    current_user: CurrentUser,
    db: DbSession,
) -> list[Device]:
    """Return all devices registered under the current user's account."""
    result = await db.execute(
        select(Device).where(Device.owner_id == current_user.id)
    )
    return list(result.scalars().all())


# ── Device Config ─────────────────────────────────────────────────────────────

@router.get(
    "/{device_id}/config",
    response_model=DeviceConfigOut,
    summary="Get the alert threshold config for a device",
)
async def get_device_config(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> DeviceConfigOut:
    """
    Return the effective alert thresholds for a device.

    If no custom config row exists, global defaults are returned with
    `is_custom=false`.  The mobile app can use this to populate its
    settings UI without needing to know what the defaults are.
    """
    # Verify ownership
    result = await db.execute(select(Device).where(Device.id == device_id))
    device: Device | None = result.scalar_one_or_none()
    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    from app.db.models.device_config import DeviceConfig
    from app.workers.tasks.telemetry import ALERT_THRESHOLDS
    from app.schemas.device import DeviceConfigOut

    cfg_result = await db.execute(
        select(DeviceConfig).where(DeviceConfig.device_id == device_id)
    )
    cfg: DeviceConfig | None = cfg_result.scalar_one_or_none()

    def _resolve(attr: str, default_key: str) -> float:
        if cfg is not None and getattr(cfg, attr) is not None:
            return getattr(cfg, attr)
        return ALERT_THRESHOLDS[default_key]

    return DeviceConfigOut(
        device_id=device_id,
        temperature_c_max=_resolve("temperature_c_max", "temperature_c_max"),
        temperature_c_min=_resolve("temperature_c_min", "temperature_c_min"),
        co2_ppm_max=_resolve("co2_ppm_max", "co2_ppm_max"),
        humidity_pct_min=_resolve("humidity_pct_min", "humidity_pct_min"),
        humidity_pct_max=_resolve("humidity_pct_max", "humidity_pct_max"),
        ph_min=_resolve("ph_min", "ph_min"),
        ph_max=_resolve("ph_max", "ph_max"),
        is_custom=cfg is not None,
    )


@router.put(
    "/{device_id}/config",
    response_model=DeviceConfigOut,
    summary="Set per-device alert thresholds",
)
async def put_device_config(
    device_id: uuid.UUID,
    body: DeviceConfigIn,
    current_user: CurrentUser,
    db: DbSession,
    redis=Depends(get_redis),
) -> DeviceConfigOut:
    """
    Upsert the per-device alert threshold configuration.

    Any field set to `null` (or omitted) falls back to the global default
    when the alert pipeline runs.  Sending an empty body `{}` effectively
    removes all custom overrides.

    The Redis cache key for this device's config is invalidated immediately
    so the next telemetry alert check picks up the new values.
    """
    from app.db.models.device_config import DeviceConfig
    from app.workers.tasks.telemetry import ALERT_THRESHOLDS
    from app.schemas.device import DeviceConfigIn, DeviceConfigOut

    # Verify ownership
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device: Device | None = dev_result.scalar_one_or_none()
    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    # Upsert
    cfg_result = await db.execute(
        select(DeviceConfig).where(DeviceConfig.device_id == device_id)
    )
    cfg: DeviceConfig | None = cfg_result.scalar_one_or_none()

    if cfg is None:
        cfg = DeviceConfig(device_id=device_id)
        db.add(cfg)

    cfg.temperature_c_max = body.temperature_c_max
    cfg.temperature_c_min = body.temperature_c_min
    cfg.co2_ppm_max       = body.co2_ppm_max
    cfg.humidity_pct_min  = body.humidity_pct_min
    cfg.humidity_pct_max  = body.humidity_pct_max
    cfg.ph_min            = body.ph_min
    cfg.ph_max            = body.ph_max

    await db.flush()

    # Invalidate Redis cache so worker picks up new values immediately
    cache_key = f"alert_config:{device_id}"
    await redis.delete(cache_key)

    logger.info("Device config updated", extra={"device_id": str(device_id)})

    def _resolve(attr: str, default_key: str) -> float:
        v = getattr(cfg, attr)
        return v if v is not None else ALERT_THRESHOLDS[default_key]

    return DeviceConfigOut(
        device_id=device_id,
        temperature_c_max=_resolve("temperature_c_max", "temperature_c_max"),
        temperature_c_min=_resolve("temperature_c_min", "temperature_c_min"),
        co2_ppm_max=_resolve("co2_ppm_max", "co2_ppm_max"),
        humidity_pct_min=_resolve("humidity_pct_min", "humidity_pct_min"),
        humidity_pct_max=_resolve("humidity_pct_max", "humidity_pct_max"),
        ph_min=_resolve("ph_min", "ph_min"),
        ph_max=_resolve("ph_max", "ph_max"),
        is_custom=True,
    )

