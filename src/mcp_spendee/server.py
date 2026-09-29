from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from mcp_spendee.client import (
    ResourceId,
    SpendeeGateway,
    TransactionType,
)
from mcp_spendee.config import Settings
from mcp_spendee.diagnostics import CallTrace, configure_logging, current_trace, event, span

mcp = FastMCP(
    "Spendee",
    instructions=(
        "Read personal finance data from Spendee. Always preview create_transaction "
        "before confirming it, and use a unique request_id for each intended write."
    ),
)

_settings = Settings.from_env()
_gateway = SpendeeGateway(_settings)


async def _run_tool(function: Callable[..., Any], **kwargs: Any) -> Any:
    trace = CallTrace()
    token = current_trace.set(trace)
    started = time.monotonic()
    try:
        try:
            request_id = str(mcp.get_context().request_id)
        except (LookupError, ValueError):
            request_id = None
        # MCP request IDs are protocol correlation IDs, never create request_id values.
        event(
            "tool_start",
            tool=function.__name__,
            mcp_request_id=request_id,
            confirm=kwargs.get("confirm") if function.__name__ == "create_transaction" else None,
        )

        def worker() -> Any:
            event("worker_start", queue_ms=round((time.monotonic() - started) * 1000))
            try:
                with span("worker", tool=function.__name__):
                    result = function(**kwargs)
                    if isinstance(result, dict) and result.get("status") in {
                        "preview",
                        "created",
                        "existing",
                        "created_labels_failed",
                    }:
                        event(
                            "transaction_result",
                            status=result["status"],
                            after_cancel=trace.cancelled.is_set(),
                        )
                    return result
            finally:
                event("worker_finished", after_cancel=trace.cancelled.is_set())

        try:
            result = await anyio.to_thread.run_sync(worker, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            trace.cancelled.set()
            event(
                "tool_cancelled",
                elapsed_ms=round((time.monotonic() - started) * 1000),
                worker_may_be_running=True,
            )
            raise
        except Exception as exc:
            event(
                "tool_error",
                elapsed_ms=round((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__,
            )
            raise
        event(
            "tool_end",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            rows=len(result) if isinstance(result, list) else None,
        )
        return result
    finally:
        current_trace.reset(token)


@mcp.tool()
def spendee_status() -> dict[str, object]:
    """Check whether credentials and local Spendee settings are configured."""
    return _settings.status()


@mcp.tool()
async def list_wallets() -> list[dict[str, Any]]:
    """List Spendee wallets with Firestore-compatible IDs and currencies."""
    return await _run_tool(_gateway.list_wallets)


@mcp.tool()
async def list_labels() -> list[dict[str, str]]:
    """List modern Spendee labels from Firestore."""
    return await _run_tool(_gateway.list_labels)


@mcp.tool()
async def list_categories(
    wallet_id: ResourceId | None = None,
    category_type: TransactionType | None = None,
) -> list[dict[str, Any]]:
    """List Spendee categories, optionally filtered by wallet and expense/income type."""
    return await _run_tool(
        _gateway.list_categories,
        wallet_id=wallet_id,
        category_type=category_type,
    )


@mcp.tool()
async def list_transactions(
    wallet_id: ResourceId | None = None,
    offset: int = 0,
    limit: int | None = 100,
    include_labels: bool = False,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """List transactions newest first, filtered in Firestore before fetching.

    date_from/date_to accept YYYY-MM-DD (inclusive UTC calendar dates) or
    ISO timestamps with an explicit offset (inclusive start, exclusive end).
    There is no implicit Moscow timezone for these filters. For duplicate
    prechecks use source dates with a one-day margin on each side, a wallet_id,
    include_labels=false and limit=null to fetch the complete date range.
    limit=null requires both bounds. Otherwise limit (1..1000, default 100)
    and offset apply per wallet; without wallet_id each wallet is queried.
    """
    return await _run_tool(
        _gateway.list_transactions,
        wallet_id=wallet_id,
        offset=offset,
        limit=limit,
        include_labels=include_labels,
        date_from=date_from,
        date_to=date_to,
    )


@mcp.tool()
async def create_transaction(
    wallet_id: ResourceId,
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

    Amount must always be positive. transaction_type controls whether Spendee
    receives a negative expense or positive income. currency defaults to the
    selected wallet's currency. For a different currency, preview without an
    exchange_rate first, then confirm with the returned foreign_rate as
    exchange_rate. First call with confirm=false; create only after checking
    the preview, then pass confirm=true and a unique request_id. Exact content
    is deduplicated against Firestore unless allow_duplicate=true; use that
    override only after the user confirms two genuinely separate identical
    operations.
    """
    return await _run_tool(
        _gateway.create_transaction,
        wallet_id=wallet_id,
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


def main() -> None:
    """Run the MCP server over stdio."""
    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
