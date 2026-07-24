from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Config
from .models import TaskInput


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, config: Config):
        self.api_url = config.api_url
        self.token = config.token

    def check(self) -> str:
        user = self._call("getMe", {})
        if not user.get("is_bot"):
            raise TelegramError("Telegram token does not belong to a bot")
        return f"@{user['username']}" if user.get("username") else user.get("first_name", "")

    def poll(self, offset: int) -> tuple[list[TaskInput], int]:
        if offset == 0:
            updates = self._call(
                "getUpdates",
                {"offset": -1, "limit": 1, "timeout": 0, "allowed_updates": ["message"]},
                timeout=10,
            )
            return [], int(updates[-1]["update_id"]) + 1 if updates else 1

        updates = self._call(
            "getUpdates",
            {
                "offset": offset,
                "limit": 100,
                "timeout": 30,
                "allowed_updates": ["message"],
            },
            timeout=40,
        )
        next_offset = offset
        inputs: list[TaskInput] = []
        for update in updates:
            next_offset = max(next_offset, int(update["update_id"]) + 1)
            message = update.get("message")
            if not message or message.get("from", {}).get("is_bot"):
                continue
            sender = message.get("from", {})
            chat = message.get("chat", {})
            voice = message.get("voice") or {}
            text = str(message.get("text") or message.get("caption") or "").strip()
            timestamp = datetime.fromtimestamp(int(message.get("date", 0)), tz=UTC).isoformat()
            inputs.append(
                TaskInput(
                    message_id=(
                        f"{update['update_id']}:{chat.get('id', 0)}:{message.get('message_id', 0)}"
                    ),
                    chat_id=int(chat.get("id", 0)),
                    user_id=int(sender.get("id", 0)),
                    chat_type=str(chat.get("type", "")),
                    reply_to_message_id=int(message.get("message_id", 0)),
                    text=text,
                    received_at=timestamp,
                    voice_file_id=str(voice.get("file_id", "")),
                    voice_duration_seconds=int(voice.get("duration", 0)),
                    voice_mime_type=str(voice.get("mime_type", "")),
                )
            )
        return inputs, next_offset

    def send_text(self, input_value: TaskInput, text: str) -> None:
        payload: dict[str, Any] = {
            "chat_id": input_value.chat_id,
            "text": text[:4096],
        }
        if input_value.reply_to_message_id:
            payload["reply_parameters"] = {"message_id": input_value.reply_to_message_id}
        self._call("sendMessage", payload)

    def download_file(self, file_id: str, destination: Path, maximum_bytes: int) -> None:
        file = self._call("getFile", {"file_id": file_id})
        raw_path = str(file.get("file_path", "")).strip()
        path = PurePosixPath(raw_path)
        if not raw_path or path.is_absolute() or ".." in path.parts:
            raise TelegramError("Telegram returned an invalid file path")
        file_size = int(file.get("file_size", 0))
        if file_size > maximum_bytes:
            raise TelegramError(f"Telegram voice file is too large: {file_size} bytes")
        url = f"{self.api_url}/file/bot{self.token}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=40) as response:
                content = response.read(maximum_bytes + 1)
        except (OSError, urllib.error.URLError) as error:
            raise TelegramError("Telegram file download failed") from error
        if len(content) > maximum_bytes:
            raise TelegramError(f"Telegram voice file exceeds {maximum_bytes} bytes")
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)

    def _call(self, method: str, payload: dict[str, Any], timeout: int = 40) -> Any:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.api_url}/bot{self.token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                envelope = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                envelope = json.load(error)
            except (ValueError, OSError):
                raise TelegramError(f"Telegram {method} failed (HTTP {error.code})") from error
        except (OSError, ValueError) as error:
            raise TelegramError(f"Telegram {method} request failed") from error
        if not envelope.get("ok"):
            description = str(envelope.get("description", "unknown API error"))[:300]
            code = envelope.get("error_code", "unknown")
            raise TelegramError(f"Telegram {method} failed ({code}): {description}")
        return envelope.get("result")
