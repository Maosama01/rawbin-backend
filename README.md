# Rawbin Smart Composter Backend

The async, high-performance backend system for the Rawbin Smart Ecosystem. This backend manages real-time IoT MQTT telemetry, time-series data storage, background task processing, and alerting.

## Architecture

- **Web Framework:** FastAPI (Python)
- **Database:** TimescaleDB (PostgreSQL) for relational and time-series telemetry.
- **Message Broker (IoT):** RabbitMQ (MQTT over WebSockets)
- **Task Queue & Caching:** Celery + Redis
- **Containerization:** Docker & Docker Compose

## Local Setup & Execution

This entire backend ecosystem is designed to run locally on your machine via Docker Desktop. No cloud infrastructure or VPS configuration is required. 

### 1. Prerequisites
- **Docker Desktop** installed and running on your local machine.

### 2. Quick Start
The entire infrastructure is orchestrated via a single Docker Compose stack.

To boot the system (Database, Redis, RabbitMQ, Celery Worker, and FastAPI), open a terminal in this directory and run:
```bash
docker compose up -d
```

### 3. Services & Ports
Once Docker Compose is running, the following local services will be available:
- **FastAPI HTTP REST API:** `http://localhost:8000/api/v1`
- **FastAPI Swagger Docs:** `http://localhost:8000/docs`
- **TimescaleDB / PostgreSQL:** `localhost:5432`
- **Redis:** `localhost:6379`
- **RabbitMQ (MQTT):** `localhost:1883`

### Stopping the System
To cleanly shut down all services and preserve data locally, run:
```bash
docker compose down
```

*(Note: Your database data is persisted locally via Docker volumes, so your users and telemetry history will not be lost between restarts).*
