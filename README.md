# rwb-iot-backend

Backend for **Rawbin**, a BLE-enabled smart home composter. The physical bin
(STM32 + sensors) talks over Bluetooth to the mobile app, which acts as a
BLE→MQTT/HTTPS gateway to this backend.

```
STM32 device --BLE--> phone (gateway) --MQTT publish--> broker
                                                          │
                                          mqtt_listener (subscriber)
                                                          │
                                                   TimescaleDB
                                                          ▲
                          mobile app --HTTPS (REST)-------┘  (reads history, manages account/devices)
```

## Processes

The system runs as **separate processes**, each with one responsibility:

| Process | Command | Role |
|---|---|---|
| API | `uvicorn app.main:app` | REST API for the mobile app (auth, devices, history, cycles, waste logs) |
| MQTT worker | `python -m app.workers.mqtt_listener` | **Sole** telemetry ingestion: subscribes to the broker and writes sensor readings to the hypertable |
| Celery worker | `celery -A app.workers.celery_app worker` | Post-ingestion jobs: alert evaluation + push notifications |
| Flower | `celery -A app.workers.celery_app flower` | Celery monitoring UI |

> **Ingestion is MQTT-only.** There is no HTTP telemetry-ingest endpoint — that
> avoids a second, divergent write path. The MQTT worker is decoupled from the
> API so scaling/restarting the API never duplicates or interrupts ingestion.
>
> ⚠️ **Run exactly one MQTT worker replica.** Multiple subscribers on
> `rawbin/telemetry/+` would each insert every reading. On ECS, set the
> service desired count to 1 with `minimumHealthyPercent: 0` so deploys never
> run two at once.

## Tech stack

FastAPI · SQLAlchemy 2 (async, asyncpg) · PostgreSQL + TimescaleDB ·
Redis · Celery · paho-mqtt · Pydantic v2 · Alembic · python-jose (JWT) ·
passlib/bcrypt · Fernet (device secrets) · Firebase Admin (push) · Twilio (SMS).

## Data model

- **users** — accounts; `phone` (unique) enables SMS-OTP login.
- **user_devices** — equal-access sharing join table (`UNIQUE(user_id, device_id)`);
  every linked user has full access.
- **devices** — composters; HMAC pairing via Fernet-encrypted secret.
- **sensor_readings** — TimescaleDB hypertable, PK `(time, device_id)`,
  **7-day chunks** (tuned for the ~10-minute reading cadence). Continuous
  aggregate `sensor_readings_hourly` powers hour/day history tiers.
- **compost_cycles** — lean batch lifecycle (`active → curing → completed`);
  one active cycle per device (partial unique index). Derived metrics are
  computed on demand, never stored.
- **waste_logs** — typed material entries (`greens|browns|food|other`) with
  optional weight and nullable `compost_cycle_id`; `device_id` is denormalized
  for fast monthly summaries.
- **alert_events**, **device_configs**, **refresh_tokens**.

## Authentication

- **Human users:** JWT access token (15 min) + rotating, hashed refresh token
  (30 days). Two login methods:
  - `POST /auth/login` — email + password.
  - `POST /auth/otp/request` → `POST /auth/otp/verify` — SMS one-time code
    (hashed in Redis, TTL + attempt-limited; SMS via Twilio or a logging stub).
- **Devices:** pair via HMAC challenge/response (`/devices/pair/...`); telemetry
  is authenticated at the MQTT broker, not via a device JWT.

## Layout

```
app/
  main.py            FastAPI app factory + lifespan (DB/Redis warmup)
  api/v1/            HTTP routes (auth, devices, telemetry, status, alerts,
                     users, ota, cycles, waste)
  core/              config, logging, security, mqtt client
  db/                engine/session + ORM models
  schemas/           Pydantic request/response models
  services/          business logic: otp, sms, auth_service, device_access
  workers/           celery_app, tasks/, db.py (sync engine), mqtt_listener.py
```

## Local development

```bash
cp .env.example .env       # fill SECRET_KEY etc.
docker compose up --build  # db, redis, mosquitto, api, mqtt_worker, worker, flower
docker compose exec api alembic upgrade head
```

- API docs (DEBUG only): http://localhost:8000/docs
- Flower: http://localhost:5555

SMS defaults to `SMS_PROVIDER=stub` — OTP codes are printed to the
`mqtt_worker`/`api` logs so you can log in without a Twilio account.

## Tests

Integration tests run against the live Docker stack (Postgres + Redis):

```bash
docker compose exec api pytest
```
