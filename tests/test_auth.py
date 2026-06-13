"""
tests/test_auth.py
───────────────────
Integration tests for the authentication routes:
  POST /api/v1/auth/register
  POST /api/v1/auth/login
  POST /api/v1/auth/refresh
  POST /api/v1/auth/logout
"""

import pytest
from httpx import AsyncClient


# ── /register ────────────────────────────────────────────────────────────────

class TestRegister:
    async def test_register_success(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "new@rawbin.io",
                "password": "Str0ngPass!",
                "display_name": "New User",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["email"] == "new@rawbin.io"
        assert body["user"]["display_name"] == "New User"
        assert body["user"]["is_active"] is True
        assert "access_token" in body["tokens"]
        assert "refresh_token" in body["tokens"]
        assert body["tokens"]["token_type"] == "bearer"
        assert body["tokens"]["expires_in"] == 900  # 15 min

    async def test_register_duplicate_email(self, async_client: AsyncClient):
        payload = {
            "email": "dup@rawbin.io",
            "password": "Str0ngPass!",
            "display_name": "First",
        }
        r1 = await async_client.post("/api/v1/auth/register", json=payload)
        assert r1.status_code == 201

        r2 = await async_client.post(
            "/api/v1/auth/register",
            json={**payload, "display_name": "Second"},
        )
        assert r2.status_code == 409
        assert "already exists" in r2.json()["detail"]

    async def test_register_missing_fields(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/auth/register",
            json={"email": "incomplete@rawbin.io"},
        )
        assert resp.status_code == 422  # Pydantic validation error

    async def test_register_invalid_email(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "Pass123!", "display_name": "X"},
        )
        assert resp.status_code == 422


# ── /login ────────────────────────────────────────────────────────────────────

class TestLogin:
    async def test_login_success(
        self, async_client: AsyncClient, registered_user: dict
    ):
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "fixture@rawbin.io", "password": "FixturePass123!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    async def test_login_wrong_password(
        self, async_client: AsyncClient, registered_user: dict
    ):
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "fixture@rawbin.io", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.json()["detail"]

    async def test_login_nonexistent_user(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@rawbin.io", "password": "Whatever123!"},
        )
        assert resp.status_code == 401

    async def test_login_returns_bearer_type(
        self, async_client: AsyncClient, registered_user: dict
    ):
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "fixture@rawbin.io", "password": "FixturePass123!"},
        )
        assert resp.json()["token_type"] == "bearer"


# ── /refresh ──────────────────────────────────────────────────────────────────

class TestRefresh:
    async def test_refresh_success(
        self, async_client: AsyncClient, registered_user: dict
    ):
        refresh_token = registered_user["tokens"]["refresh_token"]
        resp = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        # New refresh token should differ (rotation)
        assert body["refresh_token"] != refresh_token

    async def test_refresh_token_rotation_invalidates_old(
        self, async_client: AsyncClient, registered_user: dict
    ):
        """Using a refresh token twice should fail on the second use."""
        refresh_token = registered_user["tokens"]["refresh_token"]

        r1 = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert r1.status_code == 200

        # The original token is now revoked
        r2 = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert r2.status_code == 401

    async def test_refresh_invalid_token(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "completely.invalid.token"},
        )
        assert resp.status_code == 401


# ── /logout ───────────────────────────────────────────────────────────────────

class TestLogout:
    async def test_logout_success(
        self, async_client: AsyncClient, registered_user: dict
    ):
        refresh_token = registered_user["tokens"]["refresh_token"]
        resp = await async_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        # Silent 204 whether token existed or not
        assert resp.status_code == 204

    async def test_logout_then_refresh_fails(
        self, async_client: AsyncClient, registered_user: dict
    ):
        """After logout, the refresh token must not work."""
        refresh_token = registered_user["tokens"]["refresh_token"]

        logout = await async_client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )
        assert logout.status_code == 204

        refresh = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refresh.status_code == 401

    async def test_logout_nonexistent_token_is_silent(self, async_client: AsyncClient):
        """Logout with a token that never existed should return 204, not 404."""
        resp = await async_client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.token"},
        )
        assert resp.status_code == 204


# ── Protected route guard ─────────────────────────────────────────────────────

class TestAuthGuard:
    async def test_missing_token_returns_403(self, async_client: AsyncClient):
        resp = await async_client.get("/api/v1/devices/")
        assert resp.status_code in (401, 403)

    async def test_malformed_token_returns_401(self, async_client: AsyncClient):
        resp = await async_client.get(
            "/api/v1/devices/",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
        assert resp.status_code == 401
