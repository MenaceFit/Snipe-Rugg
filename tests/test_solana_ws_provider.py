"""End-to-end tests for SolanaWebSocketProvider against a real (local, in-process)
WebSocket server — including a genuine disconnect/reconnect/resubscribe cycle, not a
mock of the provider's internals."""
from __future__ import annotations

import asyncio

from snipe_rugg.providers.base import ConnectionState
from snipe_rugg.providers.solana_ws import SolanaWebSocketProvider
from tests.helpers import recv_json, send_json, wait_until, ws_server


async def test_subscribe_logs_and_receive_notification():
    received = []
    done = asyncio.Event()

    async def on_event(msg):
        received.append(msg)

    async def handler(ws):
        req = await recv_json(ws)
        assert req["method"] == "logsSubscribe"
        assert req["params"][0] == {"mentions": ["Wallet111"]}
        assert req["params"][1] == {"commitment": "confirmed"}
        await send_json(ws, {"jsonrpc": "2.0", "id": req["id"], "result": 42})
        await send_json(
            ws,
            {
                "jsonrpc": "2.0",
                "method": "logsNotification",
                "params": {
                    "subscription": 42,
                    "result": {
                        "context": {"slot": 100},
                        "value": {"signature": "sig1", "err": None, "logs": ["l1"]},
                    },
                },
            },
        )
        await done.wait()

    async with ws_server(handler) as url:
        provider = SolanaWebSocketProvider(url)
        provider.set_event_handler(on_event)
        await provider.start()
        try:
            key = await provider.subscribe_logs(mentions=["Wallet111"])
            await wait_until(lambda: len(received) == 1)
            assert received[0].method == "logsNotification"
            assert received[0].payload["value"]["signature"] == "sig1"
            assert received[0].subscription_key == key
            assert provider.last_seen_slot == 100
        finally:
            done.set()
            await provider.stop()


async def test_rejects_multi_address_mentions():
    provider = SolanaWebSocketProvider("ws://unused.invalid")
    provider.set_event_handler(lambda msg: None)
    try:
        await provider.subscribe_logs(mentions=["A", "B"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def test_reconnect_resubscribes_and_keeps_delivering():
    connection_count = 0
    second_subscribe_seen = asyncio.Event()
    received = []
    reconnect_calls = []

    async def on_event(msg):
        received.append(msg)

    async def on_reconnect(p):
        reconnect_calls.append(p)

    async def handler(ws):
        nonlocal connection_count
        connection_count += 1
        req = await recv_json(ws)
        assert req["method"] == "slotSubscribe"
        await send_json(ws, {"jsonrpc": "2.0", "id": req["id"], "result": connection_count})
        if connection_count == 1:
            await ws.close()  # simulate a clean drop right after the subscribe ack
            return
        second_subscribe_seen.set()
        await send_json(
            ws,
            {
                "jsonrpc": "2.0",
                "method": "slotNotification",
                "params": {"subscription": connection_count, "result": {"parent": 1, "root": 1, "slot": 555}},
            },
        )
        await asyncio.sleep(2)

    async with ws_server(handler) as url:
        provider = SolanaWebSocketProvider(url, initial_backoff=0.05, max_backoff=0.1)
        provider.set_event_handler(on_event)
        provider.add_reconnect_listener(on_reconnect)
        await provider.start()
        try:
            await provider.subscribe_slot()
            await asyncio.wait_for(second_subscribe_seen.wait(), timeout=5)
            assert connection_count == 2
            # The server setting second_subscribe_seen and the client running its
            # reconnect listener are two independent continuations of the same
            # underlying ack, with no ordering guarantee between them - poll rather
            # than assert immediately.
            await wait_until(lambda: len(reconnect_calls) == 1, timeout=3)
            await wait_until(lambda: len(received) == 1, timeout=3)
            assert received[0].payload["slot"] == 555
        finally:
            await provider.stop()


async def test_unsubscribe_sends_unsubscribe_request_and_stops_local_routing():
    unsubscribe_seen = asyncio.Event()
    done = asyncio.Event()

    async def handler(ws):
        req = await recv_json(ws)
        assert req["method"] == "slotSubscribe"
        await send_json(ws, {"jsonrpc": "2.0", "id": req["id"], "result": 7})
        req2 = await recv_json(ws)
        assert req2["method"] == "slotUnsubscribe"
        assert req2["params"] == [7]
        await send_json(ws, {"jsonrpc": "2.0", "id": req2["id"], "result": True})
        unsubscribe_seen.set()
        await done.wait()

    async with ws_server(handler) as url:
        provider = SolanaWebSocketProvider(url)
        provider.set_event_handler(lambda msg: None)
        await provider.start()
        try:
            # Wait for a real connection first: subscribing and unsubscribing before
            # one exists is a legitimate case too (see the ProviderManager fan-out and
            # the "not yet connected" path in _register_subscription), but it cancels
            # out locally without ever touching the wire — not what this test exercises.
            await wait_until(lambda: provider.state == ConnectionState.CONNECTED)
            key = await provider.subscribe_slot()
            await provider.unsubscribe(key)
            await asyncio.wait_for(unsubscribe_seen.wait(), timeout=3)
        finally:
            done.set()
            await provider.stop()
