from __future__ import annotations

import httpx
import pytest

from snipe_rugg.discovery.dexscreener import DexPair, DexScreenerClient

_REALISTIC_PAIR = {
    "chainId": "solana",
    "dexId": "raydium",
    "pairAddress": "PairAddr111111111111111111111111111111111",
    "url": "https://dexscreener.com/solana/PairAddr111111111111111111111111111111111",
    "baseToken": {"address": "MintXYZ111111111111111111111111111111111", "name": "Example", "symbol": "EX"},
    "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
    "priceNative": "0.0000123",
    "priceUsd": "0.0021",
    "liquidity": {"usd": 54321.0, "base": 1000000.0, "quote": 100.0},
    "fdv": 210000.0,
    "marketCap": 200000.0,
    "pairCreatedAt": 1755000000000,
    "volume": {"m5": 100.0, "h1": 5000.0, "h6": 20000.0, "h24": 80000.0},
    "priceChange": {"m5": 1.2, "h1": 5.0, "h6": -3.0, "h24": 42.0},
    "txns": {"m5": {"buys": 3, "sells": 1}, "h1": {"buys": 20, "sells": 8}, "h6": {"buys": 90, "sells": 40}, "h24": {"buys": 400, "sells": 150}},
}


def _client_with_transport(handler) -> DexScreenerClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://example.invalid", transport=transport)
    return DexScreenerClient(client=http_client)


async def test_search_pairs_parses_the_realistic_schema():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/latest/dex/search"
        assert request.url.params["q"] == "pepe"
        return httpx.Response(200, json={"schemaVersion": "1.0.0", "pairs": [_REALISTIC_PAIR]})

    client = _client_with_transport(handler)
    pairs = await client.search_pairs("pepe")

    assert len(pairs) == 1
    pair = pairs[0]
    assert isinstance(pair, DexPair)
    assert pair.chain_id == "solana"
    assert pair.base_token.symbol == "EX"
    assert pair.liquidity.usd == 54321.0
    assert pair.volume.h24 == 80000.0
    assert pair.txns.h24 is not None and pair.txns.h24.buys == 400


async def test_search_pairs_with_no_matches_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"schemaVersion": "1.0.0", "pairs": None})

    client = _client_with_transport(handler)
    assert await client.search_pairs("nonexistent-token-xyz") == []


async def test_unknown_fields_and_missing_fields_do_not_crash_parsing():
    minimal_pair = {"chainId": "solana", "someBrandNewField": {"nested": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": [minimal_pair]})

    client = _client_with_transport(handler)
    pairs = await client.search_pairs("x")

    assert len(pairs) == 1
    assert pairs[0].chain_id == "solana"
    assert pairs[0].volume.h24 is None
    assert pairs[0].liquidity.usd is None


async def test_get_pairs_for_tokens_joins_addresses_and_rejects_over_30():
    seen_path = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_path["path"] = request.url.path
        return httpx.Response(200, json={"pairs": [_REALISTIC_PAIR]})

    client = _client_with_transport(handler)
    pairs = await client.get_pairs_for_tokens(["MintA", "MintB"])

    assert seen_path["path"] == "/latest/dex/tokens/MintA,MintB"
    assert len(pairs) == 1

    with pytest.raises(ValueError, match="30"):
        await client.get_pairs_for_tokens([f"Mint{i}" for i in range(31)])


async def test_get_pairs_for_tokens_with_no_addresses_is_a_noop():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make an HTTP call for an empty address list")

    client = _client_with_transport(handler)
    assert await client.get_pairs_for_tokens([]) == []
