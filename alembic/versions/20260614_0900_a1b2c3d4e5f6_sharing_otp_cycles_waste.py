"""sharing model, sms-otp phone, compost cycles, waste logs, 7-day chunks

Revision ID: a1b2c3d4e5f6
Revises: 958308305136
Create Date: 2026-06-14 09:00:00.000000

Changes
───────
- users.phone column (unique) for SMS-OTP login.
- user_devices association table (equal-access sharing). Existing
  devices.owner_id values are migrated into it, then owner_id is dropped.
- compost_cycles table (+ partial unique index: one active cycle per device).
- waste_logs table.
- sensor_readings hypertable chunk interval widened to 7 days (suited to the
  ~10-minute reading cadence; future chunks only).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "958308305136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users.phone ───────────────────────────────────────────────────────────
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)

    # ── user_devices (equal-access sharing) ──────────────────────────────────
    op.create_table(
        "user_devices",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "device_id"),
    )
    op.create_index(op.f("ix_user_devices_device_id"), "user_devices", ["device_id"], unique=False)

    # Migrate existing single-owner links into the new association table.
    op.execute(
        """
        INSERT INTO user_devices (user_id, device_id, created_at, updated_at)
        SELECT owner_id, id, now(), now() FROM devices
        ON CONFLICT DO NOTHING
        """
    )

    # Drop the old single-owner FK column.
    op.drop_index("ix_devices_owner_id", table_name="devices")
    op.drop_column("devices", "owner_id")

    # ── compost_cycles ───────────────────────────────────────────────────────
    op.create_table(
        "compost_cycles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_compost_cycles_device_id"), "compost_cycles", ["device_id"], unique=False)
    # At most one active cycle per device.
    op.create_index(
        "uq_compost_cycles_one_active_per_device",
        "compost_cycles",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── waste_logs ───────────────────────────────────────────────────────────
    op.create_table(
        "waste_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("compost_cycle_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("waste_type", sa.String(length=16), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["compost_cycle_id"], ["compost_cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_waste_logs_device_id"), "waste_logs", ["device_id"], unique=False)
    op.create_index(op.f("ix_waste_logs_compost_cycle_id"), "waste_logs", ["compost_cycle_id"], unique=False)
    op.create_index(op.f("ix_waste_logs_logged_at"), "waste_logs", ["logged_at"], unique=False)

    # ── Widen hypertable chunk interval to 7 days (future chunks) ─────────────
    op.execute(
        "SELECT set_chunk_time_interval('sensor_readings', INTERVAL '7 days')"
    )


def downgrade() -> None:
    op.execute(
        "SELECT set_chunk_time_interval('sensor_readings', INTERVAL '1 day')"
    )

    op.drop_index(op.f("ix_waste_logs_logged_at"), table_name="waste_logs")
    op.drop_index(op.f("ix_waste_logs_compost_cycle_id"), table_name="waste_logs")
    op.drop_index(op.f("ix_waste_logs_device_id"), table_name="waste_logs")
    op.drop_table("waste_logs")

    op.drop_index("uq_compost_cycles_one_active_per_device", table_name="compost_cycles")
    op.drop_index(op.f("ix_compost_cycles_device_id"), table_name="compost_cycles")
    op.drop_table("compost_cycles")

    # Restore devices.owner_id from user_devices (pick an arbitrary member).
    op.add_column("devices", sa.Column("owner_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE devices d
        SET owner_id = ud.user_id
        FROM (
            SELECT DISTINCT ON (device_id) device_id, user_id
            FROM user_devices
            ORDER BY device_id, created_at ASC
        ) ud
        WHERE ud.device_id = d.id
        """
    )
    op.alter_column("devices", "owner_id", nullable=False)
    op.create_foreign_key(
        "devices_owner_id_fkey", "devices", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_devices_owner_id", "devices", ["owner_id"], unique=False)

    op.drop_index(op.f("ix_user_devices_device_id"), table_name="user_devices")
    op.drop_table("user_devices")

    op.drop_index(op.f("ix_users_phone"), table_name="users")
    op.drop_column("users", "phone")
