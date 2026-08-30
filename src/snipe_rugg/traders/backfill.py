"""Bounded-window historical backfill for the top-trader / insider feature.

Reuses decoder/transaction_decoder.py and decoder/classifier.py exactly as
they already exist — no new decoding logic. The only new idea is *who* to
classify a transaction for: everywhere else in this codebase, that's a
wallet the user is already tracking (`classify(tx, tracked_address)`); here
it's the transaction's own signer, since the whole point is discovering
wallets nobody added yet by watching a *token* instead of a *wallet*.

Bounded, not "all time": per the scope agreed for this feature, this
fetches at most `limit` recent signatures per address (not a full historical
backfill) via `getSignaturesForAddress`, then decodes each transaction
concurrently (bounded by the RPC client's own concurrency semaphore — see
providers/rpc_http.py). A wallet's true all-time history is not computed
anywhere in this codebase; every report built from this data says "over the
last N observed transactions," never "all-time."
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from snipe_rugg.decoder.classifier import classify
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.providers.base import TransactionProvider

logger = logging.getLogger(__name__)

DEFAULT_MINT_BACKFILL_LIMIT = 300
DEFAULT_WALLET_FUNDER_BACKFILL_LIMIT = 50


async def _fetch_transactions(rpc: TransactionProvider, signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ok_signatures = [entry["signature"] for entry in signatures if entry.get("err") is None and entry.get("signature")]

    async def _fetch(signature: str) -> dict[str, Any] | None:
        try:
            return await rpc.get_transaction(signature)
        except Exception:
            logger.exception("backfill_get_transaction_failed", extra={"fields": {"signature": signature}})
            return None

    results = await asyncio.gather(*(_fetch(sig) for sig in ok_signatures))
    return [raw for raw in results if raw is not None]


async def backfill_mint_trades(
    rpc: TransactionProvider, mint: str, *, limit: int = DEFAULT_MINT_BACKFILL_LIMIT
) -> list[NormalizedTrade]:
    """Every BUY/SELL trade touching `mint` within the last `limit` signatures
    observed for its own mint account, attributed to each transaction's own
    signer — this is what makes "top traders of a token nobody explicitly
    tracks" possible at all."""
    signatures = await rpc.get_signatures_for_address(mint, limit=limit)
    raw_transactions = await _fetch_transactions(rpc, signatures)

    decoder = TransactionDecoder()
    trades: list[NormalizedTrade] = []
    for raw in raw_transactions:
        decoded = decoder.decode(raw)
        if decoded.signer is None:
            continue
        for event in classify(decoded, decoded.signer):
            if isinstance(event, NormalizedTrade) and mint in (event.token_in, event.token_out):
                trades.append(event)
    return trades


async def backfill_wallet_funders(
    rpc: TransactionProvider, wallet: str, *, limit: int = DEFAULT_WALLET_FUNDER_BACKFILL_LIMIT
) -> set[str]:
    """Addresses observed sending SOL to `wallet` within its last `limit`
    signatures — the "who funded this wallet" signal traders/insiders.py
    correlates against a token's creator and against other top traders."""
    signatures = await rpc.get_signatures_for_address(wallet, limit=limit)
    raw_transactions = await _fetch_transactions(rpc, signatures)

    decoder = TransactionDecoder()
    funders: set[str] = set()
    for raw in raw_transactions:
        decoded = decoder.decode(raw)
        for event in classify(decoded, wallet):
            if not isinstance(event, NormalizedActivity) or event.event_type is not EventType.TRANSFER:
                continue
            if event.mint == "SOL" and event.amount is not None and event.amount > 0 and event.counterparty:
                funders.add(event.counterparty)
    return funders
