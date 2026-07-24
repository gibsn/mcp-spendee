from __future__ import annotations

from typing import Any

import pytest

from mcp_spendee.client import SpendeeGateway
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
            {"id": 30, "wallet_id": 10, "category_id": 20, "amount": -12.5},
            {"id": 31, "wallet_id": 11, "category_id": 21, "amount": 1000},
        ]

    def create_transaction(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {"id": 99}


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


def test_list_categories_filters_by_wallet_and_type(gateway: SpendeeGateway) -> None:
    categories = gateway.list_categories(wallet_id=10, category_type="expense")

    assert [category["id"] for category in categories] == [20]
    assert categories[0]["wallet_ids"] == [10]


def test_list_transactions_filters_by_wallet(gateway: SpendeeGateway) -> None:
    transactions = gateway.list_transactions(wallet_id=10)

    assert [transaction["id"] for transaction in transactions] == [30]


def test_create_transaction_requires_preview_then_confirmation(
    gateway: SpendeeGateway,
    fake_api: FakeSpendee,
) -> None:
    arguments = {
        "wallet_id": 10,
        "category_id": 20,
        "amount": 12.5,
        "transaction_type": "expense",
        "note": "Lunch",
        "occurred_at": "2026-07-23T12:00:00+03:00",
    }

    preview = gateway.create_transaction(**arguments)
    assert preview["status"] == "preview"
    assert preview["transaction"]["amount"] == -12.5
    assert fake_api.created == []

    created = gateway.create_transaction(**arguments, confirm=True, request_id="lunch-20260723")
    assert created["status"] == "created"
    assert fake_api.created[0]["amount"] == -12.5

    duplicate = gateway.create_transaction(**arguments, confirm=True, request_id="lunch-20260723")
    assert duplicate["deduplicated"] is True
    assert len(fake_api.created) == 1


def test_create_transaction_rejects_invalid_amount(gateway: SpendeeGateway) -> None:
    with pytest.raises(ValueError, match="amount must be positive"):
        gateway.create_transaction(
            wallet_id=10,
            category_id=20,
            amount=-1,
            transaction_type="expense",
        )
