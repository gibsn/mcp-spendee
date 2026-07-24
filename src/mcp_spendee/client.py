from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from typing import Any, Literal
from zoneinfo import ZoneInfo

from requests import Session
from spendee import Spendee
from spendee.exceptions import SpendeeError

from mcp_spendee.config import Settings

TransactionType = Literal["expense", "income"]


class SpendeeClientError(RuntimeError):
    """A safe, user-facing error returned by the Spendee adapter."""


class _ConfiguredSpendee(Spendee):
    """Spendee client with configurable login metadata instead of library defaults."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        timezone: str,
        global_currency: str,
    ) -> None:
        super().__init__(email, password)
        self._configured_timezone = timezone
        self._configured_global_currency = global_currency

    def user_login(
        self,
        version: str = "v3",
        url: str = "auth/login",
        **kwargs: Any,
    ) -> None:
        refresh_token = self._get_refresh_token(self._email, self._password)
        self._access_token = self._get_access_token(refresh_token)
        kwargs["json"] = {
            "global_currency": self._configured_global_currency,
            "default_wallet_name": "Cash Wallet",
            "timezone": self._configured_timezone,
            "platform": "web",
            "version": "master",
            "credential": None,
        }
        result = Session.post(self, url=url, version=version, **kwargs)
        self._device_uuid = result["device_uuid"]


def _pick(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: item.get(field) for field in fields}


class SpendeeGateway:
    """Thread-safe facade over the archived third-party Spendee client."""

    def __init__(
        self,
        settings: Settings,
        api_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._api_factory = api_factory
        self._api: Any | None = None
        self._lock = threading.RLock()
        self._write_results: dict[str, dict[str, Any]] = {}

    def _get_api(self) -> Any:
        if self._api is None:
            self._settings.validate()
            if self._api_factory is not None:
                self._api = self._api_factory()
            else:
                assert self._settings.email is not None
                assert self._settings.password is not None
                self._api = _ConfiguredSpendee(
                    self._settings.email,
                    self._settings.password,
                    timezone=self._settings.timezone,
                    global_currency=self._settings.global_currency,
                )
        return self._api

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            try:
                return getattr(self._get_api(), method)(*args, **kwargs)
            except SpendeeError as exc:
                raise SpendeeClientError(f"Spendee API request failed: {exc}") from exc
            except (KeyError, TypeError, ValueError) as exc:
                raise SpendeeClientError(f"Unexpected Spendee API response: {exc}") from exc

    def list_wallets(self) -> list[dict[str, Any]]:
        wallets = self._call("wallet_get_all")
        return [
            _pick(
                wallet,
                (
                    "id",
                    "name",
                    "balance",
                    "currency",
                    "type",
                    "status",
                    "is_my",
                ),
            )
            for wallet in wallets
        ]

    def list_categories(
        self,
        *,
        wallet_id: int | None = None,
        category_type: TransactionType | None = None,
    ) -> list[dict[str, Any]]:
        categories = self._call("get_all_user_categories")
        result: list[dict[str, Any]] = []
        for category in categories:
            if category_type is not None and category.get("type") != category_type:
                continue
            wallet_settings = category.get("wallets_settings") or []
            if wallet_id is not None and not any(
                setting.get("wallet_id") == wallet_id and setting.get("visible", 1)
                for setting in wallet_settings
            ):
                continue
            selected = _pick(
                category,
                ("id", "name", "type", "color", "status", "image_id"),
            )
            selected["wallet_ids"] = [
                setting.get("wallet_id") for setting in wallet_settings if setting.get("visible", 1)
            ]
            result.append(selected)
        return result

    def list_transactions(
        self,
        *,
        wallet_id: int | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        transactions = self._call("wallet_get_transactions", offset=offset, limit=limit)
        if isinstance(transactions, dict):
            transactions = transactions.get("transactions", [])

        fields = (
            "id",
            "uuid",
            "wallet_id",
            "category_id",
            "amount",
            "start_date",
            "note",
            "hashtags",
            "foreign_currency",
            "foreign_amount",
            "type",
            "status",
        )
        return [
            _pick(transaction, fields)
            for transaction in transactions
            if wallet_id is None or transaction.get("wallet_id") == wallet_id
        ]

    def create_transaction(
        self,
        *,
        wallet_id: int,
        category_id: int,
        amount: float,
        transaction_type: TransactionType,
        note: str | None = None,
        occurred_at: str | None = None,
        confirm: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be positive; transaction_type determines its sign")

        signed_amount = -amount if transaction_type == "expense" else amount
        start_date = self._parse_datetime(occurred_at)
        preview = {
            "wallet_id": wallet_id,
            "category_id": category_id,
            "amount": signed_amount,
            "transaction_type": transaction_type,
            "note": note,
            "occurred_at": start_date.isoformat(timespec="seconds"),
        }

        if not confirm:
            return {
                "status": "preview",
                "created": False,
                "transaction": preview,
                "next_step": "Repeat with confirm=true and a unique request_id to create it.",
            }

        normalized_request_id = (request_id or "").strip()
        if not normalized_request_id:
            raise ValueError("request_id is required when confirm=true")

        with self._lock:
            if normalized_request_id in self._write_results:
                return {
                    **self._write_results[normalized_request_id],
                    "deduplicated": True,
                }

            response = self._call(
                "create_transaction",
                wallet_id=wallet_id,
                category_id=category_id,
                amount=signed_amount,
                note=note,
                start_date=start_date,
            )
            result = {
                "status": "created",
                "created": True,
                "deduplicated": False,
                "request_id": normalized_request_id,
                "transaction": preview,
                "spendee_response": response,
            }
            self._write_results[normalized_request_id] = result
            return result

    def _parse_datetime(self, value: str | None) -> dt.datetime:
        timezone = ZoneInfo(self._settings.timezone)
        if value is None:
            return dt.datetime.now(timezone).replace(tzinfo=None)

        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO 8601 date-time") from exc

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone).replace(tzinfo=None)
        return parsed
