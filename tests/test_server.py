from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_spendee import server
from mcp_spendee.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client_session() -> AsyncGenerator[ClientSession]:
    async with create_connected_server_and_client_session(
        mcp,
        raise_exceptions=True,
    ) as session:
        yield session


@pytest.mark.anyio
async def test_server_exposes_expected_tools(client_session: ClientSession) -> None:
    result = await client_session.list_tools()

    assert {tool.name for tool in result.tools} == {
        "spendee_status",
        "list_wallets",
        "list_labels",
        "list_categories",
        "list_transactions",
        "create_transaction",
    }
    create_tool = next(tool for tool in result.tools if tool.name == "create_transaction")
    assert "wallet_selection_reason" in create_tool.inputSchema["required"]
    assert create_tool.inputSchema["properties"]["wallet_selection_reason"]["enum"] == [
        "explicit_in_request",
        "travel_rule",
        "ordinary_default",
        "income_rule",
        "currency_date_rule",
    ]


@pytest.mark.anyio
async def test_status_tool_does_not_require_credentials(client_session: ClientSession) -> None:
    result = await client_session.call_tool("spendee_status", {})

    assert result.isError is False
    assert result.structuredContent is not None
    assert "password" not in str(result.structuredContent).lower().replace(
        "password_configured",
        "",
    )


@pytest.mark.anyio
async def test_slow_transaction_read_does_not_block_tools_list(
    client_session: ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowGateway:
        def list_transactions(self, **_: object) -> list[dict[str, object]]:
            started.set()
            release.wait(timeout=0.5)
            return []

    monkeypatch.setattr(server, "_gateway", SlowGateway())
    started_at = time.monotonic()
    slow_call = asyncio.create_task(client_session.call_tool("list_transactions", {}))
    await asyncio.sleep(0)

    tools = await client_session.list_tools()
    elapsed = time.monotonic() - started_at
    release.set()
    await slow_call

    assert started.is_set()
    assert {tool.name for tool in tools.tools}
    assert elapsed < 0.2
