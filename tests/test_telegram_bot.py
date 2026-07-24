from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mcp_spendee.telegram_bot.config import Config, _split_ids
from mcp_spendee.telegram_bot.models import CodexResult, QueuedEvent, TaskInput
from mcp_spendee.telegram_bot.processor import Processor, telegram_command
from mcp_spendee.telegram_bot.state import StateStore
from mcp_spendee.telegram_bot.telegram import TelegramClient


def make_config(tmp_path: Path, allowed: frozenset[int] = frozenset({42})) -> Config:
    return Config(
        token="test-token",
        allowed_user_ids=allowed,
        bot_repo=tmp_path,
        codex_home=tmp_path,
        state_file=tmp_path / "state.json",
        skill_path=tmp_path / "SKILL.md",
        output_schema_path=tmp_path / "schema.json",
        whisper_binary=tmp_path / "whisper",
        whisper_model_path=tmp_path / "model.bin",
    )


class FakeMessenger:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.downloaded_file_id = ""

    def send_text(self, _input_value: TaskInput, text: str) -> None:
        self.messages.append(text)

    def download_file(self, file_id: str, destination: Path, _maximum_bytes: int) -> None:
        self.downloaded_file_id = file_id
        destination.write_bytes(b"fake image")


def test_state_store_persists_and_recovers_processing(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.add([TaskInput(message_id="one", text="такси")], 99)
    event = store.claim()
    assert event is not None
    assert event.attempts == 1
    assert path.stat().st_mode & 0o777 == 0o600

    reopened = StateStore(path)
    assert reopened.marker == 99
    recovered = reopened.claim()
    assert recovered is not None
    assert recovered.attempts == 2


def test_state_store_deduplicates_message_ids(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    item = TaskInput(message_id="same")
    store.add([item, item], 1)
    store.add([item], 2)
    assert len(store.events) == 1


def test_processor_runs_codex_and_saves_result(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    messenger = FakeMessenger()
    calls: list[TaskInput] = []

    def runner(
        _config: Config, input_value: TaskInput, _image_paths: tuple[Path, ...]
    ) -> CodexResult:
        calls.append(input_value)
        return CodexResult(
            status="logged",
            message="Записал расход 850 ₽ в Операционку.",
        )

    processor = Processor(
        make_config(tmp_path),
        store,
        messenger,
        codex_runner=runner,
        now=lambda: datetime(2026, 7, 24, 13, 15, tzinfo=ZoneInfo("Europe/Moscow")),
    )
    input_value = TaskInput(
        message_id="one",
        chat_id=42,
        user_id=42,
        chat_type="private",
        text="Запиши 850 рублей за такси в операционку",
    )
    store.add([input_value], 1)
    event = store.claim()
    assert event is not None

    assert processor.process(event) == "done"
    assert calls[0].current_date == "2026-07-24"
    assert calls[0].current_time_moscow == "13:15:00"
    assert messenger.messages == [
        "Принял. Проверяю кошелёк, категорию и метки — обычно это занимает до минуты.",
        "Записал расход 850 ₽ в Операционку.",
    ]
    persisted = json.loads((tmp_path / "state.json").read_text())
    assert persisted["events"][0]["result"]["status"] == "logged"


def test_processor_downloads_image_for_codex_and_removes_it(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    messenger = FakeMessenger()
    attached_path: Path | None = None

    def runner(
        _config: Config, _input_value: TaskInput, image_paths: tuple[Path, ...]
    ) -> CodexResult:
        nonlocal attached_path
        assert len(image_paths) == 1
        attached_path = image_paths[0]
        assert attached_path.suffix == ".png"
        assert attached_path.read_bytes() == b"fake image"
        assert attached_path.parent.stat().st_mode & 0o777 == 0o700
        return CodexResult(status="needs_input", message="Общий или Операционка?")

    processor = Processor(
        make_config(tmp_path),
        store,
        messenger,
        codex_runner=runner,
    )
    input_value = TaskInput(
        message_id="image-one",
        chat_id=42,
        user_id=42,
        chat_type="private",
        image_file_id="telegram-image-id",
        image_mime_type="image/png",
        image_file_name="receipt.png",
        image_file_size=1024,
    )
    store.add([input_value], 1)
    event = store.claim()
    assert event is not None

    assert processor.process(event) == "done"
    assert messenger.downloaded_file_id == "telegram-image-id"
    assert attached_path is not None
    assert not attached_path.exists()
    assert "Принял скриншот" in messenger.messages[0]
    assert messenger.messages[-1] == "Общий или Операционка?"


def test_processor_rejects_non_allowlisted_user(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    messenger = FakeMessenger()
    processor = Processor(make_config(tmp_path), store, messenger)
    event = QueuedEvent(
        input=TaskInput(
            message_id="one",
            chat_id=7,
            user_id=7,
            chat_type="private",
            text="500 рублей на обед",
        )
    )

    assert processor.process(event) == "ignored"
    assert "Ваш userId: 7" in messenger.messages[0]


def test_processor_handles_help_before_allowlist(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    messenger = FakeMessenger()
    processor = Processor(make_config(tmp_path, allowed=frozenset()), store, messenger)
    event = QueuedEvent(
        input=TaskInput(
            message_id="one",
            chat_id=7,
            user_id=7,
            chat_type="private",
            text="/help",
        )
    )

    assert processor.process(event) == "ignored"
    assert "расход или доход" in messenger.messages[0]


def test_codex_result_validation() -> None:
    CodexResult(status="logged", message="Готово").validate()
    CodexResult(status="needs_input", message="Какой кошелёк?").validate()
    CodexResult(status="error", message="Ошибка", error="timeout").validate()
    with pytest.raises(ValueError):
        CodexResult(status="logged", message="").validate()
    with pytest.raises(ValueError):
        CodexResult(status="other", message="x").validate()


def test_split_ids_and_commands() -> None:
    assert _split_ids("42, 51;99") == frozenset({42, 51, 99})
    with pytest.raises(ValueError):
        _split_ids("nope")
    assert telegram_command("/STATUS@my_bot extra") == "/status"
    assert telegram_command("запиши такси") == ""


def test_telegram_selects_largest_photo() -> None:
    image = TelegramClient._image_from_message(
        {
            "photo": [
                {"file_id": "small", "width": 90, "height": 160, "file_size": 1000},
                {"file_id": "large", "width": 1080, "height": 1920, "file_size": 9000},
            ]
        }
    )
    assert image["file_id"] == "large"
    assert image["mime_type"] == "image/jpeg"


def test_telegram_accepts_png_document_and_rejects_pdf() -> None:
    png = TelegramClient._image_from_message(
        {
            "document": {
                "file_id": "png",
                "file_name": "bank.png",
                "mime_type": "image/png",
                "file_size": 123,
            }
        }
    )
    pdf = TelegramClient._image_from_message(
        {"document": {"file_id": "pdf", "mime_type": "application/pdf"}}
    )
    assert png["file_id"] == "png"
    assert pdf == {}
