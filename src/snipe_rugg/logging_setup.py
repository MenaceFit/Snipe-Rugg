"""Structured JSON logging (spec section 109).

Attach structured fields to a log call with extra={"fields": {...}}, e.g.:

    logger.info("wallet_buy", extra={"fields": {"wallet": w, "latency_ms": 124}})
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import Any

import orjson


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return orjson.dumps(payload, default=str).decode()


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s: %(message)s %(structured)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", None)
        record.structured = " ".join(f"{k}={v}" for k, v in fields.items()) if fields else ""
        return super().format(record)


def configure_logging(*, level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
