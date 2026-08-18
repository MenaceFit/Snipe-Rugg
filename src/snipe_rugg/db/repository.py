"""CRUD for tracked wallets/groups and event persistence — the layer Discord
commands and the wallet tracker service both go through, so neither touches a
SQLAlchemy session directly."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snipe_rugg.db.models import (
    Alert,
    Token,
    TokenTrade,
    TrackedWallet,
    WalletActivity,
    WalletGroup,
    WalletGroupMember,
    WalletStatus,
)
from snipe_rugg.decoder.models import NormalizedActivity, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent, LaunchpadStatus


class WalletAlreadyTracked(Exception):
    pass


class WalletNotFound(Exception):
    pass


class GroupAlreadyExists(Exception):
    pass


class GroupNotFound(Exception):
    pass


class WalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_wallet(self, address: str, *, name: str | None = None) -> TrackedWallet:
        if await self.get_wallet(address) is not None:
            raise WalletAlreadyTracked(address)
        wallet = TrackedWallet(address=address, name=name, status=WalletStatus.ACTIVE.value)
        self._session.add(wallet)
        await self._session.flush()
        return wallet

    async def get_wallet(self, address: str) -> TrackedWallet | None:
        result = await self._session.execute(select(TrackedWallet).where(TrackedWallet.address == address))
        return result.scalar_one_or_none()

    async def remove_wallet(self, address: str) -> None:
        wallet = await self.get_wallet(address)
        if wallet is None:
            raise WalletNotFound(address)
        await self._session.delete(wallet)
        await self._session.flush()

    async def list_wallets(self) -> list[TrackedWallet]:
        result = await self._session.execute(select(TrackedWallet).order_by(TrackedWallet.created_at))
        return list(result.scalars().all())

    async def list_active_wallets(self) -> list[TrackedWallet]:
        result = await self._session.execute(
            select(TrackedWallet).where(TrackedWallet.status == WalletStatus.ACTIVE.value)
        )
        return list(result.scalars().all())

    async def set_status(self, address: str, status: WalletStatus) -> TrackedWallet:
        wallet = await self.get_wallet(address)
        if wallet is None:
            raise WalletNotFound(address)
        wallet.status = status.value
        await self._session.flush()
        return wallet

    async def create_group(self, name: str) -> WalletGroup:
        if await self.get_group(name) is not None:
            raise GroupAlreadyExists(name)
        group = WalletGroup(name=name)
        self._session.add(group)
        await self._session.flush()
        return group

    async def get_group(self, name: str) -> WalletGroup | None:
        result = await self._session.execute(select(WalletGroup).where(WalletGroup.name == name))
        return result.scalar_one_or_none()

    async def add_wallet_to_group(self, group_name: str, address: str) -> WalletGroupMember:
        group = await self.get_group(group_name)
        if group is None:
            raise GroupNotFound(group_name)
        wallet = await self.get_wallet(address)
        if wallet is None:
            raise WalletNotFound(address)
        member = WalletGroupMember(group_id=group.id, wallet_id=wallet.id)
        self._session.add(member)
        await self._session.flush()
        return member

    async def list_group_wallets(self, group_name: str) -> list[TrackedWallet]:
        group = await self.get_group(group_name)
        if group is None:
            raise GroupNotFound(group_name)
        result = await self._session.execute(
            select(TrackedWallet)
            .join(WalletGroupMember, WalletGroupMember.wallet_id == TrackedWallet.id)
            .where(WalletGroupMember.group_id == group.id)
        )
        return list(result.scalars().all())

    async def record_trade(self, trade: NormalizedTrade) -> TokenTrade:
        row = TokenTrade(
            wallet_address=trade.wallet,
            signature=trade.signature,
            side=trade.side.value,
            token_in=trade.token_in,
            token_out=trade.token_out,
            amount_in=trade.amount_in,
            amount_out=trade.amount_out,
            program=trade.program,
            confidence=trade.confidence,
            slot=trade.slot,
            block_time=trade.block_time,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_activity(self, activity: NormalizedActivity) -> WalletActivity:
        row = WalletActivity(
            wallet_address=activity.wallet,
            signature=activity.signature,
            event_type=activity.event_type.value,
            mint=activity.mint,
            amount=activity.amount,
            counterparty=activity.counterparty,
            details=activity.details,
            slot=activity.slot,
            block_time=activity.block_time,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_alert(
        self,
        *,
        wallet_address: str,
        signature: str,
        event_type: str,
        channel_id: str | None = None,
        discord_message_id: str | None = None,
        total_latency_ms: float | None = None,
    ) -> Alert:
        row = Alert(
            wallet_address=wallet_address,
            signature=signature,
            event_type=event_type,
            channel_id=channel_id,
            discord_message_id=discord_message_id,
            total_latency_ms=total_latency_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_token(self, mint: str) -> Token | None:
        result = await self._session.execute(select(Token).where(Token.mint == mint))
        return result.scalar_one_or_none()

    async def list_ungraduated_tokens(self) -> list[Token]:
        result = await self._session.execute(select(Token).where(Token.status != LaunchpadStatus.GRADUATED.value))
        return list(result.scalars().all())

    async def record_token_launch(self, event: LaunchEvent) -> Token:
        existing = await self.get_token(event.mint)
        if existing is not None:
            return existing
        row = Token(
            mint=event.mint,
            creator_address=event.creator,
            launchpad=event.launchpad,
            pair=event.pair,
            status=LaunchpadStatus.BONDING_CURVE.value,
            first_seen_slot=event.slot,
            first_seen_at=event.block_time,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def mark_graduated(
        self, mint: str, *, slot: int, block_time: datetime | None, signature: str | None = None
    ) -> Token | None:
        token = await self.get_token(mint)
        if token is None:
            return None
        token.status = LaunchpadStatus.GRADUATED.value
        token.graduated_slot = slot
        token.graduated_at = block_time
        token.graduated_signature = signature
        await self._session.flush()
        return token
