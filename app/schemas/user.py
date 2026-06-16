"""
app/schemas/user.py
────────────────────
Pydantic v2 schemas for user profile endpoints.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

_E164_PATTERN = r"^\+[1-9]\d{1,14}$"


class UserOut(BaseModel):
    """Public user profile — never expose password_hash or internal flags."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    phone: str | None = None
    display_name: str
    is_active: bool
    has_push_token: bool = Field(
        description="True if an FCM token is registered on this account."
    )
    created_at: datetime

    @classmethod
    def from_orm_user(cls, user) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            phone=user.phone,
            display_name=user.display_name,
            is_active=user.is_active,
            has_push_token=bool(user.firebase_push_token),
            created_at=user.created_at,
        )


class UserPatchIn(BaseModel):
    """Partial update — only fields the mobile app can change."""
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="New display name (1–100 characters).",
    )
    phone: str | None = Field(
        default=None,
        pattern=_E164_PATTERN,
        description="E.164 phone number used for SMS-OTP login.",
    )


class PushTokenIn(BaseModel):
    """Register or refresh the FCM push token for this user's device."""
    token: str = Field(
        min_length=10,
        description="Firebase Cloud Messaging registration token from the mobile SDK.",
    )
