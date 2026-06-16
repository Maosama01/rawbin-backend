"""
app/services/sms.py
────────────────────
Pluggable SMS delivery.

Two providers:
  - "stub"   → logs the message and returns success (default; local dev needs
               no Twilio account — read the code from the logs).
  - "twilio" → sends via the Twilio REST API using httpx (no extra dependency).

Selection is driven by settings.SMS_PROVIDER.  The public surface is a single
coroutine, `send_sms(to, body)`, so callers never branch on the provider.
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class SMSDeliveryError(Exception):
    """Raised when an SMS provider fails to accept the message."""


async def _send_stub(to: str, body: str) -> None:
    # NB: do not log the full body in production if it contains a live OTP.
    # Stub mode is dev-only; logging the code here is intentional for testing.
    logger.info("[SMS-STUB] to=%s body=%s", to, body)


async def _send_twilio(to: str, body: str) -> None:
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        raise SMSDeliveryError("Twilio selected but TWILIO_* settings are incomplete.")

    url = _TWILIO_API.format(sid=settings.TWILIO_ACCOUNT_SID)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={"To": to, "From": settings.TWILIO_FROM_NUMBER, "Body": body},
        )
    if resp.status_code >= 400:
        logger.error("Twilio send failed: %s %s", resp.status_code, resp.text)
        raise SMSDeliveryError(f"Twilio returned {resp.status_code}")


async def send_sms(to: str, body: str) -> None:
    """Send an SMS via the configured provider. Raises SMSDeliveryError on failure."""
    if settings.SMS_PROVIDER == "twilio":
        await _send_twilio(to, body)
    else:
        await _send_stub(to, body)
