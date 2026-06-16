"""
tests/test_telemetry.py
────────────────────────
Integration tests for the read-only telemetry surface and device snapshot.

Ingestion is MQTT-only now (no HTTP ingest endpoint), so these tests seed
sensor readings directly into the DB via the test session, then exercise:
  GET /api/v1/status/{device_id}
  GET /api/v1/telemetry/{device_id}/history
…plus access control, and confirm the old ingest routes are gone.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sensor_reading import SensorReading


async def _seed_reading(db_session: AsyncSession, device_id: str, **fields) -> None:
    db_session.add(
        SensorReading(
            time=fields.pop("time", datetime.now(timezone.utc)),
            device_id=uuid.UUID(device_id),
            **fields,
        )
    )
    await db_session.flush()


# ── Ingestion endpoints removed ────────────────────────────────────────────────

class TestIngestionRemoved:
    async def test_single_ingest_route_gone(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}",
            json={"temperature_c": 60.0, "time": "2026-06-14T10:00:00Z"},
            headers=paired_device["headers"],
        )
        assert resp.status_code in (404, 405)

    async def test_batch_ingest_route_gone(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.post(
            f"/api/v1/telemetry/{paired_device['device_id']}/batch",
            json={"readings": [{"temperature_c": 60.0, "time": "2026-06-14T10:00:00Z"}]},
            headers=paired_device["headers"],
        )
        assert resp.status_code in (404, 405)


# ── Device snapshot ────────────────────────────────────────────────────────────

class TestDeviceSnapshot:
    async def test_snapshot_after_reading(
        self, async_client: AsyncClient, paired_device: dict, db_session: AsyncSession
    ):
        await _seed_reading(
            db_session,
            paired_device["device_id"],
            temperature_c=72.1,
            humidity_pct=55.3,
            co2_ppm=2100.0,
        )
        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == paired_device["device_id"]
        assert body["is_paired"] is True
        lr = body["latest_reading"]
        assert lr["temperature_c"] == pytest.approx(72.1)
        assert body["reading_age_seconds"] is not None
        assert body["reading_age_seconds"] >= 0

    async def test_snapshot_returns_most_recent_reading(
        self, async_client: AsyncClient, paired_device: dict, db_session: AsyncSession
    ):
        now = datetime.now(timezone.utc)
        await _seed_reading(
            db_session, paired_device["device_id"],
            time=now - timedelta(hours=5), temperature_c=40.0,
        )
        await _seed_reading(
            db_session, paired_device["device_id"],
            time=now - timedelta(minutes=5), temperature_c=75.0,
        )
        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        assert resp.json()["latest_reading"]["temperature_c"] == pytest.approx(75.0)

    async def test_snapshot_no_readings_returns_null_latest(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.get(
            f"/api/v1/status/{paired_device['device_id']}",
            headers=paired_device["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_reading"] is None
        assert body["reading_age_seconds"] is None

    async def test_snapshot_non_member_returns_404(
        self, async_client: AsyncClient, paired_device: dict
    ):
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
        resp = await async_client.get(
            f"/api/v1/status/{uuid.uuid4()}", headers=auth_headers
        )
        assert resp.status_code == 404


# ── History ─────────────────────────────────────────────────────────────────────

class TestTelemetryHistory:
    async def test_raw_history_returns_seeded_rows(
        self, async_client: AsyncClient, paired_device: dict, db_session: AsyncSession
    ):
        now = datetime.now(timezone.utc)
        for i in range(3):
            await _seed_reading(
                db_session, paired_device["device_id"],
                time=now - timedelta(minutes=10 * i), temperature_c=50.0 + i,
            )
        resp = await async_client.get(
            f"/api/v1/telemetry/{paired_device['device_id']}/history?interval=raw",
            headers=paired_device["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["interval"] == "raw"
        assert body["count"] == 3

    async def test_raw_window_over_24h_rejected(
        self, async_client: AsyncClient, paired_device: dict
    ):
        resp = await async_client.get(
            f"/api/v1/telemetry/{paired_device['device_id']}/history",
            params={"interval": "raw", "from": "2024-01-01T00:00:00Z", "to": "2024-01-03T00:00:00Z"},
            headers=paired_device["headers"],
        )
        assert resp.status_code == 400

    async def test_history_non_member_returns_404(
        self, async_client: AsyncClient, paired_device: dict
    ):
        reg_b = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "hist_b@rawbin.io",
                "password": "HistB123!Pass",
                "display_name": "User B Hist",
            },
        )
        token_b = reg_b.json()["tokens"]["access_token"]
        resp = await async_client.get(
            f"/api/v1/telemetry/{paired_device['device_id']}/history?interval=raw",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404
