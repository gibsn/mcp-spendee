from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from requests import Response

from mcp_spendee.client import SpendeeGateway, _ConfiguredSpendee
from mcp_spendee.config import Settings


class QueryAPI(_ConfiguredSpendee):
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.queries: list[dict[str, Any]] = []

    @property
    def firestore_user_id(self) -> str:
        return "user"

    def list_firestore_wallets(self) -> list[dict[str, Any]]:
        return [{"id": "wallet", "legacy_id": 10, "currency": "EUR"}]

    def list_firestore_categories(self) -> list[dict[str, Any]]:
        return []

    def _firestore_request(self, method: str, url: str, **kwargs: Any) -> Response:
        assert method == "POST"
        assert url.endswith("/users/user/wallets/wallet:runQuery")
        self.queries.append(kwargs["json"])
        response = Response()
        response.status_code = 200
        response._content = json.dumps(self.pages.pop(0)).encode()
        return response


def document(i: int, timestamp: str = "2026-09-29T09:00:00Z") -> dict[str, Any]:
    return {
        "document": {
            "name": (
                "projects/spendee-app/databases/(default)/documents/"
                f"users/user/wallets/wallet/transactions/{i:05}"
            ),
            "fields": {"amount": {"stringValue": "-100"}, "madeAt": {"timestampValue": timestamp}},
        },
        "readTime": "2026-09-30T00:00:00Z",
    }


def test_limit_and_offset_are_sent_to_firestore() -> None:
    api = QueryAPI([[document(1)]])
    rows = api.list_firestore_transactions(10, limit=1, offset=7, include_labels=False)
    assert len(rows) == 1
    query = api.queries[0]["structuredQuery"]
    assert query["limit"] == 1
    assert query["offset"] == 7
    assert query["orderBy"][0] == {"field": {"fieldPath": "madeAt"}, "direction": "DESCENDING"}


def test_complete_date_range_reads_past_page_boundary() -> None:
    api = QueryAPI([[document(i) for i in range(300)], [document(300)]])
    gateway = SpendeeGateway(Settings("email", "password"), api_factory=lambda: api)
    rows = gateway.list_transactions(
        wallet_id=10, date_from="2026-09-28", date_to="2026-09-30", limit=None
    )
    assert len(rows) == 301
    first, second = api.queries
    filters = first["structuredQuery"]["where"]["compositeFilter"]["filters"]
    assert filters[0]["fieldFilter"]["value"] == {"timestampValue": "2026-09-28T00:00:00Z"}
    assert filters[1]["fieldFilter"]["value"] == {"timestampValue": "2026-10-01T00:00:00Z"}
    cursor = second["structuredQuery"]["startAt"]
    assert cursor["before"] is False
    assert cursor["values"][1]["referenceValue"].endswith("/00299")
    assert second["readTime"] == "2026-09-30T00:00:00Z"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": "bad"},
        {"date_from": "2026-09-29T12:00:00"},
        {"date_from": "2026-09-30", "date_to": "2026-09-29"},
        {"limit": None},
        {"limit": None, "date_from": "2026-09-29"},
    ],
)
def test_invalid_or_unbounded_query_fails_before_network(kwargs: dict[str, Any]) -> None:
    gateway = SpendeeGateway(Settings(None, None))
    with pytest.raises(ValueError):
        gateway.list_transactions(**kwargs)


def test_offset_timestamp_is_normalized_to_utc() -> None:
    api = QueryAPI([[]])
    gateway = SpendeeGateway(Settings("email", "password"), api_factory=lambda: api)
    assert (
        gateway.list_transactions(
            wallet_id=10, date_from="2026-09-29T00:15:00+14:00", date_to="2026-09-29T00:15:00-10:00"
        )
        == []
    )
    filters = api.queries[0]["structuredQuery"]["where"]["compositeFilter"]["filters"]
    assert filters[0]["fieldFilter"]["value"]["timestampValue"] == "2026-09-28T10:15:00Z"
    assert filters[1]["fieldFilter"]["value"]["timestampValue"] == "2026-09-29T10:15:00Z"


def test_query_failure_never_returns_partial_history() -> None:
    api = QueryAPI([[document(i) for i in range(300)]])
    gateway = SpendeeGateway(Settings("email", "password"), api_factory=lambda: api)
    with pytest.raises(IndexError):
        gateway.list_transactions(
            wallet_id=10, date_from="2026-09-28", date_to="2026-09-30", limit=None
        )


def test_duplicate_beyond_first_page_is_found_in_offset_timezone() -> None:
    class DuplicateAPI(QueryAPI):
        def list_firestore_categories(self) -> list[dict[str, Any]]:
            return [{"id": "food", "legacy_id": 20}]

        def create_firestore_transaction(self, **kwargs: Any) -> None:
            pytest.fail("duplicate must not be written")

    last = document(300, "2026-09-28T10:15:00Z")
    last["document"]["fields"].update(
        {"category": {"stringValue": "food"}, "note": {"stringValue": "Lunch"}}
    )
    api = DuplicateAPI([[document(i) for i in range(300)], [last]])
    gateway = SpendeeGateway(Settings("email", "password"), api_factory=lambda: api)
    result = gateway.create_transaction(
        wallet_id=10,
        category_id=20,
        amount=100,
        transaction_type="expense",
        note="Lunch",
        occurred_at="2026-09-29T00:15:00+14:00",
        confirm=True,
        request_id="existing-on-second-page",
    )
    assert result["status"] == "existing"
    assert result["spendee_response"]["uuid"] == "00300"
    filters = api.queries[0]["structuredQuery"]["where"]["compositeFilter"]["filters"]
    lower = dt.datetime.fromisoformat(filters[0]["fieldFilter"]["value"]["timestampValue"])
    upper = dt.datetime.fromisoformat(filters[1]["fieldFilter"]["value"]["timestampValue"])
    instant = dt.datetime.fromisoformat("2026-09-28T10:15:00Z")
    assert lower <= instant < upper
