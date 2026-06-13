"""
app/schemas/auth.py
────────────────────
Pydantic v2 schemas for the Authentication API surface.

Request models validate inbound data; response models control what is
serialised back to the client (no internal fields like password_hash leak out).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Request Models ────────────────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    """Body for POST /api/v1/auth/register"""

    email: EmailStr = Field(..., examples=["alice@example.com"])
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plain-text password; hashed server-side before storage.",
        examples=["Sup3rSecure!"],
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        examples=["Alice"],
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class UserLoginRequest(BaseModel):
    """Body for POST /api/v1/auth/login"""

    email: EmailStr = Field(..., examples=["alice@example.com"])
    password: str = Field(..., min_length=1, examples=["Sup3rSecure!"])

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class RefreshRequest(BaseModel):
    """Body for POST /api/v1/auth/refresh"""

    refresh_token: str = Field(
        ...,
        description="The opaque refresh token issued at login.",
    )


class DeviceAuthRequest(BaseModel):
    """
    Body for POST /api/v1/auth/device
    Devices exchange their hardware_uid + device_secret for a short-lived
    access token used to authenticate telemetry pushes.
    """

    hardware_uid: str = Field(..., max_length=128)
    device_secret: str = Field(
        ...,
        description="Factory-provisioned shared secret; compared against stored hash.",
    )


# ── Response Models ───────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    """Public user representation — no secrets."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    display_name: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    """
    Issued on successful login or token refresh.

    The refresh_token is opaque (a UUID string) — the JWT is only the
    access_token.  Keeping them separate lets us rotate refresh tokens
    without invalidating access tokens mid-request.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(
        description="Access token lifetime in seconds.",
        examples=[900],
    )


class RegisterResponse(BaseModel):
    """Returned after successful registration."""

    user: UserResponse
    tokens: TokenResponse
