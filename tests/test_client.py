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

    def wallet_get_all(self) -> list[dict[str, Any]]:
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

    def get_all_user_categories(self) -> list[dict[str, Any]]:
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


def test_login_discards_expired_session_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda self, **kwargs: {"device_uuid": "fresh-device-uuid"},
    )

    api.user_login()

    assert api._access_token == "fresh-access-token"
    assert api._device_uuid == "fresh-device-uuid"


def test_list_wallets_returns_safe_subset(gateway: SpendeeGateway) -> None:
    assert gateway.list_wallets() == [
        {
            "id": 10,
            "name": "Cash",
            "balance": 42.5,
            "currency": "EUR",
            "type": "default",
            "status": "active",
            "is_my": True,
        }
    ]


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

        def wallet_get_all(self) -> list[dict[str, Any]]:
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
            return super().wallet_get_all()

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

        def wallet_get_all(self) -> list[dict[str, Any]]:
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

    assert [category["id"] for category in categories] == [20]
    assert categories[0]["wallet_ids"] == [10]


def test_list_transactions_filters_by_wallet(gateway: SpendeeGateway) -> None:
    transactions = gateway.list_transactions(wallet_id=10)

    assert [transaction["id"] for transaction in transactions] == [30]
    assert transactions[0]["foreign_rate"] == 0.02


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
        def wallet_get_all(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 7613265,
                    "name": "Общий",
                    "balance": 0,
                    "currency": "RUB",
                    "type": "default",
                    "status": "active",
                    "is_my": True,
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


def test_create_transaction_preview_includes_wallet_name_and_selection_reason() -> None:
    class WalletRoutingSpendee(FakeSpendee):
        def wallet_get_all(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 2899807,
                    "name": "Операционка",
                    "balance": 0,
                    "currency": "RUB",
                    "type": "default",
                    "status": "active",
                    "is_my": True,
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
