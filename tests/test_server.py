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
        "list_categories",
        "list_transactions",
        "create_transaction",
    }


@pytest.mark.anyio
async def test_status_tool_does_not_require_credentials(client_session: ClientSession) -> None:
    result = await client_session.call_tool("spendee_status", {})

    assert result.isError is False
    assert result.structuredContent is not None
    assert "password" not in str(result.structuredContent).lower().replace(
        "password_configured",
        "",
    )
