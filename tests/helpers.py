"""Shared test utilities: a real in-process WebSocket server for exercising
SolanaWebSocketProvider end-to-end (including reconnect/resubscribe) without any
network dependency, plus small polling and fixture-loading helpers."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import orjson
import websockets

from snipe_rugg.core.events import RawStreamMessage
from snipe_rugg.providers.base import (
    ConnectionState,
    EventHandler,
    ReconnectListener,
    StreamingProvider,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

WsHandler = Callable[[Any], Coroutine[Any, Any, None]]


@asynccontextmanager
async def ws_server(handler: WsHandler) -> AsyncIterator[str]:
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


async def recv_json(ws: Any) -> dict[str, Any]:
    return orjson.loads(await ws.recv())


async def send_json(ws: Any, obj: dict[str, Any]) -> None:
    await ws.send(orjson.dumps(obj).decode())


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0, interval: float = 0.02) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(interval)

    await asyncio.wait_for(_poll(), timeout=timeout)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def raw_message_from_fixture(name: str, *, provider: str = "solana_ws", subscription_key: str = "test") -> RawStreamMessage:
    envelope = load_fixture(name)
    result = envelope["params"]["result"]
    context = result.get("context") if isinstance(result, dict) else None
    slot = context.get("slot") if isinstance(context, dict) else result.get("slot") if isinstance(result, dict) else None
    return RawStreamMessage(
        provider=provider,
        method=envelope["method"],
        subscription_key=subscription_key,
        payload=result,
        slot=slot,
    )


class FakeStreamingProvider(StreamingProvider):
    """A minimal in-memory double for StreamingProvider, for testing orchestration
    logic (ProviderManager) in isolation from real WebSocket connections."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self._state = ConnectionState.DISCONNECTED
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._on_event: EventHandler | None = None
        self._reconnect_listeners: list[ReconnectListener] = []
        self.fail_next_subscribe = False

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def last_seen_slot(self) -> int | None:
        return None

    def set_event_handler(self, handler: EventHandler) -> None:
        self._on_event = handler

    def add_reconnect_listener(self, listener: ReconnectListener) -> None:
        self._reconnect_listeners.append(listener)

    async def start(self) -> None:
        self._state = ConnectionState.CONNECTED

    async def stop(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    async def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))
        if self.fail_next_subscribe:
            self.fail_next_subscribe = False
            raise RuntimeError("simulated subscribe failure")

    async def subscribe_logs(
        self, *, mentions: list[str] | None = None, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        await self._record("subscribe_logs", mentions=mentions, commitment=commitment, key=key)
        return key or "logs:x"

    async def subscribe_account(
        self, pubkey: str, *, commitment: str = "finalized", encoding: str = "jsonParsed", key: str | None = None
    ) -> str:
        await self._record("subscribe_account", pubkey, commitment=commitment, encoding=encoding, key=key)
        return key or f"account:{pubkey}"

    async def subscribe_program(
        self,
        program_id: str,
        *,
        commitment: str = "finalized",
        encoding: str = "base64",
        filters: list[dict[str, Any]] | None = None,
        key: str | None = None,
    ) -> str:
        await self._record(
            "subscribe_program", program_id, commitment=commitment, encoding=encoding, filters=filters, key=key
        )
        return key or f"program:{program_id}"

    async def subscribe_slot(self, *, key: str | None = None) -> str:
        await self._record("subscribe_slot", key=key)
        return key or "slot:all"

    async def subscribe_signature(
        self, signature: str, *, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        await self._record("subscribe_signature", signature, commitment=commitment, key=key)
        return key or f"signature:{signature}"

    async def unsubscribe(self, key: str) -> None:
        await self._record("unsubscribe", key)
