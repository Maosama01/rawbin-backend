"""
app/api/v1/users.py
────────────────────
User profile management routes.

  GET  /api/v1/users/me              – Return authenticated user's profile
  PATCH /api/v1/users/me             – Update display_name
  PUT  /api/v1/users/me/push-token   – Register or refresh FCM push token
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.user import PushTokenIn, UserOut, UserPatchIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the authenticated user's profile",
)
async def get_me(current_user: CurrentUser) -> UserOut:
    """Return public profile fields for the authenticated user."""
    return UserOut.from_orm_user(current_user)


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Update profile fields",
)
async def patch_me(
    body: UserPatchIn,
    current_user: CurrentUser,
    db: DbSession,
) -> UserOut:
    """
    Partial update of the user's own profile.

    Currently supports: `display_name`.
    Email changes require a verification flow and are handled separately.
    """
    if body.display_name is not None:
        current_user.display_name = body.display_name

    db.add(current_user)
    await db.flush()

    logger.info("User profile updated", extra={"user_id": str(current_user.id)})
    return UserOut.from_orm_user(current_user)


@router.put(
    "/me/push-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Register or refresh the FCM push notification token",
)
async def update_push_token(
    body: PushTokenIn,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """
    Store or update the Firebase Cloud Messaging (FCM) registration token
    for the authenticated user's mobile device.

    The mobile app should call this:
      - On first launch after granting notification permission.
      - Whenever the FCM SDK issues a new token (``onTokenRefresh``).
      - After a fresh login (token may have rotated while logged out).

    This token is used by the Celery worker to send push notifications
    when sensor thresholds are breached.
    """
    current_user.firebase_push_token = body.token
    db.add(current_user)
    await db.flush()

    logger.info(
        "FCM push token updated",
        extra={"user_id": str(current_user.id)},
    )
