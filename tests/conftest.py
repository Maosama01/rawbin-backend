"""
tests/conftest.py
──────────────────
Shared pytest fixtures for the Rawbin test suite.

Strategy:
  - Tests run against the live Docker stack (Postgres + Redis).
  - Each test cleans up after itself via TRUNCATE CASCADE after teardown.
  - This is simpler and more reliable than SAVEPOINT rollback, which
    breaks when a flush-level exception poisons the transaction state.
  - NullPool ensures asyncpg connections are not reused across event loops.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app

settings = get_settings()

# Tables to clean between tests (order respects FK constraints)
_TRUNCATE_TABLES = [
    "alert_events",
    "device_configs",
    "sensor_readings",
    "refresh_tokens",
    "devices",
    "users",
]


# ── Per-test engine (function scope to avoid cross-loop asyncpg errors) ───────

@pytest_asyncio.fixture
async def test_engine():
    """Fresh async engine per test. NullPool ensures no connection reuse."""
    engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    yield engine
    await engine.dispose()


# ── Per-test DB session with post-test truncate cleanup ───────────────────────

@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an AsyncSession for the test.
    After the test, TRUNCATE all application tables to restore a clean state.
    """
    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    ) as session:
        try:
            yield session
        finally:
            # Always clean up — even if the test raised an exception
            await session.rollback()
            await session.close()

    # Truncate in a fresh connection so it's unconditional
    async with test_engine.begin() as conn:
        for table in _TRUNCATE_TABLES:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))


# ── Mock Redis ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def mock_redis() -> AsyncMock:
    """Default mock Redis: nonce always absent (expired), all writes succeed."""
    redis = AsyncMock()
    redis.setex = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock(return_value=1)
    redis.ping = AsyncMock(return_value=True)
    return redis


# ── HTTP client with DI overrides ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_client(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTPX async client wired to the FastAPI test app with:
      - DB session override (per-test session, cleaned up via TRUNCATE)
      - Redis mock (no real Redis I/O required)
    """
    from app.api.deps import get_redis

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ── Convenience fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def registered_user(async_client: AsyncClient) -> dict:
    """Register a fresh user and return the full response dict."""
    resp = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "fixture@rawbin.io",
            "password": "FixturePass123!",
            "display_name": "Fixture User",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def auth_headers(registered_user: dict) -> dict:
    """Authorization header for the fixture user."""
    return {"Authorization": f"Bearer {registered_user['tokens']['access_token']}"}


@pytest_asyncio.fixture
async def paired_device(
    async_client: AsyncClient,
    auth_headers: dict,
    mock_redis: AsyncMock,
) -> dict:
    """
    Fully paired device fixture with a known secret.
    Configures mock_redis to return the nonce for the confirm step.
    """
    import hashlib
    import hmac as _hmac

    HARDWARE_UID = "RB-FIXTURE-DEVICE-001"
    DEVICE_SECRET = "fixture-device-secret-xyz"

    # 1. Challenge
    challenge_resp = await async_client.post(
        "/api/v1/devices/pair/challenge",
        json={"hardware_uid": HARDWARE_UID, "display_name": "Fixture Composter"},
        headers=auth_headers,
    )
    assert challenge_resp.status_code == 201, challenge_resp.text
    challenge = challenge_resp.json()
    device_id = challenge["device_id"]
    nonce = challenge["nonce"]

    # 2. Provision secret
    prov = await async_client.post(
        f"/api/v1/devices/{device_id}/provision",
        params={"secret": DEVICE_SECRET},
        headers=auth_headers,
    )
    assert prov.status_code == 204, prov.text

    # 3. Make mock Redis return the nonce so confirm succeeds
    mock_redis.get = AsyncMock(return_value=nonce)

    # 4. Compute HMAC and confirm
    hmac_hex = _hmac.new(
        DEVICE_SECRET.encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()

    confirm = await async_client.post(
        "/api/v1/devices/pair/confirm",
        json={"device_id": device_id, "nonce": nonce, "hmac_response": hmac_hex},
        headers=auth_headers,
    )
    assert confirm.status_code == 200, confirm.text

    # Reset Redis mock to default (nonce absent) after pairing
    mock_redis.get = AsyncMock(return_value=None)

    return {
        "device_id": device_id,
        "hardware_uid": HARDWARE_UID,
        "secret": DEVICE_SECRET,
        "headers": auth_headers,
    }
