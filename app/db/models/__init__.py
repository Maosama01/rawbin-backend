"""
app/db/models/__init__.py
─────────────────────────
Re-export all models so that Alembic's env.py can import Base and discover
every table via a single import:

    from app.db.models import *  # noqa: F401, F403
    from app.db.base import Base
"""

from app.db.models.alert_event import AlertEvent
from app.db.models.device import Device
from app.db.models.device_config import DeviceConfig
from app.db.models.refresh_token import RefreshToken
from app.db.models.sensor_reading import SensorReading
from app.db.models.user import User

__all__ = ["User", "RefreshToken", "Device", "SensorReading", "AlertEvent", "DeviceConfig"]
