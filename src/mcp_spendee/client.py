from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from spendee import Spendee, SpendeeFirestoreError
from spendee.exceptions import SpendeeError

from mcp_spendee.config import Settings

TransactionType = Literal["expense", "income"]
ResourceId = int | str
WalletSelectionReason = Literal[
    "explicit_in_request",
    "travel_rule",
    "ordinary_default",
    "income_rule",
    "currency_date_rule",
]


class SpendeeClientError(RuntimeError):
    """A safe, user-facing error returned by the Spendee adapter."""


class _ConfiguredSpendee(Spendee):
    """Spendee client authenticated directly with Firebase for Firestore access."""

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
        # The legacy api.spendee.com login endpoint is no longer needed for the
        # Firestore backend and may hang. Keep this hook because the upstream
        # Firestore client calls user_login when it needs a fresh Firebase token.
        self._access_token = None
        self._device_uuid = None
        refresh_token = self._get_refresh_token(self._email, self._password)
        self._access_token = self._get_access_token(refresh_token)

    def list_firestore_transactions(
        self,
        wallet_id: ResourceId | None = None,
    ) -> list[dict[str, Any]]:
        """Return current transactions from Firestore for modern UUID wallets."""

        wallets = self.list_firestore_wallets()
        if wallet_id is not None:
            wallets = [
                wallet
                for wallet in wallets
                if wallet.get("id") == wallet_id or wallet.get("legacy_id") == wallet_id
            ]
        categories = self.list_firestore_categories()
        category_ids = {
            str(category["id"]): _public_id(category)
            for category in categories
            if category.get("id") and _public_id(category) is not None
        }
        transactions: list[dict[str, Any]] = []
        for wallet in wallets:
            firestore_wallet_id = wallet.get("id")
            public_wallet_id = _public_id(wallet)
            if not isinstance(firestore_wallet_id, str) or public_wallet_id is None:
                continue
            path = f"users/{self.firestore_user_id}/wallets/{firestore_wallet_id}/transactions"
            for transaction in self._firestore_collection(path):
                custom_currency = transaction.get("customCurrencyValue") or {}
                amount = Decimal(str(transaction.get("amount", "0")))
                transaction_id = transaction["_id"]
                transactions.append(
                    {
                        "id": transaction_id,
                        "uuid": transaction_id,
                        "wallet_id": public_wallet_id,
                        "category_id": category_ids.get(
                            str(transaction.get("category")),
                            transaction.get("category"),
                        ),
                        "amount": float(amount),
                        "start_date": transaction.get("madeAt"),
                        "note": transaction.get("note"),
                        "labels": self.get_transaction_labels(
                            firestore_wallet_id,
                            transaction_id,
                        ),
                        "hashtags": [],
                        "foreign_currency": custom_currency.get("currency"),
                        "foreign_amount": (
                            float(Decimal(str(custom_currency["amount"])))
                            if custom_currency.get("amount") is not None
                            else None
                        ),
                        "foreign_rate": (
                            float(Decimal(str(custom_currency["exchangeRate"])))
                            if custom_currency.get("exchangeRate") is not None
                            else None
                        ),
                        "type": "expense" if amount < 0 else "income",
                        "status": transaction.get("status") or "active",
                        "firestore_wallet_id": firestore_wallet_id,
                    }
                )
        return transactions


def _pick(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: item.get(field) for field in fields}


def _public_id(item: dict[str, Any]) -> ResourceId | None:
    legacy_id = item.get("legacy_id")
    if isinstance(legacy_id, int) and not isinstance(legacy_id, bool):
        return legacy_id
    firestore_id = item.get("id")
    if isinstance(firestore_id, str) and firestore_id:
        return firestore_id
    return None


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
            api = self._get_api()
            for attempt in range(2):
                try:
                    return getattr(api, method)(*args, **kwargs)
                except SpendeeError as exc:
                    response = getattr(exc, "response", None)
                    if response is None:
                        response = getattr(exc.__cause__, "response", None)
                    status_code = getattr(response, "status_code", None)
                    if attempt == 0 and status_code in {401, 403}:
                        try:
                            # The archived client adds its current bearer token to the
                            # Firebase password-login request. If that token has expired,
                            # Firebase rejects the login itself before checking the
                            # credentials, so discard all session authentication state.
                            api._access_token = None
                            api._device_uuid = None
                            api.user_login()
                        except SpendeeError as login_exc:
                            raise SpendeeClientError(
                                f"Spendee authentication failed: {login_exc}"
                            ) from login_exc
                        continue
                    raise SpendeeClientError(f"Spendee API request failed: {exc}") from exc
                except (KeyError, TypeError, ValueError) as exc:
                    raise SpendeeClientError(f"Unexpected Spendee API response: {exc}") from exc

            raise AssertionError("Spendee API retry loop exited unexpectedly")

    def list_wallets(self) -> list[dict[str, Any]]:
        wallets = self._call("list_firestore_wallets")
        return [
            {
                "id": public_id,
                "name": wallet.get("name"),
                "balance": None,
                "currency": wallet.get("currency"),
                "type": wallet.get("type"),
                "status": wallet.get("status"),
                "is_my": None,
            }
            for wallet in wallets
            if (public_id := _public_id(wallet)) is not None
        ]

    def list_labels(self) -> list[dict[str, str]]:
        return self._call("list_labels")

    def list_categories(
        self,
        *,
        wallet_id: ResourceId | None = None,
        category_type: TransactionType | None = None,
    ) -> list[dict[str, Any]]:
        categories = self._call("list_firestore_categories")
        available_wallet_ids = [wallet["id"] for wallet in self.list_wallets()]
        if wallet_id is not None:
            if wallet_id not in available_wallet_ids:
                return []
            available_wallet_ids = [wallet_id]
        result: list[dict[str, Any]] = []
        for category in categories:
            public_id = _public_id(category)
            if public_id is None:
                continue
            if category_type is not None and category.get("type") != category_type:
                continue
            selected = {
                "id": public_id,
                "name": category.get("name"),
                "type": category.get("type"),
                "color": None,
                "status": category.get("state"),
                "image_id": None,
                "wallet_ids": available_wallet_ids,
            }
            result.append(selected)
        return result

    def list_transactions(
        self,
        *,
        wallet_id: ResourceId | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        transactions = self._call("list_firestore_transactions", wallet_id)

        fields = (
            "id",
            "uuid",
            "wallet_id",
            "category_id",
            "amount",
            "start_date",
            "note",
            "labels",
            "hashtags",
            "foreign_currency",
            "foreign_amount",
            "foreign_rate",
            "type",
            "status",
            "firestore_wallet_id",
        )
        return [_pick(transaction, fields) for transaction in transactions[offset : offset + limit]]

    def create_transaction(
        self,
        *,
        wallet_id: ResourceId,
        wallet_selection_reason: WalletSelectionReason,
        category_id: ResourceId,
        amount: float,
        transaction_type: TransactionType,
        note: str | None = None,
        labels: list[str] | None = None,
        occurred_at: str | None = None,
        currency: str | None = None,
        exchange_rate: float | None = None,
        allow_duplicate: bool = False,
        confirm: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be positive; transaction_type determines its sign")

        wallet = self._resolve_wallet(wallet_id)
        wallet_name = str(wallet.get("name") or "").strip()
        wallet_currency = str(wallet.get("currency") or "").strip().upper()
        if not wallet_currency:
            raise SpendeeClientError("Selected Spendee wallet has no currency")
        transaction_currency = (currency or wallet_currency).strip().upper()
        if not transaction_currency:
            raise ValueError("currency must not be empty")
        start_date = self._parse_datetime(occurred_at)
        self._validate_wallet_selection(
            wallet_name,
            wallet_selection_reason,
            transaction_type=transaction_type,
            transaction_currency=transaction_currency,
            occurred_at=start_date,
        )

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
            "wallet_name": wallet_name,
            "wallet_selection_reason": wallet_selection_reason,
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

            existing = None if allow_duplicate else self._find_exact_transaction(preview)
            if existing is not None:
                labels_applied = True
                label_result = None
                if normalized_labels and set(existing.get("labels") or []) != set(
                    normalized_labels
                ):
                    label_result = self._call(
                        "set_transaction_labels",
                        existing["firestore_wallet_id"],
                        existing["uuid"],
                        normalized_labels,
                    )
                result = {
                    "status": "existing",
                    "created": False,
                    "deduplicated": True,
                    "request_id": normalized_request_id,
                    "transaction": preview,
                    "spendee_response": {
                        "uuid": existing["uuid"],
                        "firestore_wallet_id": existing["firestore_wallet_id"],
                    },
                    "labels_applied": labels_applied,
                }
                if label_result is not None:
                    result["label_result"] = label_result
                self._write_results[normalized_request_id] = result
                return result

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

    def _find_exact_transaction(self, preview: dict[str, Any]) -> dict[str, Any] | None:
        wallet_aliases = self._resource_aliases(self._call("list_firestore_wallets"))
        category_aliases = self._resource_aliases(self._call("list_firestore_categories"))
        preview_signature = self._transaction_signature(
            preview,
            wallet_aliases=wallet_aliases,
            category_aliases=category_aliases,
        )
        transactions = self._call("list_firestore_transactions", preview["wallet_id"])
        for transaction in transactions:
            try:
                matches = (
                    self._transaction_signature(
                        transaction,
                        wallet_aliases=wallet_aliases,
                        category_aliases=category_aliases,
                    )
                    == preview_signature
                )
            except (InvalidOperation, TypeError, ValueError):
                continue
            if matches:
                return transaction
        return None

    def _transaction_signature(
        self,
        transaction: dict[str, Any],
        *,
        wallet_aliases: dict[str, str],
        category_aliases: dict[str, str],
    ) -> tuple[Any, ...]:
        return (
            wallet_aliases.get(
                str(transaction.get("wallet_id")),
                str(transaction.get("wallet_id")),
            ),
            category_aliases.get(
                str(transaction.get("category_id")),
                str(transaction.get("category_id")),
            ),
            self._minor_unit_decimal(transaction.get("amount")),
            transaction.get("type") or transaction.get("transaction_type"),
            transaction.get("note") or "",
            self._canonical_datetime(
                transaction.get("start_date") or transaction.get("occurred_at")
            ),
            transaction.get("foreign_currency"),
            self._optional_decimal(transaction.get("foreign_amount")),
            self._optional_decimal(transaction.get("foreign_rate")),
        )

    @staticmethod
    def _resource_aliases(resources: list[dict[str, Any]]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for resource in resources:
            firestore_id = resource.get("id")
            if not isinstance(firestore_id, str) or not firestore_id:
                continue
            aliases[firestore_id] = firestore_id
            legacy_id = resource.get("legacy_id")
            if isinstance(legacy_id, int) and not isinstance(legacy_id, bool):
                aliases[str(legacy_id)] = firestore_id
        return aliases

    def _canonical_datetime(self, value: Any) -> str:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        timezone = ZoneInfo(self._settings.timezone)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone).replace(tzinfo=None)
        return parsed.isoformat(timespec="seconds")

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _minor_unit_decimal(value: Any) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"))

    @staticmethod
    def _validate_wallet_selection(
        wallet_name: str,
        wallet_selection_reason: WalletSelectionReason,
        *,
        transaction_type: TransactionType,
        transaction_currency: str,
        occurred_at: dt.datetime,
    ) -> None:
        normalized_name = wallet_name.casefold()
        if wallet_selection_reason == "ordinary_default" and normalized_name != "операционка":
            raise ValueError("ordinary_default transactions must use the Операционка wallet")
        if wallet_selection_reason == "travel_rule" and normalized_name != "общий":
            raise ValueError("travel_rule transactions must use the Общий wallet")
        if wallet_selection_reason == "income_rule" and normalized_name != "общий":
            raise ValueError("income_rule transactions must use the Общий wallet")
        if wallet_selection_reason == "currency_date_rule":
            valid_date = dt.date(2026, 9, 12) <= occurred_at.date() <= dt.date(2026, 9, 27)
            if (
                normalized_name != "uk 2026"
                or transaction_type != "expense"
                or transaction_currency != "GBP"
                or not valid_date
            ):
                raise ValueError(
                    "currency_date_rule requires a GBP expense in the UK 2026 wallet "
                    "dated from 2026-09-12 through 2026-09-27"
                )

    def _resolve_wallet(self, wallet_id: ResourceId) -> dict[str, Any]:
        matches = [wallet for wallet in self.list_wallets() if wallet.get("id") == wallet_id]
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
