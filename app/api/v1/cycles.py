"""
app/api/v1/cycles.py
─────────────────────
Compost cycle (batch) routes.

  POST  /api/v1/devices/{device_id}/cycles  – start a new batch
  GET   /api/v1/devices/{device_id}/cycles  – list a device's batches
  GET   /api/v1/cycles/{cycle_id}           – fetch one batch
  PATCH /api/v1/cycles/{cycle_id}           – advance / annotate a batch

Access requires device membership. At most one 'active' cycle per device is
enforced both here (friendly 409) and by a DB partial-unique index (safety net).
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.db.models.compost_cycle import CompostCycle
from app.schemas.cycle import CompostCycleCreate, CompostCycleOut, CompostCycleUpdate
from app.services import device_access

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Compost Cycles"])


async def _load_cycle_with_access(
    cycle_id: uuid.UUID, db: DbSession, user_id: uuid.UUID
) -> CompostCycle:
    cycle = (
        await db.execute(select(CompostCycle).where(CompostCycle.id == cycle_id))
    ).scalar_one_or_none()
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found.")
    # Reuse device membership check (raises 404 for non-members).
    await device_access.assert_device_member(db, cycle.device_id, user_id)
    return cycle


@router.post(
    "/devices/{device_id}/cycles",
    response_model=CompostCycleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new compost cycle for a device",
)
async def create_cycle(
    device_id: uuid.UUID,
    body: CompostCycleCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> CompostCycle:
    await device_access.assert_device_member(db, device_id, current_user.id)

    existing_active = (
        await db.execute(
            select(CompostCycle).where(
                CompostCycle.device_id == device_id,
                CompostCycle.status == "active",
            )
        )
    ).scalar_one_or_none()
    if existing_active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device already has an active cycle. Complete it before starting a new one.",
        )

    cycle = CompostCycle(
        device_id=device_id,
        status="active",
        started_at=body.started_at or datetime.now(timezone.utc),
        label=body.label,
        notes=body.notes,
    )
    db.add(cycle)
    await db.flush()
    logger.info("Compost cycle started", extra={"device_id": str(device_id), "cycle_id": str(cycle.id)})
    return cycle


@router.get(
    "/devices/{device_id}/cycles",
    response_model=list[CompostCycleOut],
    summary="List a device's compost cycles (newest first)",
)
async def list_cycles(
    device_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(active|curing|completed)$",
        description="Optional filter by lifecycle status.",
    ),
) -> list[CompostCycle]:
    await device_access.assert_device_member(db, device_id, current_user.id)

    clauses = [CompostCycle.device_id == device_id]
    if status_filter:
        clauses.append(CompostCycle.status == status_filter)

    result = await db.execute(
        select(CompostCycle).where(*clauses).order_by(CompostCycle.started_at.desc())
    )
    return list(result.scalars().all())


@router.get(
    "/cycles/{cycle_id}",
    response_model=CompostCycleOut,
    summary="Fetch a single compost cycle",
)
async def get_cycle(
    cycle_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> CompostCycle:
    return await _load_cycle_with_access(cycle_id, db, current_user.id)


@router.patch(
    "/cycles/{cycle_id}",
    response_model=CompostCycleOut,
    summary="Advance or annotate a compost cycle",
)
async def update_cycle(
    cycle_id: uuid.UUID,
    body: CompostCycleUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> CompostCycle:
    cycle = await _load_cycle_with_access(cycle_id, db, current_user.id)

    if body.label is not None:
        cycle.label = body.label
    if body.notes is not None:
        cycle.notes = body.notes
    if body.ended_at is not None:
        cycle.ended_at = body.ended_at

    if body.status is not None and body.status != cycle.status:
        cycle.status = body.status
        # Stamp an end time when the batch is finished, unless one was supplied.
        if body.status == "completed" and cycle.ended_at is None:
            cycle.ended_at = body.ended_at or datetime.now(timezone.utc)

    await db.flush()
    logger.info("Compost cycle updated", extra={"cycle_id": str(cycle_id), "status": cycle.status})
    return cycle
