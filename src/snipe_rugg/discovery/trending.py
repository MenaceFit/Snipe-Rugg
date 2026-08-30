"""Derives a "trending Solana pairs" view from DexScreener's search endpoint,
since DexScreener's public API has no dedicated trending-ranking endpoint —
"boosts" exist but are explicitly paid promotion (see
discovery/dexscreener.py's module docstring), not an organic ranking. This
is deliberately a derived approximation (search a broad query, keep Solana
pairs above a liquidity floor, sort by 24h volume) — not presented as an
official DexScreener ranking.
"""
from __future__ import annotations

from snipe_rugg.discovery.dexscreener import SOLANA_CHAIN_ID, DexPair, DexScreenerClient

DEFAULT_TRENDING_QUERY = "solana"
DEFAULT_MIN_LIQUIDITY_USD = 1_000.0


async def find_trending_solana_pairs(
    client: DexScreenerClient,
    *,
    query: str = DEFAULT_TRENDING_QUERY,
    min_liquidity_usd: float = DEFAULT_MIN_LIQUIDITY_USD,
    limit: int = 10,
) -> list[DexPair]:
    pairs = await client.search_pairs(query)
    solana_pairs = [
        pair
        for pair in pairs
        if pair.chain_id == SOLANA_CHAIN_ID and (pair.liquidity.usd or 0) >= min_liquidity_usd
    ]
    solana_pairs.sort(key=lambda pair: pair.volume.h24 or 0, reverse=True)
    return solana_pairs[:limit]
