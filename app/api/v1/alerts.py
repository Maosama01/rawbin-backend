"""
app/api/v1/alerts.py
──────────────────────
Alert history routes — paginated log of threshold-breach events.

  GET /api/v1/devices/{device_id}/alerts
      ?limit=20&offset=0&severity=WARNING|CRITICAL&metric=temperature_c
"""

import logging
import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.db.models.alert_event import AlertEvent
from app.schemas.alert import AlertEventOut, AlertListResponse
from app.services import device_access

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Alerts"])


@router.get(
    "/devices/{device_id}/alerts",
    response_model=AlertListResponse,
    summary="List alert history for a device",
)
async def list_alerts(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Pagination offset."),
    severity: str | None = Query(
        default=None,
        description="Filter by severity: WARNING or CRITICAL.",
        pattern="^(WARNING|CRITICAL)$",
    ),
    metric: str | None = Query(
        default=None,
        description="Filter by sensor metric name (e.g. temperature_c, co2_ppm).",
    ),
) -> AlertListResponse:
    """
    Return a paginated list of alert events for a device, newest first.

    Supports optional filters:
    - `severity` — `WARNING` or `CRITICAL`
    - `metric`   — exact metric name (e.g. `temperature_c`, `humidity_pct`, `co2_ppm`, `ph_level`)
    """
    await device_access.assert_device_member(db, device_id, current_user.id)

    # ── Build query ───────────────────────────────────────────────────────────
    base_where = [AlertEvent.device_id == device_id]
    if severity:
        base_where.append(AlertEvent.severity == severity)
    if metric:
        base_where.append(AlertEvent.metric == metric)

    # Total count (for pagination metadata)
    count_result = await db.execute(
        select(func.count()).select_from(AlertEvent).where(*base_where)
    )
    total = count_result.scalar_one()

    # Paginated rows
    rows_result = await db.execute(
        select(AlertEvent)
        .where(*base_where)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = rows_result.scalars().all()

    return AlertListResponse(
        device_id=device_id,
        total=total,
        limit=limit,
        offset=offset,
        items=[AlertEventOut.model_validate(item) for item in items],
    )
