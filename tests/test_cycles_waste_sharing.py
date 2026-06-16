"""
tests/test_cycles_waste_sharing.py
───────────────────────────────────
Integration tests for compost cycles, waste logs, and device sharing.
"""

import uuid

import pytest
from httpx import AsyncClient


async def _register(async_client: AsyncClient, email: str) -> dict:
    resp = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SharePass123!", "display_name": email.split("@")[0]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Sharing (equal access) ──────────────────────────────────────────────────────

class TestSharing:
    async def test_share_grants_access(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        device_id = paired_device["device_id"]
        reg_b = await _register(async_client, "share_b@rawbin.io")
        token_b = reg_b["tokens"]["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Before sharing, B cannot see the device.
        assert (await async_client.get("/api/v1/devices/", headers=headers_b)).json() == []

        share = await async_client.post(
            f"/api/v1/devices/{device_id}/share",
            json={"email": "share_b@rawbin.io"},
            headers=auth_headers,
        )
        assert share.status_code == 200
        emails = {m["email"] for m in share.json()}
        assert "share_b@rawbin.io" in emails

        # After sharing, B sees it and can read its status.
        listed = await async_client.get("/api/v1/devices/", headers=headers_b)
        assert device_id in [d["id"] for d in listed.json()]
        snap = await async_client.get(f"/api/v1/status/{device_id}", headers=headers_b)
        assert snap.status_code == 200

    async def test_share_unknown_email_404(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/devices/{paired_device['device_id']}/share",
            json={"email": "ghost@rawbin.io"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_cannot_remove_last_member(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict,
        registered_user: dict,
    ):
        device_id = paired_device["device_id"]
        owner_id = registered_user["user"]["id"]
        resp = await async_client.delete(
            f"/api/v1/devices/{device_id}/members/{owner_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 409


# ── Compost cycles ──────────────────────────────────────────────────────────────

class TestCycles:
    async def test_create_and_one_active_invariant(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        device_id = paired_device["device_id"]
        r1 = await async_client.post(
            f"/api/v1/devices/{device_id}/cycles",
            json={"label": "Batch 1"},
            headers=auth_headers,
        )
        assert r1.status_code == 201
        assert r1.json()["status"] == "active"

        # Second active cycle rejected.
        r2 = await async_client.post(
            f"/api/v1/devices/{device_id}/cycles",
            json={"label": "Batch 2"},
            headers=auth_headers,
        )
        assert r2.status_code == 409

    async def test_complete_then_start_new(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        device_id = paired_device["device_id"]
        created = await async_client.post(
            f"/api/v1/devices/{device_id}/cycles", json={}, headers=auth_headers
        )
        cycle_id = created.json()["id"]

        done = await async_client.patch(
            f"/api/v1/cycles/{cycle_id}",
            json={"status": "completed"},
            headers=auth_headers,
        )
        assert done.status_code == 200
        assert done.json()["status"] == "completed"
        assert done.json()["ended_at"] is not None

        # A new active cycle is now allowed.
        again = await async_client.post(
            f"/api/v1/devices/{device_id}/cycles", json={}, headers=auth_headers
        )
        assert again.status_code == 201

    async def test_cycles_non_member_404(
        self, async_client: AsyncClient, paired_device: dict
    ):
        reg_b = await _register(async_client, "cycle_b@rawbin.io")
        headers_b = {"Authorization": f"Bearer {reg_b['tokens']['access_token']}"}
        resp = await async_client.post(
            f"/api/v1/devices/{paired_device['device_id']}/cycles",
            json={}, headers=headers_b,
        )
        assert resp.status_code == 404


# ── Waste logs ──────────────────────────────────────────────────────────────────

class TestWasteLogs:
    async def test_log_attributes_to_active_cycle(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        device_id = paired_device["device_id"]
        cycle = await async_client.post(
            f"/api/v1/devices/{device_id}/cycles", json={}, headers=auth_headers
        )
        cycle_id = cycle.json()["id"]

        log = await async_client.post(
            f"/api/v1/devices/{device_id}/waste-logs",
            json={"waste_type": "greens", "weight_kg": 1.5},
            headers=auth_headers,
        )
        assert log.status_code == 201
        body = log.json()
        assert body["waste_type"] == "greens"
        assert body["compost_cycle_id"] == cycle_id

        listed = await async_client.get(
            f"/api/v1/devices/{device_id}/waste-logs", headers=auth_headers
        )
        assert listed.json()["total"] == 1

    async def test_log_without_cycle_ok(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        """Material can be logged before any cycle exists (cycle id null)."""
        log = await async_client.post(
            f"/api/v1/devices/{paired_device['device_id']}/waste-logs",
            json={"waste_type": "food"},
            headers=auth_headers,
        )
        assert log.status_code == 201
        assert log.json()["compost_cycle_id"] is None

    async def test_log_bad_cycle_id_400(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/devices/{paired_device['device_id']}/waste-logs",
            json={"waste_type": "browns", "compost_cycle_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_invalid_waste_type_422(
        self, async_client: AsyncClient, auth_headers: dict, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/devices/{paired_device['device_id']}/waste-logs",
            json={"waste_type": "plastic"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
