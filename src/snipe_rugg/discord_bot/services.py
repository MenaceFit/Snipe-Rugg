"""Command logic for /wallet and /watchlist (spec section 7-8), independent of
discord.py's Interaction/decorator machinery so it's testable without a live
Discord connection. bot.py's app_commands wrappers just format these results
into Discord responses.

Adding a wallet here also subscribes it on the live provider immediately
(spec section 152's success criterion: adding a wallet should start showing
its activity without any other manual step) - pausing unsubscribes, resuming
resubscribes.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.models import WalletStatus
from snipe_rugg.db.repository import (
    GroupAlreadyExists,
    GroupNotFound,
    WalletAlreadyTracked,
    WalletNotFound,
    WalletRepository,
)
from snipe_rugg.providers.base import StreamingProvider
from snipe_rugg.tracking.wallet_tracker import wallet_subscription_key


class WalletCommandService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], provider: StreamingProvider) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def add_wallet(self, address: str, name: str | None = None) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).add_wallet(address, name=name)
            except WalletAlreadyTracked:
                return f"⚠️ `{address}` is already tracked."
            await session.commit()

        await self._provider.subscribe_logs(mentions=[address], key=wallet_subscription_key(address))
        return f"✅ Wallet added\n\nName: {name or address}\nAddress: {address}\nStatus: LIVE TRACKING"

    async def remove_wallet(self, address: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).remove_wallet(address)
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked."
            await session.commit()

        await self._provider.unsubscribe(wallet_subscription_key(address))
        return f"✅ Wallet removed: `{address}`"

    async def list_wallets(self) -> str:
        async with self._session_factory() as session:
            wallets = await WalletRepository(session).list_wallets()
        if not wallets:
            return "No wallets tracked yet. Add one with `/wallet add <address>`."
        lines = [
            f"{'🟢' if w.status == WalletStatus.ACTIVE.value else '⏸️'} {w.name or w.address} — `{w.address}`"
            for w in wallets
        ]
        return "\n".join(lines)

    async def wallet_info(self, address: str) -> str:
        async with self._session_factory() as session:
            wallet = await WalletRepository(session).get_wallet(address)
        if wallet is None:
            return f"⚠️ `{address}` is not tracked."
        return (
            f"Wallet: {wallet.name or wallet.address}\n"
            f"Address: `{wallet.address}`\n"
            f"Status: {wallet.status.upper()}\n"
            f"Tracked since: {wallet.created_at.isoformat()}"
        )

    async def pause_wallet(self, address: str) -> str:
        return await self._set_status(address, WalletStatus.PAUSED)

    async def resume_wallet(self, address: str) -> str:
        return await self._set_status(address, WalletStatus.ACTIVE)

    async def _set_status(self, address: str, status: WalletStatus) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).set_status(address, status)
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked."
            await session.commit()

        if status is WalletStatus.PAUSED:
            await self._provider.unsubscribe(wallet_subscription_key(address))
        else:
            await self._provider.subscribe_logs(mentions=[address], key=wallet_subscription_key(address))
        return f"✅ `{address}` is now {status.value.upper()}"


class WatchlistCommandService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_watchlist(self, name: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).create_group(name)
            except GroupAlreadyExists:
                return f"⚠️ Watchlist `{name}` already exists."
            await session.commit()
        return f"✅ Watchlist created: `{name}`"

    async def add_wallet(self, watchlist: str, address: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).add_wallet_to_group(watchlist, address)
            except GroupNotFound:
                return f"⚠️ Watchlist `{watchlist}` does not exist. Create it first with `/watchlist add`."
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked yet — add it first with `/wallet add`."
            await session.commit()
        return f"✅ Added `{address}` to `{watchlist}`"

    async def list_watchlist(self, watchlist: str) -> str:
        async with self._session_factory() as session:
            try:
                wallets = await WalletRepository(session).list_group_wallets(watchlist)
            except GroupNotFound:
                return f"⚠️ Watchlist `{watchlist}` does not exist."
        if not wallets:
            return f"`{watchlist}` has no wallets yet."
        return "\n".join(f"{w.name or w.address} — `{w.address}`" for w in wallets)
