from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    """Raised when required server configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    email: str | None
    password: str | None
    timezone: str = "Europe/Moscow"
    global_currency: str = "EUR"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            email=os.getenv("SPENDEE_EMAIL"),
            password=os.getenv("SPENDEE_PASSWORD"),
            timezone=os.getenv("SPENDEE_TIMEZONE", "Europe/Moscow"),
            global_currency=os.getenv("SPENDEE_GLOBAL_CURRENCY", "EUR").upper(),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("SPENDEE_EMAIL", self.email),
                ("SPENDEE_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Unknown SPENDEE_TIMEZONE: {self.timezone}") from exc

        if len(self.global_currency) != 3 or not self.global_currency.isalpha():
            raise ConfigurationError("SPENDEE_GLOBAL_CURRENCY must be a three-letter code")

    def status(self) -> dict[str, object]:
        return {
            "configured": bool(self.email and self.password),
            "email_configured": bool(self.email),
            "password_configured": bool(self.password),
            "timezone": self.timezone,
            "global_currency": self.global_currency,
        }
