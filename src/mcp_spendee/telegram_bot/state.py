from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .models import CodexResult, QueuedEvent, StateSummary, TaskInput


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.marker = 0
        self.events: list[QueuedEvent] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        content = json.loads(self.path.read_text(encoding="utf-8"))
        self.marker = int(content.get("marker", 0))
        self.events = [QueuedEvent.from_dict(item) for item in content.get("events", [])]
        for event in self.events:
            if event.status == "processing":
                event.status = "pending"

    def add(self, inputs: list[TaskInput], marker: int) -> None:
        with self.lock:
            existing = {event.input.message_id for event in self.events}
            for input_value in inputs:
                if input_value.message_id not in existing:
                    self.events.append(QueuedEvent(input=input_value, updated_at=_now()))
                    existing.add(input_value.message_id)
            self.marker = marker
            self._prune()
            self._save()

    def claim(self) -> QueuedEvent | None:
        with self.lock:
            for event in self.events:
                if event.status != "pending":
                    continue
                event.status = "processing"
                event.attempts += 1
                event.updated_at = _now()
                self._save()
                return QueuedEvent.from_dict(event.to_dict())
        return None

    def mark_acknowledged(self, message_id: str) -> None:
        self._update(message_id, lambda event: setattr(event, "acknowledged", True))

    def save_transcription(self, message_id: str, text: str) -> None:
        self._update(message_id, lambda event: setattr(event.input, "text", text))

    def save_result(self, message_id: str, result: CodexResult) -> None:
        self._update(message_id, lambda event: setattr(event, "result", result))

    def complete(self, message_id: str, status: str) -> None:
        def mutate(event: QueuedEvent) -> None:
            event.status = status
            event.last_error = ""

        self._update(message_id, mutate)

    def retry_or_fail(self, message_id: str, maximum: int, cause: Exception) -> bool:
        will_retry = False

        def mutate(event: QueuedEvent) -> None:
            nonlocal will_retry
            event.last_error = str(cause)[:1000]
            will_retry = event.attempts < maximum
            event.status = "pending" if will_retry else "failed"

        self._update(message_id, mutate)
        return will_retry

    def summary(self) -> StateSummary:
        with self.lock:
            summary = StateSummary(marker=self.marker)
            for event in self.events:
                if event.status == "pending":
                    summary.pending += 1
                elif event.status == "processing":
                    summary.processing += 1
                elif event.status in {"done", "ignored"}:
                    summary.done += 1
                elif event.status == "failed":
                    summary.failed += 1
            return summary

    def _update(self, message_id: str, mutate: Callable[[QueuedEvent], None]) -> None:
        with self.lock:
            for event in self.events:
                if event.input.message_id == message_id:
                    mutate(event)
                    event.updated_at = _now()
                    self._prune()
                    self._save()
                    return
        raise KeyError(f"event {message_id} is missing")

    def _prune(self) -> None:
        finished = {"done", "ignored", "failed"}
        excess = sum(event.status in finished for event in self.events) - 500
        if excess <= 0:
            return
        kept: list[QueuedEvent] = []
        for event in self.events:
            if excess > 0 and event.status in finished:
                excess -= 1
            else:
                kept.append(event)
        self.events = kept

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "marker": self.marker,
                        "events": [event.to_dict() for event in self.events],
                    },
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
