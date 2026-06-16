"""
app/schemas/cycle.py
─────────────────────
Pydantic v2 schemas for compost cycle (batch) endpoints.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

CycleStatus = Literal["active", "curing", "completed"]


class CompostCycleCreate(BaseModel):
    """Body for POST /api/v1/devices/{device_id}/cycles — start a new batch."""

    label: Optional[str] = Field(default=None, max_length=100, examples=["Spring batch"])
    notes: Optional[str] = Field(default=None)
    started_at: Optional[datetime] = Field(
        default=None,
        description="When the batch started. Defaults to now.",
    )


class CompostCycleUpdate(BaseModel):
    """Body for PATCH /api/v1/cycles/{cycle_id} — advance or annotate a batch."""

    status: Optional[CycleStatus] = Field(
        default=None,
        description="Advance the batch lifecycle (active → curing → completed).",
    )
    label: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None)
    ended_at: Optional[datetime] = Field(
        default=None,
        description="Explicit end time. If omitted when moving to 'completed', the server stamps now.",
    )


class CompostCycleOut(BaseModel):
    """A compost cycle as returned by the API."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    device_id: uuid.UUID
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    label: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
