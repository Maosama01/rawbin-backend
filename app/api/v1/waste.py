"""
app/api/v1/waste.py
────────────────────
Waste log routes — material the user adds to the bin.

  POST /api/v1/devices/{device_id}/waste-logs  – log added material
  GET  /api/v1/devices/{device_id}/waste-logs  – paginated history

Access requires device membership. When no cycle is specified, the entry is
attributed to the device's currently-active cycle (if any).
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.db.models.compost_cycle import CompostCycle
from app.db.models.waste_log import WasteLog
from app.schemas.waste import WasteLogCreate, WasteLogListResponse, WasteLogOut
from app.services import device_access

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Waste Logs"])


@router.post(
    "/devices/{device_id}/waste-logs",
    response_model=WasteLogOut,
    status_code=status.HTTP_201_CREATED,
    summary="Log material added to the bin",
)
async def create_waste_log(
    device_id: uuid.UUID,
    body: WasteLogCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> WasteLog:
    await device_access.assert_device_member(db, device_id, current_user.id)

    cycle_id = body.compost_cycle_id
    if cycle_id is not None:
        # Validate the cycle belongs to this device.
        cycle = (
            await db.execute(
                select(CompostCycle).where(
                    CompostCycle.id == cycle_id,
                    CompostCycle.device_id == device_id,
                )
            )
        ).scalar_one_or_none()
        if cycle is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="compost_cycle_id does not belong to this device.",
            )
    else:
        # Default to the active cycle, if one exists.
        active = (
            await db.execute(
                select(CompostCycle.id).where(
                    CompostCycle.device_id == device_id,
                    CompostCycle.status == "active",
                )
            )
        ).scalar_one_or_none()
        cycle_id = active

    entry = WasteLog(
        device_id=device_id,
        compost_cycle_id=cycle_id,
        user_id=current_user.id,
        logged_at=body.logged_at or datetime.now(timezone.utc),
        waste_type=body.waste_type,
        weight_kg=body.weight_kg,
        notes=body.notes,
    )
    db.add(entry)
    await db.flush()
    logger.info("Waste logged", extra={"device_id": str(device_id), "waste_id": str(entry.id)})
    return entry


@router.get(
    "/devices/{device_id}/waste-logs",
    response_model=WasteLogListResponse,
    summary="List waste log history for a device (newest first)",
)
async def list_waste_logs(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cycle_id: uuid.UUID | None = Query(
        default=None, description="Filter to a single compost cycle."
    ),
) -> WasteLogListResponse:
    await device_access.assert_device_member(db, device_id, current_user.id)

    clauses = [WasteLog.device_id == device_id]
    if cycle_id is not None:
        clauses.append(WasteLog.compost_cycle_id == cycle_id)

    total = (
        await db.execute(select(func.count()).select_from(WasteLog).where(*clauses))
    ).scalar_one()

    rows = (
        await db.execute(
            select(WasteLog)
            .where(*clauses)
            .order_by(WasteLog.logged_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return WasteLogListResponse(
        device_id=device_id,
        total=total,
        limit=limit,
        offset=offset,
        items=[WasteLogOut.model_validate(r) for r in rows],
    )
