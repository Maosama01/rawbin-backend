"""
app/core/logging.py
────────────────────
Structured JSON logging configuration for the Rawbin API.

Uses structlog for machine-parseable, context-enriched log output suitable
for ingestion by AWS CloudWatch Logs Insights, Datadog, or similar.

Log format:
  Development → pretty-printed with colors and tracebacks
  Staging/Production → compact JSON per line (one event per log call)

Usage:
  Call configure_logging() once at application startup.
  All modules should use standard `logging.getLogger(__name__)` — structlog
  automatically processes these calls through its pipeline.

Example output (production):
  {"timestamp":"2024-01-15T10:23:45Z","level":"info","event":"User logged in",
   "logger":"app.api.v1.auth","user_id":"abc123"}
"""

import logging
import logging.config
import sys

import structlog

from app.core.config import get_settings

settings = get_settings()


def configure_logging() -> None:
    """
    Configure stdlib logging + structlog processors.

    Must be called once at application boot, before any log messages are emitted.
    """
    is_dev = settings.APP_ENV == "development"

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,          # Thread/async local context
        structlog.stdlib.add_log_level,                   # "level": "info"
        structlog.stdlib.add_logger_name,                 # "logger": "app.api.v1.auth"
        structlog.processors.TimeStamper(fmt="iso"),      # "timestamp": "2024-..."
        structlog.processors.StackInfoRenderer(),
    ]

    if is_dev:
        # Human-readable colored output for local development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # Machine-readable JSON for staging/production (CloudWatch, Datadog)
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.DEBUG else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through structlog
    log_level = "DEBUG" if settings.DEBUG else "INFO"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "structlog",
                },
            },
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.dev.ConsoleRenderer(colors=is_dev),
                    ],
                    "foreign_pre_chain": shared_processors,
                },
            },
            "root": {
                "handlers": ["default"],
                "level": log_level,
            },
            "loggers": {
                # Silence noisy but useful libs at WARNING in production
                "sqlalchemy.engine": {"level": "DEBUG" if settings.DEBUG else "WARNING"},
                "uvicorn.access": {"level": log_level},
                "celery": {"level": log_level},
            },
        }
    )
