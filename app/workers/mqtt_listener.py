"""
app/workers/mqtt_listener.py
─────────────────────────────
Standalone MQTT ingestion worker.

Run as its own long-lived process — separate from the API — so a single
subscriber owns the broker connection:

    python -m app.workers.mqtt_listener

It owns an asyncio event loop (used for async DB writes), hands that loop to
the paho client so messages can bridge onto it, and then blocks forever until
SIGINT/SIGTERM, at which point it shuts the broker connection down cleanly.
"""

import asyncio
import logging
import signal

from app.core.logging import configure_logging
from app.core.mqtt import MQTTIngestionClient

configure_logging()
logger = logging.getLogger(__name__)


async def _run() -> None:
    loop = asyncio.get_running_loop()
    client = MQTTIngestionClient()
    client.start(loop)

    stop = asyncio.Event()

    def _request_stop(*_args) -> None:
        logger.info("Shutdown signal received — stopping MQTT listener…")
        stop.set()

    # add_signal_handler is unavailable on Windows' default loop; fall back to
    # signal.signal there so local dev on Windows still stops cleanly.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, _request_stop)

    logger.info("MQTT listener running. Waiting for telemetry…")
    await stop.wait()

    client.stop()
    logger.info("MQTT listener stopped.")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
