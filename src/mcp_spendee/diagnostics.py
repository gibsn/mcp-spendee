"""Correlated diagnostics containing only explicitly selected operational fields."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("mcp_spendee")


@dataclass
class CallTrace:
    call_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: threading.Event = field(default_factory=threading.Event)


current_trace: ContextVar[CallTrace | None] = ContextVar("spendee_call", default=None)


def configure_logging() -> None:
    # A dedicated stderr handler avoids logging HTTP library payloads or MCP arguments.
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def resource_tag(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def event(name: str, **fields: Any) -> None:
    trace = current_trace.get()
    logger.info(json.dumps({"event": name, "call_id": trace.call_id if trace else None, **fields}))


def check_cancelled() -> None:
    trace = current_trace.get()
    if trace is not None and trace.cancelled.is_set():
        raise RuntimeError("MCP call cancelled; no further Spendee requests will be started")


@contextmanager
def span(name: str, **fields: Any) -> Iterator[None]:
    started = time.monotonic()
    event(name + "_start", **fields)
    try:
        yield
    except BaseException as exc:
        event(
            name + "_error",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error_type=type(exc).__name__,
            **fields,
        )
        raise
    else:
        event(name + "_end", elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
