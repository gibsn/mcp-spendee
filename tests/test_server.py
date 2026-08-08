from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

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
