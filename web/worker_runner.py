# (c) 2024 Jestin Rajan. All rights reserved.
"""Dedicated worker process entrypoint for background jobs."""

import logging
import os
import signal
import threading

from web.db import init_db
from web import worker_manager


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        import json
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    if os.getenv("ENVIRONMENT", "production") != "development":
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


log = logging.getLogger(__name__)


def main() -> None:
    _configure_logging()
    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        log.info("Received signal %s - shutting down workers", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    init_db()
    worker_manager.start_all_workers()
    log.info("Worker process started")

    try:
        while not stop_event.wait(timeout=2):
            pass
    finally:
        worker_manager.stop_all_workers()
        log.info("Worker process stopped")


if __name__ == "__main__":
    main()
