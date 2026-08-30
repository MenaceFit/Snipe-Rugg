"""DexScreener public API client (new feature: trending-token discovery,
prerequisite for traders/*'s top-trader / insider analysis).

Verification note (spec section 101 — verify current provider docs before
implementing): `docs.dexscreener.com` and `api.dexscreener.com` are both
unreachable from this sandbox's network policy (confirmed via direct curl,
not assumed), so the field names below were cross-referenced from two
independent secondary sources — a documented open-source MCP server
(github.com/openSVM/dexscreener-mcp-server) and a maintained Go client
library's struct definitions (pkg.go.dev/github.com/roushou/dexscreener) —
rather than the live API response itself. They agree with each other and
with this project's general knowledge of the API, but this has NOT been
confirmed against a live HTTP response the way every Solana program ID in
this codebase has been. Run a real request against `api.dexscreener.com`
and diff the response against `DexPair` before relying on this in
production; see `docs/API_MATRIX.md`.

Base URL: https://api.dexscreener.com. No API key. Confirmed real endpoints
(300 req/min): `GET /latest/dex/search?q=...` and
`GET /latest/dex/tokens/{addresses}` (up to 30 comma-separated addresses).
There is no dedicated "trending" endpoint — `token-boosts/*` exists but is
explicitly paid promotion, not organic ranking; see `discovery/trending.py`
for how this project derives a trending view without one.

All pair fields are Optional with defensive parsing (`extra="ignore"`) since
this client cannot be smoke-tested against the live schema from within this
sandbox — a renamed or missing field must degrade gracefully, not crash the
bot.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"
SOLANA_CHAIN_ID = "solana"


class TokenRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    address: str | None = None
    name: str | None = None
    symbol: str | None = None


class Liquidity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    usd: float | None = None
    base: float | None = None
    quote: float | None = None


class WindowedFloats(BaseModel):
    """volume / priceChange: {m5, h1, h6, h24}."""

    model_config = ConfigDict(extra="ignore")

    m5: float | None = None
    h1: float | None = None
    h6: float | None = None
    h24: float | None = None


class TxnCounts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    buys: int | None = None
    sells: int | None = None


class WindowedTxns(BaseModel):
    model_config = ConfigDict(extra="ignore")

    m5: TxnCounts | None = None
    h1: TxnCounts | None = None
    h6: TxnCounts | None = None
    h24: TxnCounts | None = None


class DexPair(BaseModel):
    """One DexScreener pair. Every field is Optional by design — see module
    docstring on why this client can't assume the live schema matches
    exactly."""

    model_config = ConfigDict(extra="ignore")

    chain_id: str | None = Field(default=None, alias="chainId")
    dex_id: str | None = Field(default=None, alias="dexId")
    pair_address: str | None = Field(default=None, alias="pairAddress")
    url: str | None = None
    base_token: TokenRef = Field(default_factory=TokenRef, alias="baseToken")
    quote_token: TokenRef = Field(default_factory=TokenRef, alias="quoteToken")
    price_native: str | None = Field(default=None, alias="priceNative")
    price_usd: str | None = Field(default=None, alias="priceUsd")
    liquidity: Liquidity = Field(default_factory=Liquidity)
    fdv: float | None = None
    market_cap: float | None = Field(default=None, alias="marketCap")
    pair_created_at: int | None = Field(default=None, alias="pairCreatedAt")
    volume: WindowedFloats = Field(default_factory=WindowedFloats)
    price_change: WindowedFloats = Field(default_factory=WindowedFloats, alias="priceChange")
    txns: WindowedTxns = Field(default_factory=WindowedTxns)


class DexScreenerClient:
    def __init__(self, *, base_url: str = DEXSCREENER_BASE_URL, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_pairs(self, query: str) -> list[DexPair]:
        response = await self._client.get("/latest/dex/search", params={"q": query})
        response.raise_for_status()
        return self._parse_pairs(response.json())

    async def get_pairs_for_tokens(self, addresses: list[str]) -> list[DexPair]:
        if not addresses:
            return []
        if len(addresses) > 30:
            raise ValueError("DexScreener's tokens endpoint accepts at most 30 addresses per call")
        response = await self._client.get(f"/latest/dex/tokens/{','.join(addresses)}")
        response.raise_for_status()
        return self._parse_pairs(response.json())

    @staticmethod
    def _parse_pairs(payload: Any) -> list[DexPair]:
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        if not pairs:
            return []
        return [DexPair.model_validate(raw) for raw in pairs]
