from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .codex import run_codex
from .config import Config
from .models import CodexResult, QueuedEvent, TaskInput
from .state import StateStore
from .transcriber import WhisperTranscriber


class Messenger(Protocol):
    def send_text(self, input_value: TaskInput, text: str) -> None: ...

    def download_file(self, file_id: str, destination: Path, maximum_bytes: int) -> None: ...


class Processor:
    def __init__(
        self,
        config: Config,
        store: StateStore,
        client: Messenger,
        transcriber: WhisperTranscriber | None = None,
        codex_runner: Callable[[Config, TaskInput, tuple[Path, ...]], CodexResult] = run_codex,
        now: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.store = store
        self.client = client
        self.transcriber = transcriber
        self.codex_runner = codex_runner
        self.now = now or (lambda: datetime.now(tz=ZoneInfo("Europe/Moscow")))

    def process(self, event: QueuedEvent) -> str:
        input_value = event.input
        command = telegram_command(input_value.text)
        if command == "/whoami":
            self.client.send_text(input_value, f"Ваш userId: {input_value.user_id}")
            return "ignored"
        if command in {"/help", "/start"}:
            self.client.send_text(
                input_value,
                "Пришлите расход или доход текстом либо голосом, например: "
                "«Запиши 850 рублей за такси в операционку». Я подберу категорию "
                "и метки, проверю preview и запишу транзакцию в Spendee. Также "
                "можно прислать скриншот отдельной банковской транзакции. "
                "Команды: /whoami — показать userId, /status — состояние очереди.",
            )
            return "ignored"
        if input_value.user_id not in self.config.allowed_user_ids:
            self.client.send_text(
                input_value,
                "Доступ не настроен. Ваш userId: "
                f"{input_value.user_id}. Добавьте его в "
                "TELEGRAM_BOT_ALLOWED_USER_IDS.",
            )
            return "ignored"
        if self.config.private_only and input_value.chat_type != "private":
            self.client.send_text(
                input_value,
                "Записываю транзакции только из личного диалога с ботом.",
            )
            return "ignored"
        if command == "/status":
            summary = self.store.summary()
            processing = max(0, summary.processing - 1)
            self.client.send_text(
                input_value,
                "Сервис работает. "
                f"Telegram offset: {summary.marker}. В очереди: {summary.pending}, "
                f"в работе: {processing}, завершено: {summary.done}, "
                f"ошибок: {summary.failed}.",
            )
            return "ignored"

        if input_value.image_file_size > self.config.max_image_bytes:
            self.client.send_text(
                input_value,
                "Изображение слишком большое. Пришлите PNG или JPEG размером "
                f"не больше {self.config.max_image_bytes // (1 << 20)} МБ.",
            )
            return "ignored"
        if input_value.voice_file_id and not input_value.text.strip():
            if input_value.voice_duration_seconds > self.config.max_voice_seconds:
                self.client.send_text(
                    input_value,
                    "Голосовое слишком длинное. Пришлите запись не длиннее "
                    f"{self.config.max_voice_seconds} секунд.",
                )
                return "ignored"
            if self.transcriber is None:
                raise RuntimeError("voice transcriber is unavailable")
            if not event.acknowledged:
                self.client.send_text(
                    input_value,
                    "Принял голосовое. Распознаю и записываю в Spendee — "
                    "это может занять пару минут.",
                )
                self.store.mark_acknowledged(input_value.message_id)
                event.acknowledged = True
            input_value.text = self.transcriber.transcribe(input_value)
            self.store.save_transcription(input_value.message_id, input_value.text)
            logging.info("message %s: voice transcription finished", input_value.message_id)
        if not input_value.text.strip() and not input_value.image_file_id:
            self.client.send_text(
                input_value,
                "Не нашёл описания транзакции. Пришлите расход или доход "
                "текстом, голосом либо скриншотом.",
            )
            return "ignored"

        local_now = self.now().astimezone(ZoneInfo("Europe/Moscow"))
        input_value.current_date = local_now.strftime("%Y-%m-%d")
        input_value.current_time_moscow = local_now.strftime("%H:%M:%S")
        input_value.text = input_value.text[: self.config.max_input_chars]

        if event.result is None and not event.acknowledged:
            acknowledgement = (
                "Принял скриншот. Распознаю транзакцию и проверяю кошелёк, "
                "категорию и метки — обычно это занимает до минуты."
                if input_value.image_file_id
                else "Принял. Проверяю кошелёк, категорию и метки — обычно это занимает до минуты."
            )
            self.client.send_text(
                input_value,
                acknowledgement,
            )
            self.store.mark_acknowledged(input_value.message_id)

        if event.result is not None:
            result = event.result
            result.validate()
        else:
            if self.config.log_input:
                logging.info(
                    "message %s: starting Codex; input=%s",
                    input_value.message_id,
                    json.dumps(input_value.to_dict(), ensure_ascii=False),
                )
            else:
                logging.info(
                    "message %s: starting Codex; input logging is disabled",
                    input_value.message_id,
                )
            with self._downloaded_image(input_value) as image_path:
                image_paths = (image_path,) if image_path else ()
                result = self.codex_runner(self.config, input_value, image_paths)
            self.store.save_result(input_value.message_id, result)
            logging.info(
                "message %s: Codex finished; status=%s",
                input_value.message_id,
                result.status,
            )
        if result.status == "error":
            raise RuntimeError(result.error[:500])
        self.client.send_text(input_value, result.message)
        return "done"

    @contextmanager
    def _downloaded_image(self, input_value: TaskInput) -> Iterator[Path | None]:
        if not input_value.image_file_id:
            yield None
            return
        if input_value.image_file_size > self.config.max_image_bytes:
            raise RuntimeError(f"Telegram image is too large: {input_value.image_file_size} bytes")
        suffix = ".png" if input_value.image_mime_type == "image/png" else ".jpg"
        directory = Path(tempfile.mkdtemp(prefix="telegram-spendee-image-"))
        os.chmod(directory, 0o700)
        image_path = directory / f"screenshot{suffix}"
        try:
            self.client.download_file(
                input_value.image_file_id,
                image_path,
                self.config.max_image_bytes,
            )
            yield image_path
        finally:
            image_path.unlink(missing_ok=True)
            directory.rmdir()


def telegram_command(text: str) -> str:
    fields = text.strip().lower().split()
    if not fields or not fields[0].startswith("/"):
        return ""
    return fields[0].split("@", maxsplit=1)[0]
