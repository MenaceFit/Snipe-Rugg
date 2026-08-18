from __future__ import annotations

import httpx
import orjson
import pytest

from snipe_rugg.providers.errors import RpcError
from snipe_rugg.providers.rpc_http import SolanaRpcHttpClient


def _client_with_transport(handler) -> SolanaRpcHttpClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return SolanaRpcHttpClient("https://example.invalid", client=http_client, initial_backoff=0.01, max_backoff=0.02)


async def test_successful_call_returns_result():
    def handler(request: httpx.Request) -> httpx.Response:
        body = orjson.loads(request.content)
        assert body["method"] == "getSlot"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": 12345})

    client = _client_with_transport(handler)
    assert await client.get_slot() == 12345


async def test_rpc_error_raises_rpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        body = orjson.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32602, "message": "invalid params"}}
        )

    client = _client_with_transport(handler)
    with pytest.raises(RpcError):
        await client.get_slot()


async def test_retries_on_429_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        body = orjson.loads(request.content)
        if calls["count"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": 999})

    client = _client_with_transport(handler)
    assert await client.get_slot() == 999
    assert calls["count"] == 3


async def test_get_signatures_for_address_passes_until_and_limit():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = orjson.loads(request.content)
        seen_params.update(body["params"][1])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": []})

    client = _client_with_transport(handler)
    await client.get_signatures_for_address("Addr111", until="sig-abc", limit=50)
    assert seen_params == {"limit": 50, "until": "sig-abc"}
