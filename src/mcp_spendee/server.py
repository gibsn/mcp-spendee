from __future__ import annotations

from functools import partial
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from mcp_spendee.client import (
    ResourceId,
    SpendeeGateway,
    TransactionType,
    WalletSelectionReason,
)
from mcp_spendee.config import Settings

mcp = FastMCP(
    "Spendee",
    instructions=(
        "Read personal finance data from Spendee. Always preview create_transaction "
        "before confirming it, and use a unique request_id for each intended write."
    ),
)

_settings = Settings.from_env()
_gateway = SpendeeGateway(_settings)


@mcp.tool()
def spendee_status() -> dict[str, object]:
    """Check whether credentials and local Spendee settings are configured."""
    return _settings.status()


@mcp.tool()
async def list_wallets() -> list[dict[str, Any]]:
    """List Spendee wallets with Firestore-compatible IDs and currencies."""
    return await anyio.to_thread.run_sync(_gateway.list_wallets)


@mcp.tool()
async def list_labels() -> list[dict[str, str]]:
    """List modern Spendee labels from Firestore."""
    return await anyio.to_thread.run_sync(_gateway.list_labels)


@mcp.tool()
async def list_categories(
    wallet_id: ResourceId | None = None,
    category_type: TransactionType | None = None,
) -> list[dict[str, Any]]:
    """List Spendee categories, optionally filtered by wallet and expense/income type."""
    return await anyio.to_thread.run_sync(
        partial(
            _gateway.list_categories,
            wallet_id=wallet_id,
            category_type=category_type,
        )
    )


@mcp.tool()
async def list_transactions(
    wallet_id: ResourceId | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List transactions, optionally filtered by wallet."""
    return await anyio.to_thread.run_sync(
        partial(
            _gateway.list_transactions,
            wallet_id=wallet_id,
            offset=offset,
            limit=limit,
        )
    )


@mcp.tool()
async def create_transaction(
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
    """Preview or create a transaction.

    Amount must always be positive. wallet_selection_reason records why the
    wallet was selected: explicit_in_request for a wallet named by the user,
    travel_rule for the configured travel exception, ordinary_default when
    no wallet was specified for an expense, income_rule for the configured
    income destination, or currency_date_rule for a configured currency/date
    exception. ordinary_default is accepted only for Операционка,
    travel_rule and income_rule only for Общий, and currency_date_rule only for
    the configured currency/date wallet. transaction_type controls whether Spendee
    receives a negative expense or positive income. currency defaults to the
    selected wallet's currency. For a different currency, preview without an
    exchange_rate first, then confirm with the returned foreign_rate as
    exchange_rate. First call with confirm=false; create only after checking
    the preview, then pass confirm=true and a unique request_id. Exact content
    is deduplicated against Firestore unless allow_duplicate=true; use that
    override only after the user confirms two genuinely separate identical
    operations.
    """
    return await anyio.to_thread.run_sync(
        partial(
            _gateway.create_transaction,
            wallet_id=wallet_id,
            wallet_selection_reason=wallet_selection_reason,
            category_id=category_id,
            amount=amount,
            transaction_type=transaction_type,
            note=note,
            labels=labels,
            occurred_at=occurred_at,
            currency=currency,
            exchange_rate=exchange_rate,
            allow_duplicate=allow_duplicate,
            confirm=confirm,
            request_id=request_id,
        )
    )


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
