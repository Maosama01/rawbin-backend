"""
app/schemas/alert.py
──────────────────────
Pydantic v2 schemas for alert history endpoints.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class AlertEventOut(BaseModel):
    """A single persisted alert event."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    device_id: uuid.UUID
    metric: str
    severity: str       # "WARNING" | "CRITICAL"
    value: float
    threshold: float
    message: str
    reading_time: datetime
    notified: bool
    created_at: datetime


class AlertListResponse(BaseModel):
    """Paginated alert history response."""
    device_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[AlertEventOut]
