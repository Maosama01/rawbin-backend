"""
app/db/models/device.py
────────────────────────
SQLAlchemy 2.0 ORM model for the `devices` table.

Design notes
────────────
- `hardware_uid` is the immutable identifier burned into the BLE firmware.
  It is stored in plaintext (acts like a username) and indexed for fast look-up.
- `device_secret_hash` is a bcrypt hash of the factory-provisioned shared
  secret used in the HMAC pairing challenge.  The plaintext secret never
  reaches the database.
- `is_paired` distinguishes a device that has been registered in the system
  but not yet completed the cryptographic confirmation handshake.
"""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Immutable hardware identifier (e.g. BLE MAC address or factory serial)
    hardware_uid: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )

    # Fernet-encrypted factory-provisioned shared secret (reversible — needed for HMAC)
    device_secret_enc: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Human-readable name editable by any member in the app
    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="My Rawbin",
    )

    # True once the HMAC pairing confirmation has been completed
    is_paired: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Firmware version reported during last telemetry push (informational)
    firmware_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # Association rows linking users (members) to this device.
    user_links: Mapped[list["UserDevice"]] = relationship(  # noqa: F821
        "UserDevice",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="select",
    )
    # Convenience read-only view of the member User objects.
    members: Mapped[list["User"]] = relationship(  # noqa: F821
        "User",
        secondary="user_devices",
        viewonly=True,
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Device id={self.id} hardware_uid={self.hardware_uid!r} "
            f"paired={self.is_paired}>"
        )
