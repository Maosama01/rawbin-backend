# Rawbin Companion API

Backend for the Rawbin STM32-enabled smart home composter. The companion API acts as a BLE-to-HTTPS gateway, processes telemetry data, stores it securely, and runs background alert checks.

## Architecture Stack

- **FastAPI**: Core REST API handling authentication, device management, and telemetry ingestion.
- **TimescaleDB (PostgreSQL)**: Time-series database for efficient storage and querying of sensor readings.
- **Mosquitto**: MQTT Broker for real-time telemetry ingestion from STM32 devices via Wi-Fi.
- **Redis & Celery**: Background task queue for processing alerts (e.g., high temperature, fire hazard).
- **Flower**: Dashboard for monitoring background Celery tasks.
- **Docker Compose**: Containerized environment for local development and deployment.

## Features

- **JWT Authentication**: Secure user registration and login using OAuth2/JWT Bearer tokens.
- **Device Pairing**: Challenge-response based secure pairing mechanism for STM32 hardware.
- **Time-Series Telemetry**: Store and aggregate telemetry data (Temperature, Humidity, CO2, pH, Fan Speed, Fill Level, Weight).
- **MQTT Ingestion**: Background listener connecting to Mosquitto to process raw JSON telemetry payloads.
- **Alert System**: Background tasks trigger alerts based on critical threshold conditions.

## Getting Started

### Prerequisites
- Docker and Docker Compose
- Make sure ports `8000`, `5432`, `6379`, `5555`, and `1883` are available.

### Installation

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in your secrets (or leave defaults for local dev):
   ```bash
   cp .env.example .env
   ```
3. Start the entire backend stack using Docker Compose:
   ```bash
   docker compose up -d
   ```

### Accessing the Interfaces

- **API Documentation (Swagger UI)**: http://localhost:8000/docs
- **Flower Task Monitor**: http://localhost:5555
- **MQTT Broker**: tcp://localhost:1883

## Database Migrations

The project uses Alembic for schema migrations. To run migrations or create a new one, use the provided Docker exec command:

```bash
# Upgrade to latest migration
docker exec rawbin_api alembic upgrade head

# Generate a new migration after modifying models
docker exec rawbin_api alembic revision --autogenerate -m "Add new column"
```

## Hardware Integration (STM32)

Rawbin is equipped with STM32 microcontrollers. Telemetry data can be sent via:

1. **REST API**: For gateways or mobile apps receiving data over BLE, `POST /api/v1/telemetry/{device_id}`.
2. **MQTT**: For STM32 boards with Wi-Fi capabilities, publish JSON payloads to `rawbin/telemetry/{device_id}` on the local Mosquitto broker.

### Example MQTT Payload
```json
{
  "temperature_c": 55.5,
  "humidity_pct": 60.2,
  "co2_ppm": 1200,
  "ph_level": 6.8,
  "fill_level_pct": 45,
  "weight_kg": 2.3
}
```

## Running Tests

You can run the simulated end-to-end Python script to test MQTT and REST logic:
```bash
docker exec rawbin_api python test_mqtt_flow.py
```
