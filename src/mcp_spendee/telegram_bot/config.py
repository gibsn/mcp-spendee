from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_ids(raw: str) -> frozenset[int]:
    normalized = raw.replace(",", " ").replace(";", " ")
    result: set[int] = set()
    for item in normalized.split():
        try:
            value = int(item)
        except ValueError as error:
            raise ValueError(
                f"TELEGRAM_BOT_ALLOWED_USER_IDS contains invalid ID {item!r}"
            ) from error
        if value <= 0:
            raise ValueError(f"TELEGRAM_BOT_ALLOWED_USER_IDS contains invalid ID {item!r}")
        result.add(value)
    return frozenset(result)


@dataclass(frozen=True)
class Config:
    token: str
    allowed_user_ids: frozenset[int]
    bot_repo: Path
    codex_home: Path
    state_file: Path
    skill_path: Path
    output_schema_path: Path
    api_url: str = "https://api.telegram.org"
    private_only: bool = True
    codex_timeout_seconds: int = 300
    voice_timeout_seconds: int = 300
    max_attempts: int = 3
    max_input_chars: int = 4000
    max_voice_seconds: int = 120
    max_voice_bytes: int = 20 << 20
    log_input: bool = True
    codex_binary: str = "codex"
    ffmpeg_binary: str = "ffmpeg"
    whisper_binary: Path = Path("whisper-cli")
    whisper_model_path: Path = Path("ggml-base.bin")

    @classmethod
    def load(cls) -> Config:
        home = Path.home()
        codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
        bot_repo = Path(os.environ.get("TELEGRAM_BOT_REPO", home / "ai/repos/mcp-spendee"))
        whisper_root = home / ".local/share/telegram-spendee-bot/whisper.cpp"
        config = cls(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_user_ids=_split_ids(os.environ.get("TELEGRAM_BOT_ALLOWED_USER_IDS", "")),
            bot_repo=bot_repo,
            codex_home=codex_home,
            state_file=Path(
                os.environ.get(
                    "TELEGRAM_BOT_STATE_FILE",
                    home / ".local/state/aitools/telegram-spendee-bot/state.json",
                )
            ),
            skill_path=Path(
                os.environ.get(
                    "TELEGRAM_BOT_SKILL_PATH",
                    codex_home / "skills/spendee-add-transaction/SKILL.md",
                )
            ),
            output_schema_path=Path(
                os.environ.get(
                    "TELEGRAM_BOT_OUTPUT_SCHEMA",
                    bot_repo / "config/telegram-output.schema.json",
                )
            ),
            api_url=os.environ.get("TELEGRAM_BOT_API_URL", "https://api.telegram.org").rstrip("/"),
            private_only=_env_bool("TELEGRAM_BOT_PRIVATE_ONLY", True),
            codex_timeout_seconds=_env_int("TELEGRAM_BOT_CODEX_TIMEOUT_SECONDS", 300, 30, 1800),
            voice_timeout_seconds=_env_int("TELEGRAM_BOT_VOICE_TIMEOUT_SECONDS", 300, 30, 1800),
            max_attempts=_env_int("TELEGRAM_BOT_MAX_ATTEMPTS", 3, 1, 10),
            max_input_chars=_env_int("TELEGRAM_BOT_MAX_INPUT_CHARS", 4000, 100, 50_000),
            max_voice_seconds=_env_int("TELEGRAM_BOT_MAX_VOICE_SECONDS", 120, 5, 900),
            log_input=_env_bool("TELEGRAM_BOT_LOG_INPUT", True),
            codex_binary=os.environ.get("CODEX_BIN", "codex"),
            ffmpeg_binary=os.environ.get("TELEGRAM_BOT_FFMPEG_BIN", "ffmpeg"),
            whisper_binary=Path(
                os.environ.get(
                    "TELEGRAM_BOT_WHISPER_BIN",
                    whisper_root / "build/bin/whisper-cli",
                )
            ),
            whisper_model_path=Path(
                os.environ.get(
                    "TELEGRAM_BOT_WHISPER_MODEL",
                    whisper_root / "models/ggml-base.bin",
                )
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is empty")
        for label, path in {
            "bot repo": self.bot_repo,
            "skill": self.skill_path,
            "output schema": self.output_schema_path,
            "whisper executable": self.whisper_binary,
            "whisper model": self.whisper_model_path,
        }.items():
            if not path.exists():
                raise ValueError(f"{label} path is unavailable: {path}")
        for label, binary in {
            "codex": self.codex_binary,
            "ffmpeg": self.ffmpeg_binary,
        }.items():
            if shutil.which(binary) is None:
                raise ValueError(f"{label} executable is unavailable: {binary}")
