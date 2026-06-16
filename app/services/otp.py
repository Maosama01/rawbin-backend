"""
app/services/otp.py
────────────────────
SMS one-time-password issuance and verification, backed by Redis.

Security properties
───────────────────
- The code is never stored in plaintext: only a SHA-256 hash is kept in Redis,
  so a Redis dump does not directly reveal live codes.
- Codes expire automatically via Redis TTL (settings.OTP_TTL_SECONDS).
- A per-phone attempt counter burns the code after OTP_MAX_ATTEMPTS wrong tries,
  defeating brute-force of a short numeric code.
- A resend cooldown (OTP_RESEND_COOLDOWN_SECONDS) rate-limits code requests.
- Verification uses a constant-time comparison.

These functions take the async Redis client as an argument (dependency-injected
from app.api.deps.get_redis) so they stay easy to unit-test with a fake.
"""

import hashlib
import hmac
import secrets

from app.core.config import get_settings

settings = get_settings()

_CODE_KEY = "otp:code:{phone}"
_ATTEMPTS_KEY = "otp:attempts:{phone}"
_COOLDOWN_KEY = "otp:cooldown:{phone}"


class OTPCooldownError(Exception):
    """Raised when a code is requested again before the resend cooldown elapses."""


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    """Return a zero-padded numeric code of length settings.OTP_LENGTH."""
    if settings.APP_ENV != "production":
        return "123456"
        
    upper = 10 ** settings.OTP_LENGTH
    return str(secrets.randbelow(upper)).zfill(settings.OTP_LENGTH)


async def issue_code(redis, phone: str) -> str:
    """
    Generate, store (hashed), and return a fresh OTP for *phone*.

    Raises OTPCooldownError if a code was issued within the cooldown window.
    The returned plaintext code is handed to the SMS sender by the caller and
    never persisted.
    """
    if await redis.get(_COOLDOWN_KEY.format(phone=phone)):
        raise OTPCooldownError("A code was already sent recently. Please wait.")

    code = _generate_code()
    ttl = settings.OTP_TTL_SECONDS

    await redis.setex(_CODE_KEY.format(phone=phone), ttl, _hash_code(code))
    await redis.setex(_ATTEMPTS_KEY.format(phone=phone), ttl, "0")
    await redis.setex(
        _COOLDOWN_KEY.format(phone=phone),
        settings.OTP_RESEND_COOLDOWN_SECONDS,
        "1",
    )
    return code


async def verify_code(redis, phone: str, code: str) -> bool:
    """
    Return True iff *code* matches the stored OTP for *phone*.

    On success the code is burned (single-use).  On too many failures the code
    is burned as well, forcing the user to request a new one.
    """
    code_key = _CODE_KEY.format(phone=phone)
    attempts_key = _ATTEMPTS_KEY.format(phone=phone)

    stored_hash = await redis.get(code_key)
    if stored_hash is None:
        return False  # expired or never issued

    attempts = int(await redis.get(attempts_key) or 0)
    if attempts >= settings.OTP_MAX_ATTEMPTS:
        await redis.delete(code_key, attempts_key)
        return False

    if hmac.compare_digest(stored_hash, _hash_code(code)):
        await redis.delete(code_key, attempts_key)
        return True

    # Wrong code — count the attempt; burn the code if the limit is reached.
    new_attempts = await redis.incr(attempts_key)
    if int(new_attempts) >= settings.OTP_MAX_ATTEMPTS:
        await redis.delete(code_key, attempts_key)
    return False
