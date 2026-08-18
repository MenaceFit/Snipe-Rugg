"""Native Solana JSON-RPC WebSocket streaming provider.

Implements logsSubscribe / accountSubscribe / programSubscribe / slotSubscribe /
signatureSubscribe over a single multiplexed connection, with automatic reconnect
(exponential backoff) and resubscription of every logical subscription after a
reconnect (spec section 103). This is the low-level engine HeliusWebSocketProvider
also reuses, since Helius Enhanced WebSockets speak the same subscription protocol.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import random
import uuid
from dataclasses import dataclass
from typing import Any

import orjson
import websockets
from websockets.exceptions import ConnectionClosed

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import RawStreamMessage
from snipe_rugg.providers.base import (
    ConnectionState,
    EventHandler,
    ReconnectListener,
    StreamingProvider,
)
from snipe_rugg.providers.errors import ProviderNotConnected, RpcError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SubscriptionSpec:
    kind: str
    method: str
    params: list[Any]
    unsubscribe_method: str


class SolanaWebSocketProvider(StreamingProvider):
    def __init__(
        self,
        ws_url: str,
        *,
        name: str = "solana_ws",
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
        request_timeout: float = 15.0,
    ) -> None:
        self.name = name
        self._url = ws_url
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._request_timeout = request_timeout

        self._ws: Any = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._run_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._stopped.set()

        self._logical_subs: dict[str, _SubscriptionSpec] = {}
        self._sub_id_by_key: dict[str, int] = {}
        self._key_by_sub_id: dict[int, str] = {}
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._pending_subscribe_keys: dict[int, str] = {}
        self._id_seq = itertools.count(1)

        self._on_event: EventHandler | None = None
        self._reconnect_listeners: list[ReconnectListener] = []
        self._last_seen_slot: int | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def last_seen_slot(self) -> int | None:
        return self._last_seen_slot

    def set_event_handler(self, handler: EventHandler) -> None:
        self._on_event = handler

    def add_reconnect_listener(self, listener: ReconnectListener) -> None:
        self._reconnect_listeners.append(listener)

    async def start(self) -> None:
        if self._on_event is None:
            raise RuntimeError(f"{self.name}: call set_event_handler() before start()")
        if not self._stopped.is_set():
            return
        self._stopped.clear()
        self._run_task = asyncio.create_task(self._run_forever(), name=f"{self.name}-ws-loop")

    async def stop(self) -> None:
        self._stopped.set()
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._state = ConnectionState.DISCONNECTED
        self._fail_pending(ProviderNotConnected(self.name))

    async def _run_forever(self) -> None:
        backoff = self._initial_backoff
        first_connect = True
        while not self._stopped.is_set():
            try:
                self._state = ConnectionState.CONNECTING
                async with websockets.connect(
                    self._url, ping_interval=self._ping_interval, ping_timeout=self._ping_timeout
                ) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    backoff = self._initial_backoff
                    logger.info("ws_connected", extra={"fields": {"provider": self.name}})
                    # The read loop must already be pulling messages off the socket before
                    # we send anything that awaits a response (resubscribe requests below):
                    # responses are only ever resolved by _handle_response, which only runs
                    # inside _read_loop. Running them sequentially — resubscribe, then start
                    # reading — would leave every resubscribe request awaiting a response
                    # nothing is there yet to deliver, stalling on each one's timeout.
                    read_task = asyncio.create_task(self._read_loop(ws), name=f"{self.name}-read-loop")
                    try:
                        # Always (re)send every logical subscription on connect, not just on
                        # reconnects: a subscribe_*() call made between start() and the first
                        # successful handshake only registers into _logical_subs (see
                        # _register_subscription) and relies on this to actually go out.
                        await self._resubscribe_all()
                        if not first_connect:
                            for listener in list(self._reconnect_listeners):
                                with contextlib.suppress(Exception):
                                    await listener(self)
                        first_connect = False
                        await read_task
                    finally:
                        if not read_task.done():
                            read_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await read_task
            except asyncio.CancelledError:
                raise
            except (TimeoutError, ConnectionClosed, OSError) as exc:
                logger.warning("ws_disconnected", extra={"fields": {"provider": self.name, "error": str(exc)}})
            except Exception:
                logger.exception("ws_loop_error", extra={"fields": {"provider": self.name}})

            self._ws = None
            self._fail_pending(ProviderNotConnected(self.name))
            if self._stopped.is_set():
                break
            self._state = ConnectionState.DISCONNECTED
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))
            backoff = min(backoff * 2, self._max_backoff)

        self._state = ConnectionState.DISCONNECTED

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def _read_loop(self, ws: Any) -> None:
        async for raw in ws:
            received_at = utc_now()
            try:
                message = orjson.loads(raw)
            except orjson.JSONDecodeError:
                logger.warning("ws_message_parse_error", extra={"fields": {"provider": self.name}})
                continue
            if "method" in message:
                await self._handle_notification(message, received_at)
            elif "id" in message:
                self._handle_response(message)

    def _handle_response(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        fut = self._pending.pop(request_id, None)
        key = self._pending_subscribe_keys.pop(request_id, None)
        if "error" in message:
            if fut is not None and not fut.done():
                fut.set_exception(RpcError(message["error"]))
            return
        result = message.get("result")
        # Register the subscription-id mapping here, in the read loop, rather than in
        # the caller after it resumes: that caller runs in a different task, and a
        # notification for this subscription can arrive (and be read) before that task
        # is rescheduled — dropping the first notification. Doing it here guarantees
        # the mapping exists before the read loop moves on to the next message.
        if key is not None and isinstance(result, int):
            self._sub_id_by_key[key] = result
            self._key_by_sub_id[result] = key
        if fut is not None and not fut.done():
            fut.set_result(result)

    async def _handle_notification(self, message: dict[str, Any], received_at: Any) -> None:
        params = message.get("params") or {}
        sub_id = params.get("subscription")
        if not isinstance(sub_id, int):
            return
        key = self._key_by_sub_id.get(sub_id)
        if key is None:
            return
        result = params.get("result", {})
        slot = None
        if isinstance(result, dict):
            context = result.get("context")
            if isinstance(context, dict):
                slot = context.get("slot")
            elif "slot" in result:
                slot = result.get("slot")
        if slot is not None:
            self._last_seen_slot = slot
        raw_message = RawStreamMessage(
            provider=self.name,
            method=message["method"],
            subscription_key=key,
            payload=result,
            slot=slot,
            received_at=received_at,
        )
        assert self._on_event is not None
        try:
            await self._on_event(raw_message)
        except Exception:
            logger.exception("ws_event_handler_error", extra={"fields": {"provider": self.name}})

    async def _send_request(self, method: str, params: list[Any], *, register_key: str | None = None) -> Any:
        if self._ws is None or self._state != ConnectionState.CONNECTED:
            raise ProviderNotConnected(self.name)
        request_id = next(self._id_seq)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        if register_key is not None:
            self._pending_subscribe_keys[request_id] = register_key
        payload = orjson.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            await self._ws.send(payload)
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        finally:
            self._pending.pop(request_id, None)
            self._pending_subscribe_keys.pop(request_id, None)

    async def _register_subscription(
        self, key: str | None, kind: str, method: str, params: list[Any], unsubscribe_method: str
    ) -> str:
        key = key or f"{kind}:{uuid.uuid4().hex}"
        self._logical_subs[key] = _SubscriptionSpec(kind, method, params, unsubscribe_method)
        if self._state == ConnectionState.CONNECTED:
            await self._send_request(method, params, register_key=key)
        return key

    async def subscribe_logs(
        self, *, mentions: list[str] | None = None, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        if mentions and len(mentions) > 1:
            raise ValueError(
                "logsSubscribe's mentions filter is only reliable with exactly one address; "
                "call subscribe_logs() once per address instead"
            )
        filter_: Any = {"mentions": mentions} if mentions else "all"
        params = [filter_, {"commitment": commitment}]
        return await self._register_subscription(key, "logs", "logsSubscribe", params, "logsUnsubscribe")

    async def subscribe_account(
        self, pubkey: str, *, commitment: str = "finalized", encoding: str = "jsonParsed", key: str | None = None
    ) -> str:
        params = [pubkey, {"commitment": commitment, "encoding": encoding}]
        return await self._register_subscription(key, "account", "accountSubscribe", params, "accountUnsubscribe")

    async def subscribe_program(
        self,
        program_id: str,
        *,
        commitment: str = "finalized",
        encoding: str = "base64",
        filters: list[dict[str, Any]] | None = None,
        key: str | None = None,
    ) -> str:
        options: dict[str, Any] = {"commitment": commitment, "encoding": encoding}
        if filters:
            options["filters"] = filters
        params = [program_id, options]
        return await self._register_subscription(key, "program", "programSubscribe", params, "programUnsubscribe")

    async def subscribe_slot(self, *, key: str | None = None) -> str:
        return await self._register_subscription(key, "slot", "slotSubscribe", [], "slotUnsubscribe")

    async def subscribe_signature(
        self, signature: str, *, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        params = [signature, {"commitment": commitment}]
        return await self._register_subscription(
            key, "signature", "signatureSubscribe", params, "signatureUnsubscribe"
        )

    async def unsubscribe(self, key: str) -> None:
        spec = self._logical_subs.pop(key, None)
        sub_id = self._sub_id_by_key.pop(key, None)
        if sub_id is not None:
            self._key_by_sub_id.pop(sub_id, None)
        if spec is not None and sub_id is not None and self._state == ConnectionState.CONNECTED:
            with contextlib.suppress(ProviderNotConnected, RpcError, asyncio.TimeoutError):
                await self._send_request(spec.unsubscribe_method, [sub_id])

    async def _resubscribe_all(self) -> None:
        self._sub_id_by_key.clear()
        self._key_by_sub_id.clear()
        for key, spec in list(self._logical_subs.items()):
            try:
                await self._send_request(spec.method, spec.params, register_key=key)
            except Exception:
                logger.exception("resubscribe_failed", extra={"fields": {"provider": self.name, "key": key}})
