"""
app/api/deps.py
────────────────
Shared FastAPI dependencies injected across route handlers.

Provides:
  - get_db        : Async database session (delegates to db/session.py)
  - get_redis     : Redis connection from a shared pool
  - get_current_user : Validates bearer JWT and returns the authenticated User
"""

import hashlib
import logging
from typing import Annotated, AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_token
from app.db.models.user import User
from app.db.models.refresh_token import RefreshToken
from app.db.session import get_db

logger = logging.getLogger(__name__)
settings = get_settings()

# ── HTTP Bearer scheme ────────────────────────────────────────────────────────
bearer_scheme = HTTPBearer(auto_error=True)

# ── Redis connection pool (module-level singleton) ───────────────────────────
_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return a shared async Redis connection (pool-backed)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


# ── Database session re-export ────────────────────────────────────────────────
# Aliased here so route files only need to import from app.api.deps
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ── Current authenticated user ────────────────────────────────────────────────
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    """
    Validate the Bearer JWT in the Authorization header.

    Steps:
      1. Decode and validate the JWT signature + expiry.
      2. Extract subject (user ID).
      3. Load the User from the database (ensures the account still exists and
         is active).

    Raises HTTP 401 on any failure so the client knows to re-authenticate.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(credentials.credentials)
        subject: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")

        if subject is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == subject))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
