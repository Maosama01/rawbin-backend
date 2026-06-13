"""
app/schemas/ota.py
───────────────────
Pydantic schemas for the OTA firmware update flow.
"""

import uuid
from typing import Optional
from pydantic import BaseModel, Field

class OtaUpdateCheckResponse(BaseModel):
    """
    Response for the OTA update check endpoint.
    If `update_available` is True, `download_url` will contain an S3 presigned URL
    valid for downloading the firmware binary.
    """
    device_id: uuid.UUID
    current_version: Optional[str] = Field(description="The device's currently reported firmware version.")
    latest_version: str = Field(description="The latest available firmware version.")
    update_available: bool = Field(description="True if an update is required.")
    download_url: Optional[str] = Field(default=None, description="Presigned S3 URL valid for 15 minutes.")
