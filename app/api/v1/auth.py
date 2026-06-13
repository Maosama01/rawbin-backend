"""
app/api/v1/auth.py
───────────────────
Authentication routes:
  POST /api/v1/auth/register   – create new user account
  POST /api/v1/auth/login      – issue access + refresh tokens
  POST /api/v1/auth/refresh    – rotate refresh token, issue new access token
  POST /api/v1/auth/logout     – revoke the supplied refresh token
  POST /api/v1/auth/device     – device authenticates with hardware_uid + secret

The actual business logic lives in app/services/auth_service.py (to be
implemented). Routes are intentionally thin: validate input → delegate →
return response.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decrypt_device_secret,
    hash_password,
    verify_password,
)
from app.db.models.device import Device
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.schemas.auth import (
    DeviceAuthRequest,
    RefreshRequest,
    RegisterResponse,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    """SHA-256 hex digest of an opaque token string for DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _store_refresh_token(db: DbSession, user_id: uuid.UUID, raw_token: str) -> None:
    """Persist a hashed refresh token record."""
    rt = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(rt)
    await db.flush()  # Ensure the row is visible to subsequent SELECTs in the same tx


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(body: UserRegisterRequest, db: DbSession) -> RegisterResponse:
    """
    Create a new user, hash their password, and return tokens so they are
    immediately authenticated after sign-up.
    """
    # Idempotency: reject duplicate emails
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    await db.flush()  # populate user.id without committing

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    await _store_refresh_token(db, user.id, refresh_token)

    logger.info("New user registered", extra={"user_id": str(user.id)})

    return RegisterResponse(
        user=UserResponse.model_validate(user),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        ),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
async def login(body: UserLoginRequest, db: DbSession) -> TokenResponse:
    """Return access and refresh tokens for valid credentials."""
    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    # Constant-time rejection regardless of whether user exists
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    await _store_refresh_token(db, user.id, refresh_token)

    logger.info("User logged in", extra={"user_id": str(user.id)})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue new access token",
)
async def refresh(body: RefreshRequest, db: DbSession) -> TokenResponse:
    """
    Validate the supplied refresh token, rotate it (revoke old, issue new),
    and return a fresh access token.
    """
    token_hash = _hash_token(body.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt: RefreshToken | None = result.scalar_one_or_none()

    if rt is None or not rt.is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid or has expired.",
        )

    # Rotate: revoke the current token
    rt.revoked = True

    # Issue new pair
    new_access = create_access_token(str(rt.user_id))
    new_refresh = create_refresh_token(str(rt.user_id))
    await _store_refresh_token(db, rt.user_id, new_refresh)

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token (logout)",
)
async def logout(body: RefreshRequest, db: DbSession) -> None:
    """Mark the refresh token as revoked. Silent success if already revoked."""
    token_hash = _hash_token(body.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt: RefreshToken | None = result.scalar_one_or_none()
    if rt and not rt.revoked:
        rt.revoked = True


@router.post(
    "/device",
    response_model=TokenResponse,
    summary="Authenticate a paired device with hardware_uid + device_secret",
)
async def device_auth(body: DeviceAuthRequest, db: DbSession) -> TokenResponse:
    """
    Devices use this endpoint to obtain an access token for submitting telemetry.
    The device must have completed the pairing handshake first.
    """
    result = await db.execute(
        select(Device).where(Device.hardware_uid == body.hardware_uid)
    )
    device: Device | None = result.scalar_one_or_none()

    if device is None or not device.is_paired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device not found or not yet paired.",
        )

    # Decrypt stored Fernet secret and do a constant-time string comparison
    try:
        stored_secret = decrypt_device_secret(device.device_secret_enc)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device credentials.",
        )

    import hmac as _hmac
    if not _hmac.compare_digest(stored_secret, body.device_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device credentials.",
        )

    # Scope: device tokens carry the device UUID, not a user UUID
    access_token = create_access_token(f"device:{device.id}")

    return TokenResponse(
        access_token=access_token,
        refresh_token="",  # Devices do not use refresh tokens
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
