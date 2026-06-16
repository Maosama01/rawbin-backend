"""
app/api/v1/auth.py
───────────────────
Authentication routes:
  POST /api/v1/auth/register      – create new user account
  POST /api/v1/auth/login         – password login → access + refresh tokens
  POST /api/v1/auth/otp/request   – send an SMS one-time code to a phone
  POST /api/v1/auth/otp/verify    – exchange an SMS code for tokens
  POST /api/v1/auth/refresh       – rotate refresh token, issue new access token
  POST /api/v1/auth/logout        – revoke the supplied refresh token

Token issuance and refresh-token persistence live in
app/services/auth_service.py; OTP/SMS logic in app/services/{otp,sms}.py.
Routes stay thin: validate input → delegate → return response.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from app.api.deps import DbSession, RedisDep
from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.schemas.auth import (
    OTPRequestIn,
    OTPVerifyIn,
    RefreshRequest,
    RegisterResponse,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services import auth_service
from app.services.otp import OTPCooldownError, issue_code, verify_code
from app.services.sms import SMSDeliveryError, send_sms

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(body: UserRegisterRequest, db: DbSession) -> RegisterResponse:
    """Create a new user and return tokens so they are authenticated immediately."""
    # Reject duplicate email or phone.
    clauses = [User.email == body.email]
    if body.phone:
        clauses.append(User.phone == body.phone)
    existing = await db.execute(select(User).where(or_(*clauses)))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or phone already exists.",
        )

    user = User(
        email=body.email,
        phone=body.phone,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    await db.flush()  # populate user.id without committing

    tokens = await auth_service.issue_token_pair(db, user.id)
    logger.info("New user registered", extra={"user_id": str(user.id)})

    return RegisterResponse(
        user=UserResponse.model_validate(user),
        tokens=tokens,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
async def login(body: UserLoginRequest, db: DbSession) -> TokenResponse:
    """Return access and refresh tokens for valid credentials."""
    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )

    tokens = await auth_service.issue_token_pair(db, user.id)
    logger.info("User logged in (password)", extra={"user_id": str(user.id)})
    return tokens


@router.post(
    "/otp/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send an SMS login code to a registered phone number",
)
async def request_otp(body: OTPRequestIn, db: DbSession, redis: RedisDep) -> dict:
    """
    Generate and SMS a one-time login code.

    To avoid leaking which phone numbers are registered, this endpoint returns
    202 regardless of whether the number exists. A real code is only sent when
    an active account matches the number.
    """
    result = await db.execute(select(User).where(User.phone == body.phone))
    user: User | None = result.scalar_one_or_none()

    if user is not None and user.is_active:
        try:
            code = await issue_code(redis, body.phone)
        except OTPCooldownError:
            # Silently accept — don't reveal timing of prior requests.
            return {"detail": "If the number is registered, a code has been sent."}
        try:
            await send_sms(body.phone, f"Your Rawbin login code is {code}. It expires in 5 minutes.")
        except SMSDeliveryError:
            logger.error("Failed to deliver OTP SMS", extra={"phone": body.phone})
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not send the SMS code. Please try again.",
            )

    return {"detail": "If the number is registered, a code has been sent."}


@router.post(
    "/otp/verify",
    response_model=TokenResponse,
    summary="Exchange an SMS code for access + refresh tokens",
)
async def verify_otp(body: OTPVerifyIn, db: DbSession, redis: RedisDep) -> TokenResponse:
    """Verify the SMS code and, on success, issue a token pair."""
    if not await verify_code(redis, body.phone, body.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired code.",
        )

    result = await db.execute(select(User).where(User.phone == body.phone))
    user: User | None = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired code.",
        )

    tokens = await auth_service.issue_token_pair(db, user.id)
    logger.info("User logged in (otp)", extra={"user_id": str(user.id)})
    return tokens


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue new access token",
)
async def refresh(body: RefreshRequest, db: DbSession) -> TokenResponse:
    """Validate the refresh token, rotate it, and return a fresh pair."""
    token_hash = auth_service.hash_token(body.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt: RefreshToken | None = result.scalar_one_or_none()

    if rt is None or not rt.is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid or has expired.",
        )

    rt.revoked = True  # rotate: revoke the presented token
    return await auth_service.issue_token_pair(db, rt.user_id)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token (logout)",
)
async def logout(body: RefreshRequest, db: DbSession) -> None:
    """Mark the refresh token as revoked. Silent success if already revoked."""
    token_hash = auth_service.hash_token(body.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt: RefreshToken | None = result.scalar_one_or_none()
    if rt and not rt.revoked:
        rt.revoked = True
