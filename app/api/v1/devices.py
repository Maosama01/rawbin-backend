"""
app/api/v1/devices.py
──────────────────────
Device pairing, sharing, and per-device config.

  POST   /api/v1/devices/pair/challenge   – register device + issue nonce
  POST   /api/v1/devices/pair/confirm     – verify HMAC, mark paired
  POST   /api/v1/devices/{id}/provision   – set/replace device secret
  GET    /api/v1/devices/                 – list the caller's devices
  POST   /api/v1/devices/{id}/share       – add another user as a member
  GET    /api/v1/devices/{id}/members     – list members
  DELETE /api/v1/devices/{id}/members/{user_id} – remove a member (or leave)
  GET    /api/v1/devices/{id}/config      – read effective alert thresholds
  PUT    /api/v1/devices/{id}/config      – set per-device alert thresholds

Sharing model: all members are equal (see UserDevice). Any member may pair,
configure, share, or remove members. Access is enforced via
app.services.device_access.

HMAC Pairing Handshake
──────────────────────
Step 1 – Challenge:  App → {hardware_uid, display_name};  Server → {device_id, nonce}
Step 2 – BLE:        App forwards nonce to firmware → HMAC-SHA256(secret, nonce)
Step 3 – Confirm:    App → {device_id, nonce, hmac_response};  Server verifies → paired
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, RedisDep
from app.core.config import get_settings
from app.core.security import encrypt_device_secret, verify_device_hmac
from app.db.models.device import Device
from app.db.models.user import User
from app.db.models.user_device import UserDevice
from app.schemas.device import (
    DeviceConfigIn,
    DeviceConfigOut,
    DeviceMemberOut,
    DeviceResponse,
    DeviceShareRequest,
    PairingChallengeRequest,
    PairingChallengeResponse,
    PairingConfirmRequest,
    PairingConfirmResponse,
)
from app.services import device_access

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/devices", tags=["Devices"])

_CHALLENGE_KEY = "pairing:challenge:{device_id}"


def _challenge_key(device_id: uuid.UUID) -> str:
    return _CHALLENGE_KEY.format(device_id=device_id)


# ── Pairing ───────────────────────────────────────────────────────────────────

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
    redis: RedisDep,
) -> PairingChallengeResponse:
    """
    Step 1 of the HMAC pairing handshake.

    On first registration the device row is created and the caller is linked as
    its first member. If the device already exists, the caller must already be a
    member to re-challenge it.
    """
    existing = await db.execute(
        select(Device).where(Device.hardware_uid == body.hardware_uid)
    )
    device: Device | None = existing.scalar_one_or_none()

    if device is None:
        # First-time registration — secret provisioned separately via /provision.
        device = Device(
            hardware_uid=body.hardware_uid,
            display_name=body.display_name,
            device_secret_enc=encrypt_device_secret("UNPROVISIONED"),
        )
        db.add(device)
        await db.flush()  # populate device.id
        await device_access.add_member(db, device.id, current_user.id)
    else:
        if not await device_access.is_member(db, device.id, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This device is already registered to another account.",
            )
        if not device.is_paired:
            device.display_name = body.display_name

    nonce = secrets.token_hex(32)  # 256 bits
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.PAIRING_CHALLENGE_TTL_SECONDS
    )
    await redis.setex(
        _challenge_key(device.id), settings.PAIRING_CHALLENGE_TTL_SECONDS, nonce
    )
    logger.info("Pairing challenge issued", extra={"device_id": str(device.id)})

    return PairingChallengeResponse(device_id=device.id, nonce=nonce, expires_at=expires_at)


@router.post(
    "/pair/confirm",
    response_model=PairingConfirmResponse,
    summary="Confirm device pairing by verifying HMAC-SHA256 response",
)
async def pairing_confirm(
    body: PairingConfirmRequest,
    current_user: CurrentUser,
    db: DbSession,
    redis: RedisDep,
) -> PairingConfirmResponse:
    """Step 3: verify the device's HMAC response and mark it paired."""
    device = await device_access.assert_device_member(db, body.device_id, current_user.id)

    if device.is_paired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device is already paired. Reset the device to re-pair.",
        )

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

    if not verify_device_hmac(device.device_secret_enc, body.nonce, body.hmac_response):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HMAC verification failed. Ensure the device secret is correctly provisioned.",
        )

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
        "Sets the factory shared secret for a device. The plaintext is Fernet-"
        "encrypted before storage. In production, restrict to an admin role or a "
        "secure factory provisioning pipeline."
    ),
)
async def provision_device_secret(
    device_id: uuid.UUID,
    secret: str,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Set or replace the device secret. Forces re-pairing (resets is_paired)."""
    device = await device_access.assert_device_member(db, device_id, current_user.id)
    device.device_secret_enc = encrypt_device_secret(secret)
    device.is_paired = False
    logger.info("Device secret provisioned", extra={"device_id": str(device_id)})


# ── Listing & sharing ─────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=list[DeviceResponse],
    summary="List all devices the authenticated user can access",
)
async def list_devices(current_user: CurrentUser, db: DbSession) -> list[Device]:
    """Return every device the current user is a member of."""
    result = await db.execute(
        select(Device)
        .join(UserDevice, UserDevice.device_id == Device.id)
        .where(UserDevice.user_id == current_user.id)
        .order_by(Device.created_at.asc())
    )
    return list(result.scalars().all())


@router.post(
    "/{device_id}/share",
    response_model=list[DeviceMemberOut],
    summary="Grant another user equal access to a device",
)
async def share_device(
    device_id: uuid.UUID,
    body: DeviceShareRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> list[DeviceMemberOut]:
    """Add an existing user (by email) as a member. Returns the updated member list."""
    await device_access.assert_device_member(db, device_id, current_user.id)

    target = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user with that email address.",
        )

    await device_access.add_member(db, device_id, target.id)
    logger.info(
        "Device shared",
        extra={"device_id": str(device_id), "with_user": str(target.id)},
    )
    return await _list_members(db, device_id)


@router.get(
    "/{device_id}/members",
    response_model=list[DeviceMemberOut],
    summary="List the members of a device",
)
async def list_members(
    device_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> list[DeviceMemberOut]:
    await device_access.assert_device_member(db, device_id, current_user.id)
    return await _list_members(db, device_id)


@router.delete(
    "/{device_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a device (or leave it yourself)",
)
async def remove_member(
    device_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """
    Remove *user_id* from the device. Any member may remove any member (equal
    access). Removing the last member is blocked to avoid orphaning the device.
    """
    await device_access.assert_device_member(db, device_id, current_user.id)

    if await device_access.member_count(db, device_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the only member. Delete the device instead.",
        )
    await device_access.remove_member(db, device_id, user_id)
    logger.info(
        "Device member removed",
        extra={"device_id": str(device_id), "removed_user": str(user_id)},
    )


async def _list_members(db: DbSession, device_id: uuid.UUID) -> list[DeviceMemberOut]:
    rows = await db.execute(
        select(User.id, User.email, User.display_name)
        .join(UserDevice, UserDevice.user_id == User.id)
        .where(UserDevice.device_id == device_id)
        .order_by(User.created_at.asc())
    )
    return [
        DeviceMemberOut(user_id=r.id, email=r.email, display_name=r.display_name)
        for r in rows
    ]


# ── Device Config ─────────────────────────────────────────────────────────────

@router.get(
    "/{device_id}/config",
    response_model=DeviceConfigOut,
    summary="Get the alert threshold config for a device",
)
async def get_device_config(
    device_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> DeviceConfigOut:
    """Return effective alert thresholds (defaults filled in for unset values)."""
    await device_access.assert_device_member(db, device_id, current_user.id)

    from app.db.models.device_config import DeviceConfig
    from app.workers.tasks.telemetry import ALERT_THRESHOLDS

    cfg = (
        await db.execute(select(DeviceConfig).where(DeviceConfig.device_id == device_id))
    ).scalar_one_or_none()

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
    redis: RedisDep,
) -> DeviceConfigOut:
    """Upsert per-device alert thresholds and invalidate the worker's cache."""
    from app.db.models.device_config import DeviceConfig
    from app.workers.tasks.telemetry import ALERT_THRESHOLDS

    await device_access.assert_device_member(db, device_id, current_user.id)

    cfg = (
        await db.execute(select(DeviceConfig).where(DeviceConfig.device_id == device_id))
    ).scalar_one_or_none()
    if cfg is None:
        cfg = DeviceConfig(device_id=device_id)
        db.add(cfg)

    cfg.temperature_c_max = body.temperature_c_max
    cfg.temperature_c_min = body.temperature_c_min
    cfg.co2_ppm_max = body.co2_ppm_max
    cfg.humidity_pct_min = body.humidity_pct_min
    cfg.humidity_pct_max = body.humidity_pct_max
    cfg.ph_min = body.ph_min
    cfg.ph_max = body.ph_max
    await db.flush()

    await redis.delete(f"alert_config:{device_id}")
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
