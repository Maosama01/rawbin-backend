"""
tests/test_users.py
────────────────────
Integration tests for user profile endpoints:
  GET  /api/v1/users/me
  PATCH /api/v1/users/me
  PUT  /api/v1/users/me/push-token
"""

import pytest
from httpx import AsyncClient


# ── GET /users/me ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_me_returns_profile(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.get("/api/v1/users/me", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["email"] == "fixture@rawbin.io"
    assert data["display_name"] == "Fixture User"
    assert data["is_active"] is True
    assert data["has_push_token"] is False   # no token registered yet


@pytest.mark.asyncio
async def test_get_me_unauthenticated(async_client: AsyncClient):
    r = await async_client.get("/api/v1/users/me")
    assert r.status_code == 403   # no Bearer → HTTPBearer raises 403


# ── PATCH /users/me ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_me_display_name(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.patch(
        "/api/v1/users/me",
        json={"display_name": "New Name"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "New Name"

    # Verify persisted — subsequent GET returns updated name
    r2 = await async_client.get("/api/v1/users/me", headers=auth_headers)
    assert r2.json()["display_name"] == "New Name"


@pytest.mark.asyncio
async def test_patch_me_empty_body_is_noop(async_client: AsyncClient, auth_headers: dict):
    """Empty body should succeed — nothing changes."""
    r = await async_client.patch("/api/v1/users/me", json={}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["display_name"] == "Fixture User"


@pytest.mark.asyncio
async def test_patch_me_display_name_too_long(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.patch(
        "/api/v1/users/me",
        json={"display_name": "x" * 101},
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_me_display_name_empty_string(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.patch(
        "/api/v1/users/me",
        json={"display_name": ""},
        headers=auth_headers,
    )
    assert r.status_code == 422


# ── PUT /users/me/push-token ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_push_token(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.put(
        "/api/v1/users/me/push-token",
        json={"token": "fcm-token-abc123xyz"},
        headers=auth_headers,
    )
    assert r.status_code == 204

    # Profile should now report has_push_token=True
    profile = await async_client.get("/api/v1/users/me", headers=auth_headers)
    assert profile.json()["has_push_token"] is True


@pytest.mark.asyncio
async def test_push_token_can_be_overwritten(async_client: AsyncClient, auth_headers: dict):
    """Calling the endpoint again should overwrite the previous token."""
    for token in ("token-v1-aaabbbccc", "token-v2-xxxyyyzzz"):
        r = await async_client.put(
            "/api/v1/users/me/push-token",
            json={"token": token},
            headers=auth_headers,
        )
        assert r.status_code == 204


@pytest.mark.asyncio
async def test_push_token_too_short(async_client: AsyncClient, auth_headers: dict):
    r = await async_client.put(
        "/api/v1/users/me/push-token",
        json={"token": "short"},
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_push_token_requires_auth(async_client: AsyncClient):
    r = await async_client.put(
        "/api/v1/users/me/push-token",
        json={"token": "fcm-token-abc123xyz"},
    )
    assert r.status_code == 403
