"""
app/schemas/auth.py
────────────────────
Pydantic v2 schemas for the Authentication API surface.

Request models validate inbound data; response models control what is
serialised back to the client (no internal fields like password_hash leak out).
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

# E.164 phone number: a leading '+' then 1–15 digits.
_E164_PATTERN = r"^\+[1-9]\d{1,14}$"


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
    phone: Optional[str] = Field(
        default=None,
        pattern=_E164_PATTERN,
        description="E.164 phone number for SMS-OTP login (optional).",
        examples=["+14155552671"],
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class OTPRequestIn(BaseModel):
    """Body for POST /api/v1/auth/otp/request — ask for a login code by SMS."""

    phone: str = Field(
        ...,
        pattern=_E164_PATTERN,
        description="E.164 phone number registered on the account.",
        examples=["+14155552671"],
    )


class OTPVerifyIn(BaseModel):
    """Body for POST /api/v1/auth/otp/verify — exchange a code for tokens."""

    phone: str = Field(..., pattern=_E164_PATTERN, examples=["+14155552671"])
    code: str = Field(
        ...,
        min_length=4,
        max_length=10,
        description="The numeric code delivered by SMS.",
        examples=["123456"],
    )


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


# ── Response Models ───────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    """Public user representation — no secrets."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    phone: Optional[str] = None
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
