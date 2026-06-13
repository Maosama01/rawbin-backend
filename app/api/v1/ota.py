"""
app/api/v1/ota.py
─────────────────
OTA Firmware Update API.

  GET /api/v1/devices/{device_id}/ota  – Check for update and get presigned S3 URL
"""

import logging
import uuid
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.db.models.device import Device
from app.schemas.ota import OtaUpdateCheckResponse

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/devices", tags=["OTA"])

# For demonstration, hardcode the latest version and binary name.
# In a full system, this would be looked up in a `firmware_releases` table.
LATEST_FIRMWARE_VERSION = "2.0.0"
FIRMWARE_OBJECT_KEY = f"firmware/rawbin_v{LATEST_FIRMWARE_VERSION}.bin"


def _generate_presigned_url(object_key: str, expiration: int) -> str:
    """
    Generate an S3 presigned URL for downloading the firmware.
    Since this relies on boto3 and botocore, which run synchronously and only perform
    local HMAC-SHA256 signing (no network IO), it is safe to call directly in the async event loop.
    """
    try:
        s3_client = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            # For purely local signing without making a metadata API call:
            config=boto3.session.Config(signature_version="s3v4")
        )
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.OTA_FIRMWARE_BUCKET,
                "Key": object_key,
            },
            ExpiresIn=expiration,
        )
        return url
    except (BotoCoreError, ClientError) as e:
        logger.error("Failed to generate presigned URL", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate secure download link.",
        )


@router.get(
    "/{device_id}/ota",
    response_model=OtaUpdateCheckResponse,
    summary="Check for OTA firmware updates",
)
async def check_ota_update(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> OtaUpdateCheckResponse:
    """
    Check if a firmware update is available for the given device.
    
    If `firmware_version` reported by the device is older than the `LATEST_FIRMWARE_VERSION`,
    this endpoint generates an S3 presigned URL that the mobile app can use to download the
    new binary. The URL expires after 15 minutes.
    """
    # Verify ownership
    result = await db.execute(select(Device).where(Device.id == device_id))
    device: Device | None = result.scalar_one_or_none()

    if device is None or device.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    current_version = device.firmware_version

    # Simple version comparison (assumes semver strings like "1.4.2" vs "2.0.0")
    # For a robust system, use the `packaging.version` module.
    def parse_version(v: str) -> tuple:
        if not v:
            return (0, 0, 0)
        try:
            return tuple(map(int, v.strip("v").split(".")))
        except ValueError:
            return (0, 0, 0)

    is_update_available = parse_version(current_version) < parse_version(LATEST_FIRMWARE_VERSION)

    download_url = None
    if is_update_available:
        download_url = _generate_presigned_url(
            FIRMWARE_OBJECT_KEY, 
            expiration=settings.OTA_PRESIGNED_URL_EXPIRY_SECONDS
        )

    return OtaUpdateCheckResponse(
        device_id=device.id,
        current_version=current_version,
        latest_version=LATEST_FIRMWARE_VERSION,
        update_available=is_update_available,
        download_url=download_url,
    )
