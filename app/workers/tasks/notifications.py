"""
app/workers/tasks/notifications.py
────────────────────────────────────
Celery task for Firebase Cloud Messaging (FCM) push notifications.

Setup (production):
  1. Download the Firebase service account JSON from the Firebase Console:
     Project Settings → Service Accounts → Generate new private key
  2. Set FIREBASE_CREDENTIALS_JSON in your environment (the raw JSON string),
     OR set FIREBASE_CREDENTIALS_PATH to the path of the JSON file.
  3. Add firebase-admin to requirements.txt (already included).

The task initialises the firebase-admin SDK once per worker process (module-level
singleton) and re-uses the connection pool for subsequent calls.
"""

import logging
import os
from typing import Any

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Firebase app singleton (one initialisation per worker process) ─────────────

_firebase_app = None


def _get_firebase_app():
    """
    Return (and lazily initialise) the firebase-admin App instance.

    Credential resolution order:
      1. FIREBASE_CREDENTIALS_JSON env var  (raw JSON string — good for ECS secrets)
      2. FIREBASE_CREDENTIALS_PATH env var  (path to JSON file — good for local dev)
      3. Application Default Credentials    (works on GCP / Cloud Run / local gcloud)

    Returns None if no credentials are available (stub mode).
    """
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials

        # Already initialised by another import path?
        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
            return _firebase_app

        cred = None

        # Option 1: raw JSON in env var (preferred for containerised deployments)
        raw_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        if raw_json:
            import json
            cred = credentials.Certificate(json.loads(raw_json))
            logger.info("Firebase: using credentials from FIREBASE_CREDENTIALS_JSON")

        # Option 2: file path
        if cred is None:
            cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH")
            if cred_path and os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
                logger.info(
                    "Firebase: using credentials from file",
                    extra={"path": cred_path},
                )

        # Option 3: Application Default Credentials (ADC)
        if cred is None:
            cred = credentials.ApplicationDefault()
            logger.info("Firebase: using Application Default Credentials")

        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialised")
        return _firebase_app

    except ImportError:
        logger.warning(
            "firebase-admin not installed — push notifications disabled. "
            "Run: pip install firebase-admin"
        )
        return None
    except Exception:
        logger.error("Firebase Admin SDK initialisation failed", exc_info=True)
        return None


# ── Task ──────────────────────────────────────────────────────────────────────

@celery_app.task(
    queue="default",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="notifications.send_push",
    acks_late=True,   # Acknowledge only after successful completion → safer retries
)
def send_push_notification(
    self,
    user_id: str,
    fcm_token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Send a Firebase Cloud Messaging push notification.

    Args:
        user_id:   UUID of the recipient user (for logging / audit).
        fcm_token: FCM registration token stored on the User row.
        title:     Notification title (shown in the OS tray).
        body:      Notification body text.
        data:      Optional key-value payload (all values must be strings).
                   Available to the app even when tapped from the background.

    Returns:
        {"status": "sent", "message_id": "<fcm_id>"} on success.
        {"status": "stub", "message_id": ""}          when firebase-admin
                                                       is not configured.

    Raises:
        Retries automatically (up to max_retries) on transient FCM errors.
        Permanent errors (invalid token, app not found) are NOT retried.
    """
    firebase_app = _get_firebase_app()

    if firebase_app is None:
        # Graceful degradation — log and return without crashing the worker
        logger.info(
            "Push notification skipped (Firebase not configured)",
            extra={"user_id": user_id, "title": title},
        )
        return {"status": "stub", "message_id": ""}

    try:
        from firebase_admin import messaging

        # Ensure all data values are strings (FCM requirement)
        safe_data = {k: str(v) for k, v in (data or {}).items()}
        safe_data["user_id"] = user_id  # always include for deep-linking

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=safe_data,
            token=fcm_token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)
                )
            ),
        )

        message_id = messaging.send(message, app=firebase_app)
        logger.info(
            "Push notification sent",
            extra={"user_id": user_id, "message_id": message_id},
        )
        return {"status": "sent", "message_id": message_id}

    except Exception as exc:
        exc_name = type(exc).__name__

        # Permanent failures — don't retry (invalid token, deregistered device, etc.)
        permanent_errors = (
            "UnregisteredError",
            "InvalidArgumentError",
            "SenderIdMismatchError",
        )
        if any(err in exc_name for err in permanent_errors):
            logger.warning(
                "Push notification permanently failed — not retrying",
                extra={"user_id": user_id, "error": exc_name},
            )
            return {"status": "failed", "message_id": ""}

        # Transient failures — retry with exponential backoff
        logger.warning(
            "Push notification failed — retrying",
            extra={"user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        raise self.retry(exc=exc)
