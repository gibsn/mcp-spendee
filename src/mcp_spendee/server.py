from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_spendee.client import SpendeeGateway, TransactionType
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
def list_wallets() -> list[dict[str, Any]]:
    """List Spendee wallets with IDs, balances, and currencies."""
    return _gateway.list_wallets()


@mcp.tool()
def list_categories(
    wallet_id: int | None = None,
    category_type: TransactionType | None = None,
) -> list[dict[str, Any]]:
    """List Spendee categories, optionally filtered by wallet and expense/income type."""
    return _gateway.list_categories(wallet_id=wallet_id, category_type=category_type)


@mcp.tool()
def list_transactions(
    wallet_id: int | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List transactions, optionally filtered by wallet."""
    return _gateway.list_transactions(wallet_id=wallet_id, offset=offset, limit=limit)


@mcp.tool()
def create_transaction(
    wallet_id: int,
    category_id: int,
    amount: float,
    transaction_type: TransactionType,
    note: str | None = None,
    occurred_at: str | None = None,
    confirm: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Preview or create a transaction.

    Amount must always be positive. transaction_type controls whether Spendee
    receives a negative expense or positive income. First call with
    confirm=false; create only after checking the preview, then pass
    confirm=true and a unique request_id.
    """
    return _gateway.create_transaction(
        wallet_id=wallet_id,
        category_id=category_id,
        amount=amount,
        transaction_type=transaction_type,
        note=note,
        occurred_at=occurred_at,
        confirm=confirm,
        request_id=request_id,
    )


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
