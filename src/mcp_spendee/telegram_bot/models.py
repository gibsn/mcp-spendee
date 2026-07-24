from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TaskInput:
    message_id: str
    chat_id: int = 0
    user_id: int = 0
    chat_type: str = ""
    reply_to_message_id: int = 0
    text: str = ""
    received_at: str = ""
    voice_file_id: str = ""
    voice_duration_seconds: int = 0
    voice_mime_type: str = ""
    current_date: str = ""
    current_time_moscow: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskInput:
        return cls(
            **{
                key: value.get(key, field.default)
                for key, field in cls.__dataclass_fields__.items()
            }
        )


@dataclass
class CodexResult:
    status: str
    message: str
    error: str = ""

    def validate(self) -> None:
        if self.status in {"logged", "needs_input"} and self.message.strip():
            return
        if self.status == "error" and self.message.strip() and self.error.strip():
            return
        raise ValueError("Codex returned an invalid result")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CodexResult:
        result = cls(
            status=str(value.get("status", "")),
            message=str(value.get("message", "")),
            error=str(value.get("error", "")),
        )
        result.validate()
        return result


@dataclass
class QueuedEvent:
    input: TaskInput
    status: str = "pending"
    attempts: int = 0
    acknowledged: bool = False
    result: CodexResult | None = None
    last_error: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> QueuedEvent:
        result = value.get("result")
        return cls(
            input=TaskInput.from_dict(value["input"]),
            status=str(value.get("status", "pending")),
            attempts=int(value.get("attempts", 0)),
            acknowledged=bool(value.get("acknowledged", False)),
            result=CodexResult.from_dict(result) if result else None,
            last_error=str(value.get("last_error", "")),
            updated_at=str(value.get("updated_at", "")),
        )


@dataclass
class StateSummary:
    marker: int = 0
    pending: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
