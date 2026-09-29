from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import pytest
from requests import Response
from requests.adapters import HTTPAdapter

from mcp_spendee import server
from mcp_spendee.client import SpendeeGateway, _ConfiguredSpendee
from mcp_spendee.config import Settings
from mcp_spendee.diagnostics import CallTrace, check_cancelled, current_trace


def records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.message) for r in caplog.records if r.name == "mcp_spendee"]


def test_network_logs_are_redacted_and_auth_has_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_spendee")
    timeouts = []

    def send(self: HTTPAdapter, request: Any, **kwargs: Any) -> Response:
        timeouts.append(kwargs["timeout"])
        response = Response()
        response.status_code = 200
        response._content = b'{"refreshToken": "refresh-secret", "access_token": "access-secret"}'
        return response

    monkeypatch.setattr(HTTPAdapter, "send", send)
    api = _ConfiguredSpendee(
        "private@example.com", "password-secret", timezone="Europe/Moscow", global_currency="RUB"
    )
    api.user_login()
    assert timeouts == [(10, 30), (10, 30)]
    assert [r["status"] for r in records(caplog) if r["event"] == "http_response"] == [200, 200]
    for secret in (
        "private@example.com",
        "password-secret",
        "refresh-secret",
        "access-secret",
        api._google_client_id,
    ):
        assert secret not in caplog.text


def test_failed_network_logs_exception_type_without_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_spendee")

    def fail(self: HTTPAdapter, request: Any, **kwargs: Any) -> Response:
        raise RuntimeError("secret body and URL")

    monkeypatch.setattr(HTTPAdapter, "send", fail)
    api = _ConfiguredSpendee("e", "p", timezone="Europe/Moscow", global_currency="RUB")
    with pytest.raises(RuntimeError):
        api.user_login()
    assert "secret body" not in caplog.text
    assert any(
        r["event"] == "http_error" and r["error_type"] == "RuntimeError" for r in records(caplog)
    )


def test_lock_wait_duration_is_measured(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mcp_spendee")
    gateway = SpendeeGateway(Settings(None, None))
    entered = threading.Event()
    done = threading.Event()

    def wait() -> None:
        entered.set()
        with gateway._locked("test"):
            done.set()

    with gateway._lock:
        thread = threading.Thread(target=wait)
        thread.start()
        assert entered.wait(1)
        assert not done.wait(0.03)
    thread.join(1)
    assert done.is_set()
    assert any(r["event"] == "lock_acquired" and r["wait_ms"] >= 20 for r in records(caplog))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_cancellation_is_correlated_with_background_completion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_spendee")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow() -> None:
        started.set()
        try:
            release.wait(1)
            check_cancelled()
        finally:
            finished.set()

    task = asyncio.create_task(server._run_tool(slow))
    while not started.is_set() and not task.done():
        await asyncio.sleep(0.001)
    assert started.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    while not finished.is_set():
        await asyncio.sleep(0.001)
    # Worker logs its completion after the function's finally block.
    for _ in range(100):
        if any(r["event"] == "worker_finished" for r in records(caplog)):
            break
        await asyncio.sleep(0.001)
    events = records(caplog)
    assert any(r["event"] == "tool_cancelled" for r in events)
    assert any(r["event"] == "worker_finished" and r["after_cancel"] for r in events)
    assert len({r["call_id"] for r in events}) == 1


def test_cancelled_call_stops_before_next_network_request(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _ConfiguredSpendee("e", "p", timezone="Europe/Moscow", global_currency="RUB")
    trace = CallTrace()
    trace.cancelled.set()
    token = current_trace.set(trace)
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            api.user_login()
    finally:
        current_trace.reset(token)
