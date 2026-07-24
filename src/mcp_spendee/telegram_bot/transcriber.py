from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .models import TaskInput
from .telegram import TelegramClient


class WhisperTranscriber:
    def __init__(self, config: Config, client: TelegramClient):
        self.config = config
        self.client = client

    def transcribe(self, input_value: TaskInput) -> str:
        if not input_value.voice_file_id.strip():
            raise ValueError("voice file ID is empty")
        directory = Path(tempfile.mkdtemp(prefix="telegram-spendee-voice-"))
        os.chmod(directory, 0o700)
        try:
            source = directory / "voice.ogg"
            self.client.download_file(
                input_value.voice_file_id, source, self.config.max_voice_bytes
            )
            wav = directory / "voice.wav"
            self._run(
                [
                    self.config.ffmpeg_binary,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav),
                ]
            )
            output_prefix = directory / "transcript"
            self._run(
                [
                    str(self.config.whisper_binary),
                    "-m",
                    str(self.config.whisper_model_path),
                    "-f",
                    str(wav),
                    "-l",
                    "ru",
                    "-nt",
                    "-otxt",
                    "-of",
                    str(output_prefix),
                ]
            )
            text = output_prefix.with_suffix(".txt").read_text(encoding="utf-8").strip()
            if not text:
                raise RuntimeError("voice transcription is empty")
            return text
        finally:
            for path in directory.iterdir():
                path.unlink(missing_ok=True)
            directory.rmdir()

    def _run(self, command: list[str]) -> None:
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=self.config.voice_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"{Path(command[0]).name} timed out") from error
        except subprocess.CalledProcessError as error:
            details = error.stderr.decode(errors="replace").strip()[:500]
            raise RuntimeError(f"{Path(command[0]).name} failed: {details}") from error
