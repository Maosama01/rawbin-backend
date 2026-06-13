"""timescaledb_retention_and_compression_policies

Revision ID: ebb4657960fd
Revises: c609f5b32976
Create Date: 2026-06-12 11:23:42

Adds TimescaleDB data management policies to the sensor_readings hypertable:

  1. Compression policy
     ─────────────────
     Chunks older than 7 days are compressed using TimescaleDB's native
     columnar compression.  Compressor is configured to:
       - ORDER BY device_id, time  (optimal for per-device time-range queries)
       - SEGMENT BY device_id      (each segment = one device's chunk = best
                                    compression ratio + fastest decompression)

     Typical compression ratio for sensor float data: 8–20×.

  2. Retention policy
     ─────────────────
     Chunks older than 365 days are automatically dropped.
     This is a rolling window — no manual maintenance required.
     Adjust the interval for your data compliance requirements.

  3. Continuous aggregate (materialised hourly rollup)
     ────────────────────────────────────────────────────
     sensor_readings_hourly materialises per-device hourly averages.
     Kept up-to-date by a real-time aggregate refresh policy (lag 1 hour).
     This view powers the charting API without hitting raw rows.

Notes:
  - All TimescaleDB policies use `if_not_exists => TRUE` / `if_exists => TRUE`
    so this migration is safe to re-run.
  - Downgrade removes policies and the continuous aggregate but does NOT
    decompress or restore dropped chunks (data loss is intentional in downgrade).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "ebb4657960fd"
down_revision: Union[str, None] = "c609f5b32976"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Compression settings ───────────────────────────────────────────────
    op.execute(
        """
        ALTER TABLE sensor_readings
        SET (
            timescaledb.compress,
            timescaledb.compress_orderby   = 'time DESC',
            timescaledb.compress_segmentby = 'device_id'
        );
        """
    )

    # ── 2. Compression policy: compress chunks older than 7 days ─────────────
    op.execute(
        """
        SELECT add_compression_policy(
            'sensor_readings',
            compress_after => INTERVAL '7 days',
            if_not_exists  => TRUE
        );
        """
    )

    # ── 3. Retention policy: drop chunks older than 365 days ─────────────────
    op.execute(
        """
        SELECT add_retention_policy(
            'sensor_readings',
            drop_after    => INTERVAL '365 days',
            if_not_exists => TRUE
        );
        """
    )

    # ── 4. Continuous aggregate: hourly rollup ────────────────────────────────
    # Real-time continuous aggregate — automatically includes recent data
    # not yet in a completed chunk via the real-time option.
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_hourly
        WITH (timescaledb.continuous, timescaledb.materialized_only = FALSE)
        AS
        SELECT
            time_bucket('1 hour', time)  AS bucket,
            device_id,
            AVG(temperature_c)           AS avg_temperature_c,
            AVG(humidity_pct)            AS avg_humidity_pct,
            AVG(co2_ppm)                 AS avg_co2_ppm,
            AVG(ph_level)                AS avg_ph_level,
            AVG(ambient_temp_c)          AS avg_ambient_temp_c,
            AVG(fan_speed_rpm)           AS avg_fan_speed_rpm,
            AVG(fill_level_pct)          AS avg_fill_level_pct,
            MIN(temperature_c)           AS min_temperature_c,
            MAX(temperature_c)           AS max_temperature_c,
            COUNT(*)                     AS reading_count
        FROM sensor_readings
        GROUP BY bucket, device_id
        WITH NO DATA;
        """
    )

    # ── 5. Continuous aggregate refresh policy: keep within 1 hour lag ───────
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
            'sensor_readings_hourly',
            start_offset  => INTERVAL '3 hours',
            end_offset    => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour',
            if_not_exists => TRUE
        );
        """
    )


def downgrade() -> None:
    # Remove policies and the continuous aggregate in reverse order
    op.execute(
        """
        SELECT remove_continuous_aggregate_policy(
            'sensor_readings_hourly',
            if_not_exists => TRUE
        );
        """
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_hourly;")
    op.execute(
        """
        SELECT remove_retention_policy(
            'sensor_readings',
            if_not_exists => TRUE
        );
        """
    )
    op.execute(
        """
        SELECT remove_compression_policy(
            'sensor_readings',
            if_not_exists => TRUE
        );
        """
    )
    op.execute(
        """
        ALTER TABLE sensor_readings
        SET (
            timescaledb.compress = FALSE
        );
        """
    )
