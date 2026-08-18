"""JSON-RPC HTTP client for Solana. Used for gap-recovery backfill and one-off
lookups — never for the hot streaming path (see solana_ws.py / helius_ws.py)."""
from __future__ import annotations

import asyncio
import itertools
import logging
import random
from typing import Any

import httpx
import orjson

from snipe_rugg.providers.base import BlockchainProvider, ConnectionState, TransactionProvider
from snipe_rugg.providers.errors import RpcError, RpcHttpError

logger = logging.getLogger(__name__)


class SolanaRpcHttpClient(TransactionProvider, BlockchainProvider):
    def __init__(
        self,
        endpoint: str,
        *,
        name: str = "solana_rpc_http",
        timeout: float = 10.0,
        max_concurrency: int = 10,
        max_retries: int = 5,
        initial_backoff: float = 0.5,
        max_backoff: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._endpoint = endpoint
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._id_seq = itertools.count(1)
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._state = ConnectionState.DISCONNECTED

    @property
    def state(self) -> ConnectionState:
        return self._state

    async def start(self) -> None:
        await self.get_health()
        self._state = ConnectionState.CONNECTED

    async def stop(self) -> None:
        await self._client.aclose()
        self._state = ConnectionState.DISCONNECTED

    async def _call(self, method: str, params: list[Any]) -> Any:
        request_id = next(self._id_seq)
        body = orjson.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        backoff = self._initial_backoff
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        self._endpoint, content=body, headers={"Content-Type": "application/json"}
                    )
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else backoff
                    last_exc = RpcHttpError(response.status_code, response.text[:500])
                    logger.warning(
                        "rpc_http_retry",
                        extra={"fields": {"method": method, "status": response.status_code, "attempt": attempt}},
                    )
                    await asyncio.sleep(wait + random.uniform(0, wait * 0.1))
                    backoff = min(backoff * 2, self._max_backoff)
                    continue
                if response.status_code != 200:
                    raise RpcHttpError(response.status_code, response.text[:500])
                payload = orjson.loads(response.content)
                if "error" in payload:
                    raise RpcError(payload["error"])
                return payload["result"]
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.1))
            backoff = min(backoff * 2, self._max_backoff)
        assert last_exc is not None
        raise last_exc

    async def get_health(self) -> str:
        return await self._call("getHealth", [])

    async def get_slot(self, *, commitment: str = "finalized") -> int:
        return await self._call("getSlot", [{"commitment": commitment}])

    async def get_signatures_for_address(
        self, address: str, *, before: str | None = None, until: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": limit}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        result = await self._call("getSignaturesForAddress", [address, opts])
        return result or []

    async def get_transaction(
        self, signature: str, *, encoding: str = "jsonParsed", max_supported_transaction_version: int = 0
    ) -> dict[str, Any] | None:
        return await self._call(
            "getTransaction",
            [signature, {"encoding": encoding, "maxSupportedTransactionVersion": max_supported_transaction_version}],
        )
