"""
app/core/security.py
─────────────────────
Password hashing (bcrypt), JWT access/refresh token utilities, and
device-secret encryption (Fernet) + HMAC verification helpers.

Design note — Why Fernet instead of bcrypt for device secrets?
──────────────────────────────────────────────────────────────
The HMAC pairing challenge requires the server to compute
  HMAC-SHA256(key=device_secret, msg=nonce)
and compare it to the value produced by the device firmware.

bcrypt is a one-way hash — you cannot recover the plaintext to use it
as an HMAC key.  Therefore, device secrets are stored symmetrically
encrypted with Fernet (AES-128-CBC + HMAC-SHA256 envelope) using a
server-side key (DEVICE_SECRET_KEY in settings).

In production, DEVICE_SECRET_KEY should be stored in AWS Secrets Manager
and rotated via envelope encryption with KMS.
"""

import hashlib
import hmac as _hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# ── Password hashing ─────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored *hashed* password."""
    return pwd_context.verify(plain, hashed)


# ── JWT helpers ──────────────────────────────────────────────────────────────
def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    payload["iat"] = datetime.now(timezone.utc)
    payload["jti"] = str(uuid.uuid4())  # Unique ID — prevents duplicate hashes
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str) -> str:
    """Create a short-lived access token for the given user ID / subject."""
    return _create_token(
        {"sub": subject, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(subject: str) -> str:
    """Create a long-lived refresh token stored in the DB."""
    return _create_token(
        {"sub": subject, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT.

    Raises:
        jose.JWTError: if the token is invalid or expired.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# ── Device secret — Fernet symmetric encryption ──────────────────────────────

def _get_fernet() -> Fernet:
    """
    Return a Fernet instance using DEVICE_SECRET_KEY from settings.

    Falls back to auto-generating a dev key if the setting is empty.
    This ensures the local dev stack works out of the box, while
    production deployments must explicitly set the key.
    """
    key = settings.DEVICE_SECRET_KEY
    if not key:
        # Deterministic dev-only key derived from SECRET_KEY — never use in prod.
        import base64
        raw = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode())


def encrypt_device_secret(plaintext: str) -> str:
    """
    Symmetrically encrypt *plaintext* device secret.
    Returns a URL-safe base64 Fernet token (str).
    """
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_device_secret(token: str) -> str:
    """
    Decrypt a Fernet-encrypted device secret token.

    Raises:
        cryptography.fernet.InvalidToken: if decryption fails.
    """
    return _get_fernet().decrypt(token.encode()).decode()


# ── HMAC verification ─────────────────────────────────────────────────────────

def compute_device_hmac(device_secret_plain: str, nonce: str) -> str:
    """
    Compute HMAC-SHA256(key=device_secret, msg=nonce).
    Returns the lowercase hex digest — this is what device firmware must send.
    """
    return _hmac.new(
        key=device_secret_plain.encode(),
        msg=nonce.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_device_hmac(device_secret_enc: str, nonce: str, hmac_response: str) -> bool:
    """
    Decrypt the stored device secret and verify the HMAC response
    using a constant-time comparison to prevent timing attacks.

    Args:
        device_secret_enc:  Fernet-encrypted device secret from DB.
        nonce:              The challenge nonce issued to the device.
        hmac_response:      Hex HMAC-SHA256 sent back by the device firmware.

    Returns:
        True if the HMAC matches; False otherwise.
    """
    try:
        secret_plain = decrypt_device_secret(device_secret_enc)
    except InvalidToken:
        return False

    expected = compute_device_hmac(secret_plain, nonce)
    return _hmac.compare_digest(expected.lower(), hmac_response.lower())
