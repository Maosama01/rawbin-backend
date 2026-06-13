"""
tests/test_alerts.py
──────────────────────
Integration tests for alert history endpoint:
  GET /api/v1/devices/{device_id}/alerts
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert_event import AlertEvent
import uuid
from datetime import datetime, timezone


async def _seed_alerts(db_session: AsyncSession, device_id: str, count: int = 5):
    """Insert synthetic alert events directly into the DB."""
    now = datetime.now(timezone.utc)
    events = []
    metrics = ["temperature_c", "co2_ppm", "humidity_pct", "ph_level"]
    severities = ["WARNING", "CRITICAL", "WARNING", "WARNING"]
    for i in range(count):
        events.append(AlertEvent(
            device_id=uuid.UUID(device_id),
            metric=metrics[i % len(metrics)],
            severity=severities[i % len(severities)],
            value=float(80 + i),
            threshold=float(80),
            message=f"Test alert #{i}",
            reading_time=now,
            notified=False,
        ))
    db_session.add_all(events)
    await db_session.flush()
    return events


# ── List alerts ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_alerts_empty(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
):
    device_id = paired_device["device_id"]
    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_alerts_returns_all(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
):
    device_id = paired_device["device_id"]
    await _seed_alerts(db_session, device_id, count=5)

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5


@pytest.mark.asyncio
async def test_list_alerts_pagination(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
):
    device_id = paired_device["device_id"]
    await _seed_alerts(db_session, device_id, count=10)

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts?limit=4&offset=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 10
    assert len(data["items"]) == 4
    assert data["limit"] == 4
    assert data["offset"] == 2


@pytest.mark.asyncio
async def test_list_alerts_filter_severity(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
):
    device_id = paired_device["device_id"]
    await _seed_alerts(db_session, device_id, count=8)  # seeds: W C W W W C W W

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts?severity=CRITICAL",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    # Every 2nd alert is CRITICAL (index 1, 3, 5, 7 → indices % 4 == 1)
    assert all(item["severity"] == "CRITICAL" for item in data["items"])


@pytest.mark.asyncio
async def test_list_alerts_filter_metric(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
):
    device_id = paired_device["device_id"]
    await _seed_alerts(db_session, device_id, count=8)

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts?metric=temperature_c",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert all(item["metric"] == "temperature_c" for item in data["items"])


@pytest.mark.asyncio
async def test_list_alerts_invalid_severity(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
):
    device_id = paired_device["device_id"]
    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts?severity=UNKNOWN",
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_alerts_wrong_owner(
    async_client: AsyncClient,
    paired_device: dict,
):
    """A different user should not see another user's alerts."""
    device_id = paired_device["device_id"]

    # Register a second user
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "other@rawbin.io", "password": "Other123!", "display_name": "Other"},
    )
    other_token = r.json()["tokens"]["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/alerts",
        headers=other_headers,
    )
    assert r.status_code == 403
