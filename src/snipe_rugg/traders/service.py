"""Orchestrates the top-trader / insider feature end to end: backfill this
token's recent trades, compute per-wallet stats, resolve the token's
creator, and correlate top traders' SOL funding sources against each other
and against the creator. One TokenAnalysisReport per mint is everything the
Discord command layer (traders/service.py's callers) needs to render.

Bounded, not "all time": every RPC lookup here goes through
traders/backfill.py's already-bounded functions, and creator resolution
(when this bot never tracked the token's own launch) is itself capped —
see _find_creator_via_rpc. WINDOW_NOTE is carried on every report so nothing
downstream can present this as a genuine all-time history by omission.
"""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.launchpad.detector import detect_launch
from snipe_rugg.providers.base import TransactionProvider
from snipe_rugg.traders.backfill import (
    backfill_mint_trades,
    backfill_wallet_funders,
    fetch_transactions,
)
from snipe_rugg.traders.insiders import InsiderSignal, build_funding_graph, detect_insider_signals
from snipe_rugg.traders.stats import TraderStats, compute_trader_stats

DEFAULT_TOP_N = 10
CREATOR_LOOKUP_PAGE_SIZE = 1000
MAX_CREATOR_LOOKUP_PAGES = 3

WINDOW_NOTE = (
    "Based on the most recent observed transactions per wallet/token within this "
    "lookup's bounds — not a complete all-time history."
)


class TokenAnalysisReport(BaseModel):
    mint: str
    creator: str | None
    creator_source: str  # "tracked" | "detected" | "unknown"
    trades_analyzed: int
    wallets_analyzed: int
    top_traders: list[TraderStats]
    insider_signals: list[InsiderSignal]
    window_note: str = WINDOW_NOTE


class TraderAnalysisService:
    def __init__(
        self,
        *,
        rpc: TransactionProvider,
        session_factory: async_sessionmaker[AsyncSession],
        top_n: int = DEFAULT_TOP_N,
    ) -> None:
        self._rpc = rpc
        self._session_factory = session_factory
        self._top_n = top_n

    async def analyze_mint(self, mint: str) -> TokenAnalysisReport:
        trades = await backfill_mint_trades(self._rpc, mint)
        stats = compute_trader_stats(trades, mint)
        stats.sort(key=lambda s: s.realized_pnl_sol, reverse=True)
        top = stats[: self._top_n]

        creator, creator_source = await self._resolve_creator(mint)

        wallet_funders: dict[str, set[str]] = {}
        for trader in top:
            wallet_funders[trader.wallet] = await backfill_wallet_funders(self._rpc, trader.wallet)
        if creator is not None and creator not in wallet_funders:
            wallet_funders[creator] = await backfill_wallet_funders(self._rpc, creator)

        graph = build_funding_graph(wallet_funders)
        signals = detect_insider_signals(graph, top_traders=[trader.wallet for trader in top], creator=creator)

        return TokenAnalysisReport(
            mint=mint,
            creator=creator,
            creator_source=creator_source,
            trades_analyzed=len(trades),
            wallets_analyzed=len(stats),
            top_traders=top,
            insider_signals=signals,
        )

    async def _resolve_creator(self, mint: str) -> tuple[str | None, str]:
        async with self._session_factory() as session:
            token = await WalletRepository(session).get_token(mint)
        if token is not None:
            return token.creator_address, "tracked"

        creator = await self._find_creator_via_rpc(mint)
        return creator, "detected" if creator is not None else "unknown"

    async def _find_creator_via_rpc(self, mint: str) -> str | None:
        """Best-effort fallback for a mint this bot never tracked through its
        own launch detector: walks backwards through the mint account's
        signature history, one bounded page at a time, looking for the one
        transaction that actually created it (launchpad/detector.py's
        detect_launch). Capped at MAX_CREATOR_LOOKUP_PAGES pages so a mint
        old/active enough that its creation transaction falls outside that
        cap is reported with creator=None rather than paying for an
        unbounded historical walk — consistent with this feature's bounded-
        window scope.
        """
        decoder = TransactionDecoder()
        before: str | None = None
        for _ in range(MAX_CREATOR_LOOKUP_PAGES):
            signatures = await self._rpc.get_signatures_for_address(mint, before=before, limit=CREATOR_LOOKUP_PAGE_SIZE)
            if not signatures:
                return None

            raw_transactions = await fetch_transactions(self._rpc, signatures)
            for raw in raw_transactions:
                launch = detect_launch(decoder.decode(raw))
                if launch is not None and launch.mint == mint:
                    return launch.creator

            if len(signatures) < CREATOR_LOOKUP_PAGE_SIZE:
                return None  # reached the start of this mint's history without a match
            before = signatures[-1].get("signature")
        return None
