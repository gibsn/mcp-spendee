from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from requests import Session
from spendee import Spendee, SpendeeFirestoreError
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

    def list_labels(self) -> list[dict[str, str]]:
        return self._call("list_labels")

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
            "foreign_rate",
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
        labels: list[str] | None = None,
        occurred_at: str | None = None,
        currency: str | None = None,
        exchange_rate: float | None = None,
        confirm: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be positive; transaction_type determines its sign")

        wallet = self._resolve_wallet(wallet_id)
        wallet_currency = str(wallet.get("currency") or "").strip().upper()
        if not wallet_currency:
            raise SpendeeClientError("Selected Spendee wallet has no currency")
        transaction_currency = (currency or wallet_currency).strip().upper()
        if not transaction_currency:
            raise ValueError("currency must not be empty")

        try:
            input_amount = Decimal(str(amount))
        except InvalidOperation as exc:
            raise ValueError("amount must be a finite decimal number") from exc
        if not input_amount.is_finite():
            raise ValueError("amount must be a finite decimal number")
        signed_input_amount = -input_amount if transaction_type == "expense" else input_amount
        is_foreign = transaction_currency != wallet_currency
        resolved_exchange_rate: Decimal | None = None
        foreign_amount: Decimal | None = None
        if is_foreign:
            if exchange_rate is None:
                if confirm:
                    raise ValueError(
                        "exchange_rate from the preview is required when "
                        "confirming a foreign-currency transaction"
                    )
                rate_value = self._call(
                    "get_currency_exchange_rate",
                    transaction_currency,
                    wallet_currency,
                )
            else:
                rate_value = exchange_rate
            try:
                resolved_exchange_rate = Decimal(str(rate_value))
            except InvalidOperation as exc:
                raise ValueError("exchange_rate must be a finite decimal number") from exc
            if not resolved_exchange_rate.is_finite() or resolved_exchange_rate <= 0:
                raise ValueError("exchange_rate must be positive")
            foreign_amount = signed_input_amount
            signed_amount = signed_input_amount * resolved_exchange_rate
        else:
            if exchange_rate is not None:
                raise ValueError(
                    "exchange_rate is only valid when currency differs from the wallet currency"
                )
            signed_amount = signed_input_amount

        start_date = self._parse_datetime(occurred_at)
        normalized_labels: list[str] = []
        seen_labels: set[str] = set()
        for raw_label in labels or []:
            label = raw_label.strip()
            if not label:
                raise ValueError("labels must not contain empty names")
            folded = label.casefold()
            if folded not in seen_labels:
                normalized_labels.append(label)
                seen_labels.add(folded)
        preview = {
            "wallet_id": wallet_id,
            "category_id": category_id,
            "amount": float(signed_amount),
            "currency": wallet_currency,
            "transaction_type": transaction_type,
            "note": note,
            "labels": normalized_labels,
            "occurred_at": start_date.isoformat(timespec="seconds"),
        }
        if is_foreign:
            assert foreign_amount is not None
            assert resolved_exchange_rate is not None
            preview.update(
                {
                    "foreign_amount": float(foreign_amount),
                    "foreign_currency": transaction_currency,
                    "foreign_rate": float(resolved_exchange_rate),
                }
            )

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
                stored = self._write_results[normalized_request_id]
                if normalized_labels and not stored.get("labels_applied", False):
                    self._retry_labels(
                        result=stored,
                        labels=normalized_labels,
                    )
                return {
                    **stored,
                    "deduplicated": True,
                }

            try:
                timezone = ZoneInfo(self._settings.timezone)
                aware_start_date = start_date.replace(tzinfo=timezone)
                offset = aware_start_date.utcoffset()
                response = self._get_api().create_firestore_transaction(
                    legacy_wallet_id=wallet_id,
                    legacy_category_id=category_id,
                    amount=signed_amount,
                    note=note,
                    made_at=aware_start_date,
                    timezone_name=self._settings.timezone,
                    timezone_offset_seconds=(
                        int(offset.total_seconds()) if offset is not None else 0
                    ),
                    labels=normalized_labels or None,
                    foreign_currency=transaction_currency if is_foreign else None,
                    foreign_amount=(
                        format(foreign_amount, "f") if foreign_amount is not None else None
                    ),
                    foreign_rate=(
                        format(resolved_exchange_rate, "f")
                        if resolved_exchange_rate is not None
                        else None
                    ),
                )
            except SpendeeFirestoreError as exc:
                if not normalized_labels or not isinstance(exc.response, dict):
                    raise SpendeeClientError(f"Spendee API request failed: {exc}") from exc
                response = exc.response
                result = {
                    "status": "created_labels_failed",
                    "created": True,
                    "deduplicated": False,
                    "request_id": normalized_request_id,
                    "transaction": preview,
                    "spendee_response": response,
                    "labels_applied": False,
                    "label_error": str(exc),
                }
                self._write_results[normalized_request_id] = result
                return result
            except SpendeeError as exc:
                raise SpendeeClientError(f"Spendee API request failed: {exc}") from exc
            result = {
                "status": "created",
                "created": True,
                "deduplicated": False,
                "request_id": normalized_request_id,
                "transaction": preview,
                "spendee_response": response,
                "labels_applied": True,
            }
            if normalized_labels and isinstance(response, dict):
                result["label_result"] = response.get("firestore_labels")
            self._write_results[normalized_request_id] = result
            return result

    def _resolve_wallet(self, wallet_id: int) -> dict[str, Any]:
        matches = [
            wallet for wallet in self._call("wallet_get_all") if wallet.get("id") == wallet_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one Spendee wallet with id {wallet_id}, found {len(matches)}"
            )
        return matches[0]

    def _retry_labels(
        self,
        *,
        result: dict[str, Any],
        labels: list[str],
    ) -> None:
        response = result.get("spendee_response")
        transaction_uuid = self._find_transaction_uuid(response)
        firestore_wallet_id = (
            response.get("firestore_wallet_id") if isinstance(response, dict) else None
        )
        if transaction_uuid is None:
            result.update(
                {
                    "status": "created_labels_failed",
                    "labels_applied": False,
                    "label_error": (
                        "Transaction was created, but the Firestore response "
                        "did not contain its UUID"
                    ),
                }
            )
            return
        if not isinstance(firestore_wallet_id, str) or not firestore_wallet_id:
            result.update(
                {
                    "status": "created_labels_failed",
                    "labels_applied": False,
                    "label_error": (
                        "Transaction was created, but the Firestore wallet ID "
                        "was missing from the response"
                    ),
                }
            )
            return

        try:
            label_result = self._call(
                "set_transaction_labels",
                firestore_wallet_id,
                transaction_uuid,
                labels,
            )
        except (SpendeeClientError, ValueError) as exc:
            result.update(
                {
                    "status": "created_labels_failed",
                    "labels_applied": False,
                    "label_error": f"Transaction was created, but labels failed: {exc}",
                }
            )
            return

        result.update(
            {
                "status": "created",
                "labels_applied": True,
                "label_result": label_result,
            }
        )
        result.pop("label_error", None)

    @staticmethod
    def _find_transaction_uuid(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        for key in ("uuid", "transaction_uuid"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for key in ("transaction", "result", "data"):
            candidate = SpendeeGateway._find_transaction_uuid(value.get(key))
            if candidate:
                return candidate
        return None

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
