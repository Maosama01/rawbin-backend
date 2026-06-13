"""
tests/test_telemetry.py
────────────────────────
Integration tests for telemetry ingestion and device snapshot:
  POST /api/v1/telemetry/{device_id}
  POST /api/v1/telemetry/{device_id}/batch
  GET  /api/v1/status/{device_id}
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


# ── Single reading ingestion ───────────────────────────────────────────────────

class TestIngestSingleReading:
    READING = {
        "temperature_c": 62.4,
        "humidity_pct": 68.2,
        "co2_ppm": 1850.0,
        "ph_level": 6.9,
        "fan_speed_rpm": 1200,
        "firmware_version": "1.4.2",
    }

    async def test_ingest_returns_202(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json=self.READING,
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] == 1
        assert body["device_id"] == paired_device["device_id"]

    async def test_ingest_sparse_reading(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """Only temperature is required; all other fields are optional."""
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={"temperature_c": 55.0},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202

    async def test_ingest_empty_reading_accepted(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """All-null readings (heartbeat) are valid."""
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202

    async def test_ingest_with_explicit_timestamp(
        self, async_client: AsyncClient, paired_device: dict
    ):
        ts = "2024-06-01T10:00:00Z"
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={"temperature_c": 60.0, "time": ts},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202

    async def test_ingest_out_of_range_temperature_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """Temperature bounds: -40 to 150 °C."""
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={"temperature_c": 999.0},  # Way too hot
            headers=paired_device["headers"],
        )
        assert resp.status_code == 422

    async def test_ingest_out_of_range_ph_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """pH must be 0–14."""
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={"ph_level": 15.0},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 422

    async def test_ingest_unauthenticated_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json=self.READING,
        )
        assert resp.status_code in (401, 403)

    async def test_ingest_wrong_device_owner_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """User B cannot push telemetry for User A's device."""
        reg_b = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "telemetry_b@rawbin.io",
                "password": "TelB123!Pass",
                "display_name": "User B Telemetry",
            },
        )
        token_b = reg_b.json()["tokens"]["access_token"]

        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json=self.READING,
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403

    async def test_ingest_nonexistent_device_returns_404(
        self, async_client: AsyncClient, auth_headers: dict
    ):
        import uuid
        fake_id = str(uuid.uuid4())
        resp = await async_client.post(
            f"/api/v1/telemetry/{fake_id}",
            json={"temperature_c": 60.0},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_ingest_unpaired_device_rejected(
        self, async_client: AsyncClient, auth_headers: dict
    ):
        """A device that hasn't completed pairing must not accept telemetry."""
        challenge_resp = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": "RB-UNPAIRED-TEL-001", "display_name": "Unpaired"},
            headers=auth_headers,
        )
        device_id = challenge_resp.json()["device_id"]

        resp = await async_client.post(
            f"/api/v1/telemetry/{device_id}",
            json={"temperature_c": 60.0},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ── Batch ingestion ────────────────────────────────────────────────────────────

class TestIngestBatch:
    async def test_batch_multiple_readings(
        self, async_client: AsyncClient, paired_device: dict
    ):
        readings = [
            {"temperature_c": 55.0 + i, "co2_ppm": 1000.0 + i * 100}
            for i in range(10)
        ]
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": readings},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 10

    async def test_batch_single_reading(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": [{"temperature_c": 61.0}]},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1

    async def test_batch_empty_list_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """Batch must have at least 1 reading."""
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": []},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 422

    async def test_batch_exceeds_limit_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """Max 500 readings per batch."""
        readings = [{"temperature_c": 60.0}] * 501
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": readings},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 422

    async def test_batch_updates_firmware_version(
        self, async_client: AsyncClient, paired_device: dict
    ):
        readings = [
            {"temperature_c": 60.0},
            {"temperature_c": 61.0, "firmware_version": "2.0.0"},
        ]
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": readings},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 202

        # Verify firmware version updated via snapshot
        snapshot = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        assert snapshot.json()["firmware_version"] == "2.0.0"


# ── Device snapshot ────────────────────────────────────────────────────────────

class TestDeviceSnapshot:
    async def test_snapshot_after_reading(
        self, async_client: AsyncClient, paired_device: dict
    ):
        # Ingest a reading
        reading = {
            "temperature_c": 72.1,
            "humidity_pct": 55.3,
            "co2_ppm": 2100.0,
            "ph_level": 7.1,
            "fan_speed_rpm": 900,
            "firmware_version": "1.5.0",
        }
        await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json=reading,
            headers=paired_device["headers"],
        )

        # Check snapshot
        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == paired_device["device_id"]
        assert body["hardware_uid"] == paired_device["hardware_uid"]
        assert body["is_paired"] is True
        assert body["firmware_version"] == "1.5.0"
        lr = body["latest_reading"]
        assert lr["temperature_c"] == pytest.approx(72.1)
        assert lr["humidity_pct"] == pytest.approx(55.3)
        assert lr["co2_ppm"] == pytest.approx(2100.0)
        assert body["reading_age_seconds"] is not None
        assert body["reading_age_seconds"] >= 0

    async def test_snapshot_no_readings_returns_null_latest(
        self, async_client: AsyncClient, auth_headers: dict, mock_redis: AsyncMock
    ):
        """A paired device with no telemetry should return latest_reading=null."""
        uid = "RB-SNAPSHOT-EMPTY-001"
        challenge_resp = await async_client.post(
            "/api/v1/devices/pair/challenge",
            json={"hardware_uid": uid, "display_name": "Empty"},
            headers=auth_headers,
        )
        device_id = challenge_resp.json()["device_id"]
        nonce = challenge_resp.json()["nonce"]

        await async_client.post(
            f"/api/v1/devices/{device_id}/provision",
            params={"secret": "test-secret"},
            headers=auth_headers,
        )

        import hmac as _hmac, hashlib
        hmac_hex = _hmac.new(b"test-secret", nonce.encode(), hashlib.sha256).hexdigest()

        mock_redis.get = AsyncMock(return_value=nonce)

        await async_client.post(
            "/api/v1/devices/pair/confirm",
            json={"device_id": device_id, "nonce": nonce, "hmac_response": hmac_hex},
            headers=auth_headers,
        )

        resp = await async_client.get(
            f"/api/v1/status/{device_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_reading"] is None
        assert body["reading_age_seconds"] is None

    async def test_snapshot_returns_most_recent_reading(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """With multiple readings, only the latest is returned."""
        base_time = "2024-01-01T00:00:00Z"
        later_time = "2024-06-01T12:00:00Z"

        for ts, temp in [(base_time, 40.0), (later_time, 75.0)]:
            await async_client.post(
                f"/api/v1/telemetry/{paired_device['device_id']}",
                json={"temperature_c": temp, "time": ts},
                headers=paired_device["headers"],
            )

        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        # Most recent reading has temperature 75.0
        assert resp.json()["latest_reading"]["temperature_c"] == pytest.approx(75.0)

    async def test_snapshot_unauthorized_access_returns_404(
        self, async_client: AsyncClient, paired_device: dict
    ):
        """User B cannot see User A's device snapshot."""
        reg_b = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "snapshot_b@rawbin.io",
                "password": "SnapB123!Pass",
                "display_name": "User B Snap",
            },
        )
        token_b = reg_b.json()["tokens"]["access_token"]

        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

    async def test_snapshot_nonexistent_device_returns_404(
        self, async_client: AsyncClient, auth_headers: dict
    ):
        import uuid
        resp = await async_client.get(
            f"/api/v1/status/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404
