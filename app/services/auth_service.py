"""
app/services/auth_service.py
─────────────────────────────
Token issuance and refresh-token persistence.

Centralises the "issue an access+refresh pair and record the refresh token"
logic shared by the password-login, SMS-OTP-login, registration, and refresh
flows, so the route handlers stay thin.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.db.models.refresh_token import RefreshToken
from app.schemas.auth import TokenResponse

settings = get_settings()


def hash_token(token: str) -> str:
    """SHA-256 hex digest of an opaque token string for DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def store_refresh_token(
    db: AsyncSession, user_id: uuid.UUID, raw_token: str
) -> None:
    """Persist a hashed refresh-token record (raw token never stored)."""
    rt = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(rt)
    await db.flush()


async def issue_token_pair(db: AsyncSession, user_id: uuid.UUID) -> TokenResponse:
    """Create an access+refresh pair, persist the refresh token, return the response."""
    subject = str(user_id)
    access_token = create_access_token(subject)
    refresh_token = create_refresh_token(subject)
    await store_refresh_token(db, user_id, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
