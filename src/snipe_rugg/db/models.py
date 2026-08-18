"""SQLAlchemy models for wallet tracking (spec section 70, Phase 2 subset).

Only the tables Phase 2 needs: tracked_wallets, wallet_groups,
wallet_group_members, token_trades, wallet_activity, alerts. Everything else in
spec section 70 (tokens, launches, creators, creator_clusters, strategy_signals,
paper_trades, positions, portfolio_snapshots, known_entities, api_sources)
belongs to a later phase and is added when that phase actually needs it.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.base import Base


class WalletStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


def _new_id() -> str:
    return uuid.uuid4().hex


class TrackedWallet(Base):
    __tablename__ = "tracked_wallets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=WalletStatus.ACTIVE.value)

    alert_buys: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_sells: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_transfers: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_launches: Mapped[bool] = mapped_column(Boolean, default=True)
    min_alert_sol: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    group_memberships: Mapped[list[WalletGroupMember]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )


class WalletGroup(Base):
    __tablename__ = "wallet_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    memberships: Mapped[list[WalletGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class WalletGroupMember(Base):
    __tablename__ = "wallet_group_members"
    __table_args__ = (UniqueConstraint("group_id", "wallet_id", name="uq_group_wallet"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    group_id: Mapped[str] = mapped_column(ForeignKey("wallet_groups.id"))
    wallet_id: Mapped[str] = mapped_column(ForeignKey("tracked_wallets.id"))

    group: Mapped[WalletGroup] = relationship(back_populates="memberships")
    wallet: Mapped[TrackedWallet] = relationship(back_populates="group_memberships")


class TokenTrade(Base):
    __tablename__ = "token_trades"
    __table_args__ = (UniqueConstraint("signature", "wallet_address", name="uq_trade_signature_wallet"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(String(128), index=True)
    side: Mapped[str] = mapped_column(String(16))
    token_in: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_out: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_in: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    amount_out: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    program: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16))
    slot: Mapped[int] = mapped_column()
    block_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WalletActivity(Base):
    __tablename__ = "wallet_activity"
    __table_args__ = (
        UniqueConstraint("signature", "wallet_address", "event_type", name="uq_activity_signature_wallet_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    mint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    slot: Mapped[int] = mapped_column()
    block_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discord_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
