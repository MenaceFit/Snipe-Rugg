from __future__ import annotations

import httpx

from snipe_rugg.discovery.dexscreener import DexScreenerClient
from snipe_rugg.discovery.trending import find_trending_solana_pairs


def _pair(*, chain="solana", liquidity_usd=10_000.0, volume_h24=1_000.0, symbol="TOK") -> dict:
    return {
        "chainId": chain,
        "baseToken": {"symbol": symbol},
        "liquidity": {"usd": liquidity_usd},
        "volume": {"h24": volume_h24},
    }


def _client_returning(pairs: list[dict]) -> DexScreenerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": pairs})

    transport = httpx.MockTransport(handler)
    return DexScreenerClient(client=httpx.AsyncClient(base_url="https://example.invalid", transport=transport))


async def test_sorts_by_24h_volume_descending():
    client = _client_returning(
        [_pair(symbol="LOW", volume_h24=100.0), _pair(symbol="HIGH", volume_h24=9000.0), _pair(symbol="MID", volume_h24=500.0)]
    )
    result = await find_trending_solana_pairs(client)
    assert [p.base_token.symbol for p in result] == ["HIGH", "MID", "LOW"]


async def test_filters_out_non_solana_chains():
    client = _client_returning([_pair(chain="ethereum", symbol="ETH_TOK"), _pair(chain="solana", symbol="SOL_TOK")])
    result = await find_trending_solana_pairs(client)
    assert [p.base_token.symbol for p in result] == ["SOL_TOK"]


async def test_filters_out_pairs_below_the_liquidity_floor():
    client = _client_returning([_pair(symbol="DUST", liquidity_usd=10.0), _pair(symbol="REAL", liquidity_usd=50_000.0)])
    result = await find_trending_solana_pairs(client, min_liquidity_usd=1_000.0)
    assert [p.base_token.symbol for p in result] == ["REAL"]


async def test_respects_the_limit():
    client = _client_returning([_pair(symbol=f"T{i}", volume_h24=float(i)) for i in range(20)])
    result = await find_trending_solana_pairs(client, limit=5)
    assert len(result) == 5
    assert result[0].base_token.symbol == "T19"  # highest volume first
