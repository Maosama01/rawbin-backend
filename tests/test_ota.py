"""
tests/test_ota.py
──────────────────
Integration tests for the OTA firmware update endpoint:
  GET /api/v1/devices/{device_id}/ota
"""

from unittest.mock import patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text
from app.db.models.device import Device
import uuid


@pytest.fixture
def mock_boto3_client():
    with patch("boto3.client") as mock_client:
        mock_s3 = mock_client.return_value
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/mock-presigned-url"
        yield mock_s3


@pytest.mark.asyncio
async def test_ota_update_available(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
    mock_boto3_client,
):
    device_id = paired_device["device_id"]

    # Set device firmware version to an old one
    await db_session.execute(
        text(f"UPDATE devices SET firmware_version = '1.0.0' WHERE id = '{device_id}'")
    )
    await db_session.commit()

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/ota",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["update_available"] is True
    assert data["download_url"] == "https://s3.amazonaws.com/mock-presigned-url"
    assert data["current_version"] == "1.0.0"
    assert data["latest_version"] == "2.0.0"

    mock_boto3_client.generate_presigned_url.assert_called_once()


@pytest.mark.asyncio
async def test_ota_no_update_needed(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    db_session: AsyncSession,
    mock_boto3_client,
):
    device_id = paired_device["device_id"]

    # Set device firmware version to the latest one
    await db_session.execute(
        text(f"UPDATE devices SET firmware_version = '2.0.0' WHERE id = '{device_id}'")
    )
    await db_session.commit()

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/ota",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["update_available"] is False
    assert data["download_url"] is None
    assert data["current_version"] == "2.0.0"

    mock_boto3_client.generate_presigned_url.assert_not_called()


@pytest.mark.asyncio
async def test_ota_no_current_version_defaults_to_update(
    async_client: AsyncClient,
    auth_headers: dict,
    paired_device: dict,
    mock_boto3_client,
):
    """If firmware_version is NULL, it should assume update is available."""
    device_id = paired_device["device_id"]

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/ota",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["update_available"] is True
    assert data["download_url"] == "https://s3.amazonaws.com/mock-presigned-url"
    assert data["current_version"] is None


@pytest.mark.asyncio
async def test_ota_unauthorized(async_client: AsyncClient, paired_device: dict):
    device_id = paired_device["device_id"]
    r = await async_client.get(f"/api/v1/devices/{device_id}/ota")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ota_wrong_owner(
    async_client: AsyncClient,
    paired_device: dict,
):
    """A different user should not be able to get an OTA link for someone else's device."""
    device_id = paired_device["device_id"]

    # Register a second user
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "other@rawbin.io", "password": "Other123!", "display_name": "Other"},
    )
    other_token = r.json()["tokens"]["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    r = await async_client.get(
        f"/api/v1/devices/{device_id}/ota",
        headers=other_headers,
    )
    assert r.status_code == 404
