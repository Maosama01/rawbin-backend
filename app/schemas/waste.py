"""
app/schemas/waste.py
─────────────────────
Pydantic v2 schemas for waste log endpoints.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

WasteType = Literal["greens", "browns", "food", "other"]


class WasteLogCreate(BaseModel):
    """Body for POST /api/v1/devices/{device_id}/waste-logs."""

    waste_type: WasteType = Field(..., examples=["greens"])
    weight_kg: Optional[float] = Field(
        default=None, ge=0, le=100, description="Material weight in kg (optional)."
    )
    notes: Optional[str] = Field(default=None)
    logged_at: Optional[datetime] = Field(
        default=None, description="When the material was added. Defaults to now."
    )
    compost_cycle_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Attribute this entry to a specific cycle. Defaults to the device's active cycle, if any.",
    )


class WasteLogOut(BaseModel):
    """A waste log entry as returned by the API."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    device_id: uuid.UUID
    compost_cycle_id: Optional[uuid.UUID] = None
    user_id: Optional[uuid.UUID] = None
    logged_at: datetime
    waste_type: str
    weight_kg: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime


class WasteLogListResponse(BaseModel):
    """Paginated waste-log history."""

    device_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[WasteLogOut]
