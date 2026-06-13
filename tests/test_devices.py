"""
tests/test_devices.py
──────────────────────
Integration tests for the device pairing flow:
  POST /api/v1/devices/pair/challenge
  POST /api/v1/devices/pair/confirm
  POST /api/v1/devices/{id}/provision
  GET  /api/v1/devices/

These tests mock Redis in-process so no real Redis TTL expiry is involved.
The mock is configured per-test to simulate nonce presence/absence.
"""

import hashlib
import hmac as _hmac
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


HARDWARE_UID = "RB-TEST-DEVICE-001"
DEVICE_SECRET = "test-secret-for-hmac-verification"


def _compute_hmac(secret: str, nonce: str) -> str:
    return _hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


# ── Challenge ─────────────────────────────────────────────────────────────────

class TestPairingChallenge:
    async def test_challenge_creates_device(
        self, async_client: AsyncClient, auth_headers: dict
    ):
        resp = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": HARDWARE_UID, "display_name": "My Composter"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "device_id" in body
        assert "nonce" in body
        assert len(body["nonce"]) == 64  # 32 bytes = 64 hex chars
        assert "expires_at" in body

    async def test_challenge_unauthenticated(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": HARDWARE_UID, "display_name": "X"},
        )
        assert resp.status_code in (401, 403)

    async def test_challenge_idempotent_for_same_user(
        self, async_client: AsyncClient, auth_headers: dict
    ):
        """Re-challenging for the same hardware_uid / same owner returns 201 again."""
        uid = "RB-IDEMPOTENT-001"
        r1 = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "First"},
            headers=auth_headers,
        )
        r2 = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "Updated Name"},
            headers=auth_headers,
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Same device_id issued for the same hardware_uid
        assert r1.json()["device_id"] == r2.json()["device_id"]

    async def test_challenge_different_user_for_taken_uid_returns_409(
        self,
        async_client: AsyncClient,
        auth_headers: dict,
    ):
        """A hardware UID already registered to user A must reject user B."""
        uid = "RB-CONFLICT-001"

        # Register as user A (fixture user)
        r1 = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "A's Device"},
            headers=auth_headers,
        )
        assert r1.status_code == 201

        # Register as user B
        reg_b = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "userb@rawbin.io",
                "password": "UserBPass123!",
                "display_name": "User B",
            },
        )
        token_b = reg_b.json()["tokens"]["access_token"]

        r2 = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "B's Device"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r2.status_code == 409


# ── Provision + Confirm (HMAC) ────────────────────────────────────────────────

class TestPairingConfirm:
    async def _setup_challenge(
        self, async_client: AsyncClient, auth_headers: dict, uid: str = HARDWARE_UID
    ) -> dict:
        resp = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "Test"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()  # {device_id, nonce, expires_at}

    async def test_confirm_valid_hmac_succeeds(
        self, async_client: AsyncClient, auth_headers: dict, mock_redis: AsyncMock
    ):
        challenge = await self._setup_challenge(async_client, auth_headers)
        device_id = challenge["device_id"]
        nonce = challenge["nonce"]

        # Provision secret
        await async_client.post(
            f"/api/v1/devices/{device_id}/provision",
            params={"secret": DEVICE_SECRET},
            headers=auth_headers,
        )

        mock_redis.get = AsyncMock(return_value=nonce)

        hmac_hex = _compute_hmac(DEVICE_SECRET, nonce)
        resp = await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={"device_id": device_id, "nonce": nonce, "hmac_response": hmac_hex},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == device_id
        assert body["hardware_uid"] == HARDWARE_UID
        assert "paired_at" in body

    async def test_confirm_wrong_hmac_returns_400(
        self, async_client: AsyncClient, auth_headers: dict, mock_redis: AsyncMock
    ):
        challenge = await self._setup_challenge(
            async_client, auth_headers, uid="RB-BAD-HMAC-001"
        )
        device_id = challenge["device_id"]
        nonce = challenge["nonce"]

        await async_client.post(
            f"/api/v1/devices/{device_id}/provision",
            params={"secret": DEVICE_SECRET},
            headers=auth_headers,
        )

        mock_redis.get = AsyncMock(return_value=nonce)

        resp = await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={
                "device_id": device_id,
                "nonce": nonce,
                "hmac_response": "deadbeef" * 8,  # Wrong HMAC
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "HMAC" in resp.json()["detail"]

    async def test_confirm_expired_nonce_returns_410(
        self, async_client: AsyncClient, auth_headers: dict, mock_redis: AsyncMock
    ):
        challenge = await self._setup_challenge(
            async_client, auth_headers, uid="RB-EXPIRED-001"
        )
        device_id = challenge["device_id"]
        nonce = challenge["nonce"]

        await async_client.post(
            f"/api/v1/devices/{device_id}/provision",
            params={"secret": DEVICE_SECRET},
            headers=auth_headers,
        )

        # Nonce absent from Redis (expired)
        mock_redis.get = AsyncMock(return_value=None)

        resp = await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={
                "device_id": device_id,
                "nonce": nonce,
                "hmac_response": _compute_hmac(DEVICE_SECRET, nonce),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 410
        assert "expired" in resp.json()["detail"].lower()

    async def test_confirm_nonce_mismatch_returns_400(
        self, async_client: AsyncClient, auth_headers: dict, mock_redis: AsyncMock
    ):
        challenge = await self._setup_challenge(
            async_client, auth_headers, uid="RB-MISMATCH-001"
        )
        device_id = challenge["device_id"]
        real_nonce = challenge["nonce"]
        stale_nonce = "a" * 64

        await async_client.post(
            f"/api/v1/devices/{device_id}/provision",
            params={"secret": DEVICE_SECRET},
            headers=auth_headers,
        )

        mock_redis.get = AsyncMock(return_value=real_nonce)

        resp = await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={
                "device_id": device_id,
                "nonce": stale_nonce,
                "hmac_response": _compute_hmac(DEVICE_SECRET, stale_nonce),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_confirm_already_paired_returns_409(
        self, async_client: AsyncClient, auth_headers: dict,
        paired_device: dict, mock_redis: AsyncMock
    ):
        """Attempting to re-pair an already-paired device must fail."""
        device_id = paired_device["device_id"]
        mock_redis.get = AsyncMock(return_value="somenonce")

        resp = await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={
                "device_id": device_id,
                "nonce": "somenonce",
                "hmac_response": "a" * 64,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409


# ── List devices ──────────────────────────────────────────────────────────────

class TestListDevices:
    async def test_list_returns_empty_for_new_user(
        self, async_client: AsyncClient
    ):
        reg = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "empty@rawbin.io",
                "password": "EmptyPass123!",
                "display_name": "Empty",
            },
        )
        token = reg.json()["tokens"]["access_token"]
        resp = await async_client.get(
            "/api/v1/devices/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_users_devices(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        resp = await async_client.get("/api/v1/devices/", headers=auth_headers)
        assert resp.status_code == 200
        devices = resp.json()
        assert len(devices) >= 1
        ids = [d["id"] for d in devices]
        assert paired_device["device_id"] in ids

    async def test_list_does_not_return_other_users_devices(
        self,
        async_client: AsyncClient,
        auth_headers: dict,
        paired_device: dict,
    ):
        """User B cannot see User A's devices."""
        reg_b = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "userb2@rawbin.io",
                "password": "UserB2Pass123!",
                "display_name": "User B2",
            },
        )
        token_b = reg_b.json()["tokens"]["access_token"]
        resp = await async_client.get(
            "/api/v1/devices/",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()]
        assert paired_device["device_id"] not in ids
