"""CRUD for tracked wallets/groups and event persistence — the layer Discord
commands and the wallet tracker service both go through, so neither touches a
SQLAlchemy session directly."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.models import (
    Alert,
    DevRiskSignal,
    Token,
    TokenTrade,
    TrackedWallet,
    WalletActivity,
    WalletGroup,
    WalletGroupMember,
    WalletSource,
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

    async def get_or_create_dev_wallet(self, address: str) -> tuple[TrackedWallet, bool]:
        """Idempotent auto-tracking for a launch's creator address (spec section
        20-37): unlike add_wallet, never raises on an existing row — a dev may
        already be manually tracked, in which case their existing preferences are
        left untouched. A freshly auto-discovered dev defaults to alert_buys/
        alert_sells/alert_transfers off (their routine trading isn't something the
        user asked to be paged for) but alert_launches on, since a further launch
        from a known dev is exactly what this phase exists to catch. Returns
        (wallet, created)."""
        existing = await self.get_wallet(address)
        if existing is not None:
            return existing, False
        wallet = TrackedWallet(
            address=address,
            status=WalletStatus.ACTIVE.value,
            source=WalletSource.AUTO_DEV.value,
            alert_buys=False,
            alert_sells=False,
            alert_transfers=False,
            alert_launches=True,
        )
        self._session.add(wallet)
        await self._session.flush()
        return wallet, True

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

    async def list_tokens_by_creator(self, creator_address: str) -> list[Token]:
        result = await self._session.execute(
            select(Token).where(Token.creator_address == creator_address).order_by(Token.first_seen_slot)
        )
        return list(result.scalars().all())

    async def list_trades_for_wallet(self, wallet_address: str) -> list[TokenTrade]:
        result = await self._session.execute(
            select(TokenTrade).where(TokenTrade.wallet_address == wallet_address).order_by(TokenTrade.slot)
        )
        return list(result.scalars().all())

    async def list_activity_for_wallet(self, wallet_address: str) -> list[WalletActivity]:
        result = await self._session.execute(
            select(WalletActivity).where(WalletActivity.wallet_address == wallet_address).order_by(WalletActivity.slot)
        )
        return list(result.scalars().all())

    async def get_dev_risk_signal(self, creator_address: str, pattern_type: str) -> DevRiskSignal | None:
        result = await self._session.execute(
            select(DevRiskSignal).where(
                DevRiskSignal.creator_address == creator_address, DevRiskSignal.pattern_type == pattern_type
            )
        )
        return result.scalar_one_or_none()

    async def list_dev_risk_signals(self, creator_address: str) -> list[DevRiskSignal]:
        result = await self._session.execute(
            select(DevRiskSignal).where(DevRiskSignal.creator_address == creator_address)
        )
        return list(result.scalars().all())

    async def upsert_dev_risk_signal(
        self, *, creator_address: str, pattern_type: str, severity: str, evidence: dict
    ) -> tuple[DevRiskSignal, bool]:
        """Insert or update the one row for (creator_address, pattern_type).
        Returns (row, escalated) where escalated is True the first time this
        pattern is seen for this creator, or when severity increases relative to
        what was previously stored — the signal WalletTracker/TokenTracker use to
        decide whether a fresh alert is warranted, so re-detecting the same
        standing MEDIUM pattern on every subsequent launch doesn't spam Discord."""
        existing = await self.get_dev_risk_signal(creator_address, pattern_type)
        severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if existing is None:
            row = DevRiskSignal(
                creator_address=creator_address, pattern_type=pattern_type, severity=severity, evidence=evidence
            )
            self._session.add(row)
            await self._session.flush()
            return row, True
        escalated = severity_rank.get(severity, 0) > severity_rank.get(existing.severity, 0)
        existing.severity = severity
        existing.evidence = evidence
        existing.updated_at = utc_now()
        await self._session.flush()
        return existing, escalated

    async def delete_dev_risk_signal(self, creator_address: str, pattern_type: str) -> None:
        existing = await self.get_dev_risk_signal(creator_address, pattern_type)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
