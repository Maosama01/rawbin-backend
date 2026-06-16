"""
app/db/models/user_device.py
─────────────────────────────
Association table linking users to the devices they can access.

Sharing model: **all members are equal.**  There is no owner/viewer role —
every user linked to a device has full access (read telemetry, edit config,
add/remove other members, unpair).  A device may have many members; a user
may have many devices.

The composite primary key (user_id, device_id) enforces UNIQUE(user_id,
device_id) so the same user cannot be linked to the same device twice.
"""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class UserDevice(Base, TimestampMixin):
    __tablename__ = "user_devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,  # fast "who are this device's members?" lookups
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="device_links", lazy="select"
    )
    device: Mapped["Device"] = relationship(  # noqa: F821
        "Device", back_populates="user_links", lazy="select"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserDevice user_id={self.user_id} device_id={self.device_id}>"
