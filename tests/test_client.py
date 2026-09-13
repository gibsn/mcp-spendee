from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from requests import HTTPError, Response, Session
from spendee.exceptions import SpendeeError

from mcp_spendee.client import SpendeeGateway, _ConfiguredSpendee
from mcp_spendee.config import ConfigurationError, Settings


class FakeSpendee:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.legacy_wallet_calls = 0
        self.firestore_wallet_calls = 0
        self.legacy_category_calls = 0
        self.firestore_category_calls = 0
        self.firestore_transaction_calls: list[int | str | None] = []
        self.label_updates: list[dict[str, Any]] = []

    def wallet_get_all(self) -> list[dict[str, Any]]:
        self.legacy_wallet_calls += 1
        return [
            {
                "id": 10,
                "name": "Cash",
                "balance": 42.5,
                "currency": "EUR",
                "type": "default",
                "status": "active",
                "is_my": True,
                "sharing_users": [{"email": "private@example.com"}],
            }
        ]

    def list_firestore_wallets(self) -> list[dict[str, Any]]:
        self.firestore_wallet_calls += 1
        return [
            {
                "id": "wallet-uuid",
                "legacy_id": 10,
                "name": "Cash",
                "currency": "EUR",
                "type": "default",
                "status": "active",
            },
            {
                "id": "general-wallet-uuid",
                "legacy_id": None,
                "name": "Общий",
                "currency": "RUB",
                "type": "cash",
                "status": "active",
            },
            {
                "id": "uk-wallet-uuid",
                "legacy_id": None,
                "name": "UK 2026",
                "currency": "RUB",
                "type": "cash",
                "status": "active",
            },
        ]

    def get_all_user_categories(self) -> list[dict[str, Any]]:
        self.legacy_category_calls += 1
        return [
            {
                "id": 20,
                "name": "Food",
                "type": "expense",
                "color": "#fff",
                "status": "active",
                "image_id": 1,
                "wallets_settings": [{"wallet_id": 10, "visible": 1}],
            },
            {
                "id": 21,
                "name": "Salary",
                "type": "income",
                "wallets_settings": [{"wallet_id": 11, "visible": 1}],
            },
        ]

    def list_firestore_categories(self) -> list[dict[str, Any]]:
        self.firestore_category_calls += 1
        return [
            {
                "id": "food-uuid",
                "legacy_id": 20,
                "name": "Food",
                "type": "expense",
                "state": "active",
            },
            {
                "id": "salary-uuid",
                "legacy_id": 21,
                "name": "Salary",
                "type": "income",
                "state": "active",
            },
            {
                "id": "music-category-uuid",
                "legacy_id": None,
                "name": "Музыка",
                "type": "expense",
                "state": "active",
            },
        ]

    def wallet_get_transactions(self, offset: int, limit: int) -> list[dict[str, Any]]:
        assert offset == 0
        assert limit == 100
        return [
            {
                "id": 30,
                "wallet_id": 10,
                "category_id": 20,
                "amount": -12.5,
                "foreign_rate": 0.02,
            },
            {"id": 31, "wallet_id": 11, "category_id": 21, "amount": 1000},
        ]

    def list_firestore_transactions(
        self,
        wallet_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        self.firestore_transaction_calls.append(wallet_id)
        transactions = [
            {
                "id": 30,
                "uuid": "legacy-wallet-transaction-uuid",
                "wallet_id": 10,
                "category_id": 20,
                "amount": -12.5,
                "start_date": "2026-07-21T09:00:00Z",
                "note": "Lunch",
                "labels": [],
                "foreign_currency": "THB",
                "foreign_amount": "-625",
                "foreign_rate": 0.02,
                "type": "expense",
                "status": "active",
                "firestore_wallet_id": "wallet-uuid",
            },
            {
                "id": "existing-transaction-uuid",
                "uuid": "existing-transaction-uuid",
                "wallet_id": "general-wallet-uuid",
                "category_id": "salary-uuid",
                "amount": 1000.0,
                "start_date": "2026-07-22T09:00:00Z",
                "note": "Existing income",
                "labels": [],
                "foreign_currency": None,
                "foreign_amount": None,
                "foreign_rate": None,
                "type": "income",
                "status": "active",
                "firestore_wallet_id": "general-wallet-uuid",
            },
        ]
        for index, created in enumerate(self.created):
            transactions.append(
                {
                    "id": f"created-{index}",
                    "uuid": f"created-{index}",
                    "wallet_id": created["legacy_wallet_id"],
                    "category_id": created["legacy_category_id"],
                    "amount": float(created["amount"]),
                    "start_date": created["made_at"].isoformat(),
                    "note": created.get("note"),
                    "labels": created.get("labels") or [],
                    "foreign_currency": created.get("foreign_currency"),
                    "foreign_amount": created.get("foreign_amount"),
                    "foreign_rate": created.get("foreign_rate"),
                    "type": "expense" if created["amount"] < 0 else "income",
                    "status": "active",
                    "firestore_wallet_id": "wallet-uuid",
                }
            )
        if wallet_id is None:
            return transactions
        return [item for item in transactions if item["wallet_id"] == wallet_id]

    def get_currency_exchange_rate(
        self,
        source_currency: str,
        target_currency: str,
    ) -> str:
        assert source_currency == "THB"
        assert target_currency == "EUR"
        return "0.02"

    def create_firestore_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {
            "id": 99,
            "uuid": "transaction-uuid",
            "firestore_wallet_id": "wallet-uuid",
            "firestore_labels": {
                "changed": True,
                "labels": kwargs.get("labels") or [],
                "added": kwargs.get("labels") or [],
                "removed": [],
            },
        }

    def list_labels(self) -> list[dict[str, str]]:
        return [{"id": "taxi-id", "name": "такси"}]

    def set_transaction_labels(
        self,
        firestore_wallet_id: str,
        transaction_uuid: str,
        labels: list[str],
    ) -> dict[str, Any]:
        self.label_updates.append(
            {
                "wallet_id": firestore_wallet_id,
                "transaction_id": transaction_uuid,
                "labels": labels,
            }
        )
        return {
            "changed": True,
            "labels": labels,
            "added": labels,
            "removed": [],
        }


@pytest.fixture
def fake_api() -> FakeSpendee:
    return FakeSpendee()


@pytest.fixture
def gateway(fake_api: FakeSpendee) -> SpendeeGateway:
    settings = Settings(
        email="test@example.com",
        password="secret",
        timezone="Europe/Moscow",
        global_currency="EUR",
    )
    return SpendeeGateway(settings, api_factory=lambda: fake_api)


def test_settings_status_does_not_expose_credentials() -> None:
    settings = Settings(email="test@example.com", password="secret")

    status = settings.status()

    assert status["configured"] is True
    assert "test@example.com" not in repr(status)
    assert "secret" not in repr(status)


def test_missing_credentials_fail_only_when_api_is_used() -> None:
    gateway = SpendeeGateway(Settings(email=None, password=None))

    with pytest.raises(ConfigurationError, match="SPENDEE_EMAIL, SPENDEE_PASSWORD"):
        gateway.list_wallets()


def test_login_refreshes_firebase_without_calling_legacy_spendee_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ConfiguredSpendee(
        "test@example.com",
        "secret",
        timezone="Europe/Moscow",
        global_currency="EUR",
    )
    api._access_token = "expired-token"
    api._device_uuid = "stale-device-uuid"

    def get_refresh_token(email: str, password: str) -> str:
        assert email == "test@example.com"
        assert password == "secret"
        assert api._access_token is None
        assert api._device_uuid is None
        return "fresh-refresh-token"

    monkeypatch.setattr(api, "_get_refresh_token", get_refresh_token)
    monkeypatch.setattr(api, "_get_access_token", lambda token: "fresh-access-token")
    monkeypatch.setattr(
        Session,
        "post",
        lambda self, **kwargs: pytest.fail("legacy Spendee login must not be called"),
    )

    api.user_login()

    assert api._access_token == "fresh-access-token"
    assert api._device_uuid is None


def test_list_wallets_returns_safe_subset(gateway: SpendeeGateway) -> None:
    assert gateway.list_wallets() == [
        {
            "id": 10,
            "name": "Cash",
            "balance": None,
            "currency": "EUR",
            "type": "default",
            "status": "active",
            "is_my": None,
        },
        {
            "id": "general-wallet-uuid",
            "name": "Общий",
            "balance": None,
            "currency": "RUB",
            "type": "cash",
            "status": "active",
            "is_my": None,
        },
        {
            "id": "uk-wallet-uuid",
            "name": "UK 2026",
            "balance": None,
            "currency": "RUB",
            "type": "cash",
            "status": "active",
            "is_my": None,
        },
    ]
    api = gateway._get_api()
    assert api.firestore_wallet_calls == 1
    assert api.legacy_wallet_calls == 0


def test_api_call_reauthenticates_once_after_expired_token() -> None:
    class ExpiredTokenSpendee(FakeSpendee):
        def __init__(self) -> None:
            super().__init__()
            self.login_calls = 0
            self.wallet_calls = 0
            self._access_token = "expired-token"
            self._device_uuid = "stale-device-uuid"

        def user_login(self) -> None:
            assert self._access_token is None
            assert self._device_uuid is None
            self.login_calls += 1

        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            self.wallet_calls += 1
            if self.wallet_calls == 1:
                response = Response()
                response.status_code = 401
                try:
                    raise HTTPError(response=response)
                except HTTPError as exc:
                    raise SpendeeError(
                        "Spendee returned a non-200 HTTP code.", response=response
                    ) from exc
            return super().list_firestore_wallets()

    api = ExpiredTokenSpendee()
    settings = Settings(email="test@example.com", password="secret")
    gateway = SpendeeGateway(settings, api_factory=lambda: api)

    assert gateway.list_wallets()[0]["id"] == 10
    assert api.login_calls == 1
    assert api.wallet_calls == 2


def test_api_call_does_not_retry_non_authentication_errors() -> None:
    class FailingSpendee(FakeSpendee):
        def __init__(self) -> None:
            super().__init__()
            self.login_calls = 0
            self.wallet_calls = 0

        def user_login(self) -> None:
            self.login_calls += 1

        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            self.wallet_calls += 1
            response = Response()
            response.status_code = 500
            try:
                raise HTTPError(response=response)
            except HTTPError as exc:
                raise SpendeeError(
                    "Spendee returned a non-200 HTTP code.", response=response
                ) from exc

    api = FailingSpendee()
    settings = Settings(email="test@example.com", password="secret")
    gateway = SpendeeGateway(settings, api_factory=lambda: api)

    with pytest.raises(RuntimeError, match="Spendee API request failed"):
        gateway.list_wallets()
    assert api.login_calls == 0
    assert api.wallet_calls == 1


def test_list_labels_uses_forked_spendee_client(gateway: SpendeeGateway) -> None:
    assert gateway.list_labels() == [{"id": "taxi-id", "name": "такси"}]


def test_list_categories_filters_by_wallet_and_type(gateway: SpendeeGateway) -> None:
    categories = gateway.list_categories(wallet_id=10, category_type="expense")

    assert [category["id"] for category in categories] == [20, "music-category-uuid"]
    assert categories[0]["wallet_ids"] == [10]
    api = gateway._get_api()
    assert api.firestore_category_calls == 1
    assert api.legacy_category_calls == 0


def test_create_transaction_accepts_firestore_only_ids(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": "general-wallet-uuid",
        "wallet_selection_reason": "income_rule",
        "category_id": "music-category-uuid",
        "amount": 1000,
        "transaction_type": "income",
    }

    preview = gateway.create_transaction(**arguments)
    assert preview["transaction"]["wallet_name"] == "Общий"

    gateway.create_transaction(
        **arguments,
        confirm=True,
        request_id="modern-only-ids",
    )
    assert fake_api.created[0]["legacy_wallet_id"] == "general-wallet-uuid"
    assert fake_api.created[0]["legacy_category_id"] == "music-category-uuid"


def test_list_transactions_filters_by_wallet(gateway: SpendeeGateway) -> None:
    transactions = gateway.list_transactions(wallet_id=10)

    assert [transaction["id"] for transaction in transactions] == [30]
    assert transactions[0]["foreign_rate"] == 0.02


def test_list_transactions_accepts_firestore_only_wallet_id(
    gateway: SpendeeGateway,
) -> None:
    transactions = gateway.list_transactions(wallet_id="general-wallet-uuid")

    assert [transaction["id"] for transaction in transactions] == ["existing-transaction-uuid"]


def test_create_transaction_requires_preview_then_confirmation(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 12.5,
        "transaction_type": "expense",
        "note": "Lunch",
        "labels": ["такси"],
        "occurred_at": "2026-07-23T12:00:00+03:00",
    }

    preview = gateway.create_transaction(**arguments)
    assert preview["status"] == "preview"
    assert preview["transaction"]["amount"] == -12.5
    assert fake_api.created == []

    created = gateway.create_transaction(**arguments, confirm=True, request_id="lunch-20260723")
    assert created["status"] == "created"
    assert created["labels_applied"] is True
    assert fake_api.created[0]["amount"] == -12.5
    assert fake_api.created[0]["labels"] == ["такси"]
    assert fake_api.created[0]["timezone_name"] == "Europe/Moscow"
    assert fake_api.created[0]["timezone_offset_seconds"] == 10800

    duplicate = gateway.create_transaction(**arguments, confirm=True, request_id="lunch-20260723")
    assert duplicate["deduplicated"] is True
    assert len(fake_api.created) == 1


def test_create_transaction_deduplicates_exact_content_across_request_ids(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 17.22,
        "currency": "THB",
        "exchange_rate": 0.02,
        "transaction_type": "expense",
        "note": "Uber",
        "occurred_at": "2026-09-12T12:00:00+03:00",
        "confirm": True,
    }

    first = gateway.create_transaction(**arguments, request_id="first-attempt")
    second = gateway.create_transaction(**arguments, request_id="retry-with-new-id")

    assert first["status"] == "created"
    assert second["status"] == "existing"
    assert second["deduplicated"] is True
    assert len(fake_api.created) == 1


def test_duplicate_detection_canonicalizes_category_aliases(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "amount": 12.5,
        "transaction_type": "expense",
        "note": "Lunch",
        "occurred_at": "2026-09-14T12:00:00+03:00",
        "confirm": True,
    }
    gateway.create_transaction(
        **arguments,
        category_id=20,
        request_id="legacy-category-id",
    )

    duplicate = gateway.create_transaction(
        **arguments,
        category_id="food-uuid",
        request_id="firestore-category-id",
    )

    assert duplicate["status"] == "existing"
    assert len(fake_api.created) == 1


def test_duplicate_detection_tolerates_sub_cent_wallet_rounding(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 17.22,
        "currency": "THB",
        "exchange_rate": 113.85046474816446,
        "transaction_type": "expense",
        "note": "Uber",
        "occurred_at": "2026-09-12T12:00:00+03:00",
        "confirm": True,
    }
    gateway.create_transaction(**arguments, request_id="original-write")
    fake_api.created[0]["amount"] = Decimal("-1960.50500300")

    duplicate = gateway.create_transaction(
        **arguments,
        request_id="rounded-readback-retry",
    )

    assert duplicate["status"] == "existing"
    assert len(fake_api.created) == 1


def test_duplicate_retry_repairs_labels_instead_of_creating_another_transaction(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 17.74,
        "currency": "THB",
        "exchange_rate": 0.02,
        "transaction_type": "expense",
        "note": "Uber",
        "occurred_at": "2026-09-13T10:41:41+03:00",
        "confirm": True,
    }
    gateway.create_transaction(**arguments, request_id="first-without-label")

    duplicate = gateway.create_transaction(
        **arguments,
        labels=["такси"],
        request_id="retry-with-label",
    )

    assert duplicate["status"] == "existing"
    assert len(fake_api.created) == 1
    assert fake_api.label_updates == [
        {
            "wallet_id": "wallet-uuid",
            "transaction_id": "created-0",
            "labels": ["такси"],
        }
    ]


def test_create_transaction_allows_explicit_identical_transaction(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 5,
        "transaction_type": "expense",
        "note": "Two visible identical charges",
        "occurred_at": "2026-09-13T12:00:00+03:00",
        "confirm": True,
    }
    gateway.create_transaction(**arguments, request_id="first-charge")

    second = gateway.create_transaction(
        **arguments,
        request_id="second-charge",
        allow_duplicate=True,
    )

    assert second["status"] == "created"
    assert len(fake_api.created) == 2


def test_create_transaction_preserves_foreign_amount_and_rate(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": 1200,
        "currency": "thb",
        "transaction_type": "expense",
        "note": "Ferry",
        "labels": ["такси"],
        "occurred_at": "2026-07-26T11:12:00+03:00",
    }

    preview = gateway.create_transaction(**arguments)

    assert preview["transaction"] == {
        "wallet_id": 10,
        "wallet_name": "Cash",
        "wallet_selection_reason": "explicit_in_request",
        "category_id": 20,
        "amount": -24.0,
        "currency": "EUR",
        "transaction_type": "expense",
        "note": "Ferry",
        "labels": ["такси"],
        "occurred_at": "2026-07-26T11:12:00",
        "foreign_amount": -1200.0,
        "foreign_currency": "THB",
        "foreign_rate": 0.02,
    }
    assert fake_api.created == []

    with pytest.raises(ValueError, match="exchange_rate from the preview"):
        gateway.create_transaction(
            **arguments,
            confirm=True,
            request_id="ferry-without-rate",
        )

    created = gateway.create_transaction(
        **arguments,
        exchange_rate=preview["transaction"]["foreign_rate"],
        confirm=True,
        request_id="ferry-20260726",
    )

    assert created["status"] == "created"
    assert fake_api.created[0]["amount"] == Decimal("-24")
    assert fake_api.created[0]["foreign_currency"] == "THB"
    assert fake_api.created[0]["foreign_amount"] == "-1200"
    assert fake_api.created[0]["foreign_rate"] == "0.02"


def test_create_transaction_rejects_invalid_amount(gateway: SpendeeGateway) -> None:
    with pytest.raises(ValueError, match="amount must be positive"):
        gateway.create_transaction(
            wallet_id=10,
            wallet_selection_reason="explicit_in_request",
            category_id=20,
            amount=-1,
            transaction_type="expense",
        )


def test_create_transaction_rejects_general_wallet_for_ordinary_default() -> None:
    class WalletRoutingSpendee(FakeSpendee):
        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "general-wallet-uuid",
                    "legacy_id": 7613265,
                    "name": "Общий",
                    "currency": "RUB",
                    "type": "cash",
                    "status": "active",
                }
            ]

    gateway = SpendeeGateway(
        Settings(email="test@example.com", password="secret"),
        api_factory=WalletRoutingSpendee,
    )

    with pytest.raises(ValueError, match="must use the Операционка wallet"):
        gateway.create_transaction(
            wallet_id=7613265,
            wallet_selection_reason="ordinary_default",
            category_id=20,
            amount=290,
            transaction_type="expense",
        )


def test_create_transaction_accepts_income_rule_for_general_wallet() -> None:
    class WalletRoutingSpendee(FakeSpendee):
        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "general-wallet-uuid",
                    "legacy_id": 7613265,
                    "name": "Общий",
                    "currency": "RUB",
                    "type": "cash",
                    "status": "active",
                }
            ]

    gateway = SpendeeGateway(
        Settings(email="test@example.com", password="secret"),
        api_factory=WalletRoutingSpendee,
    )

    preview = gateway.create_transaction(
        wallet_id=7613265,
        wallet_selection_reason="income_rule",
        category_id=20,
        amount=1000,
        transaction_type="income",
    )

    assert preview["transaction"]["wallet_name"] == "Общий"
    assert preview["transaction"]["wallet_selection_reason"] == "income_rule"


def test_create_transaction_rejects_operational_wallet_for_income_rule() -> None:
    class WalletRoutingSpendee(FakeSpendee):
        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "operations-wallet-uuid",
                    "legacy_id": 2899807,
                    "name": "Операционка",
                    "currency": "RUB",
                    "type": "cash",
                    "status": "active",
                }
            ]

    gateway = SpendeeGateway(
        Settings(email="test@example.com", password="secret"),
        api_factory=WalletRoutingSpendee,
    )

    with pytest.raises(ValueError, match="must use the Общий wallet"):
        gateway.create_transaction(
            wallet_id=2899807,
            wallet_selection_reason="income_rule",
            category_id=20,
            amount=1000,
            transaction_type="income",
        )


@pytest.mark.parametrize(
    ("wallet_id", "currency", "occurred_at", "transaction_type"),
    [
        (10, "GBP", "2026-09-20T12:00:00+03:00", "expense"),
        ("uk-wallet-uuid", "EUR", "2026-09-20T12:00:00+03:00", "expense"),
        ("uk-wallet-uuid", "GBP", "2026-09-11T12:00:00+03:00", "expense"),
        ("uk-wallet-uuid", "GBP", "2026-09-28T12:00:00+03:00", "expense"),
        ("uk-wallet-uuid", "GBP", "2026-09-20T12:00:00+03:00", "income"),
    ],
)
def test_currency_date_rule_rejects_transactions_outside_uk_trip(
    gateway: SpendeeGateway,
    wallet_id: int | str,
    currency: str,
    occurred_at: str,
    transaction_type: str,
) -> None:
    with pytest.raises(ValueError, match="currency_date_rule"):
        gateway.create_transaction(
            wallet_id=wallet_id,
            wallet_selection_reason="currency_date_rule",
            category_id=20,
            amount=10,
            currency=currency,
            occurred_at=occurred_at,
            transaction_type=transaction_type,
        )


def test_currency_date_rule_accepts_inclusive_uk_trip_boundaries(
    gateway: SpendeeGateway,
) -> None:
    for occurred_at in (
        "2026-09-12T00:00:00+03:00",
        "2026-09-27T23:59:59+03:00",
    ):
        preview = gateway.create_transaction(
            wallet_id="uk-wallet-uuid",
            wallet_selection_reason="currency_date_rule",
            category_id=20,
            amount=10,
            currency="GBP",
            exchange_rate=100,
            occurred_at=occurred_at,
            transaction_type="expense",
        )

        assert preview["status"] == "preview"


def test_create_transaction_preview_includes_wallet_name_and_selection_reason() -> None:
    class WalletRoutingSpendee(FakeSpendee):
        def list_firestore_wallets(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "operations-wallet-uuid",
                    "legacy_id": 2899807,
                    "name": "Операционка",
                    "currency": "RUB",
                    "type": "cash",
                    "status": "active",
                }
            ]

    gateway = SpendeeGateway(
        Settings(email="test@example.com", password="secret"),
        api_factory=WalletRoutingSpendee,
    )

    preview = gateway.create_transaction(
        wallet_id=2899807,
        wallet_selection_reason="ordinary_default",
        category_id=20,
        amount=290,
        transaction_type="expense",
    )

    assert preview["transaction"]["wallet_name"] == "Операционка"
    assert preview["transaction"]["wallet_selection_reason"] == "ordinary_default"
