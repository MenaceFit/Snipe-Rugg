"""Transaction decoder output contracts (spec sections 9-14).

DecodedTransaction is a provider-independent structure produced from a single
getTransaction(jsonParsed) response. EventType/NormalizedTrade are the
business-level classifier output that spec section 11 asks for — left
undefined in Phase 1 until there was an actual decoder to produce them.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    TRANSFER = "TRANSFER"
    SWAP = "SWAP"
    TOKEN_CREATE = "TOKEN_CREATE"
    LIQUIDITY_ADD = "LIQUIDITY_ADD"
    LIQUIDITY_REMOVE = "LIQUIDITY_REMOVE"
    MINT = "MINT"
    BURN = "BURN"
    APPROVAL = "APPROVAL"
    STAKE = "STAKE"
    UNSTAKE = "UNSTAKE"
    ACCOUNT_CREATE = "ACCOUNT_CREATE"
    ACCOUNT_CLOSE = "ACCOUNT_CLOSE"


class SolBalanceChange(BaseModel):
    account: str
    account_index: int
    pre_lamports: int
    post_lamports: int

    @property
    def delta_lamports(self) -> int:
        return self.post_lamports - self.pre_lamports


class SplBalanceChange(BaseModel):
    account: str
    owner: str | None
    mint: str
    decimals: int
    account_index: int
    pre_amount: int
    post_amount: int

    @property
    def delta_amount(self) -> int:
        return self.post_amount - self.pre_amount

    @property
    def delta_ui_amount(self) -> Decimal:
        scale = Decimal(10) ** self.decimals
        return (Decimal(self.post_amount) - Decimal(self.pre_amount)) / scale


class DecodedTransaction(BaseModel):
    signature: str
    slot: int
    block_time: datetime | None = None
    success: bool
    err: Any | None = None
    signer: str | None = None
    programs: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    instructions: list[dict[str, Any]] = Field(default_factory=list)
    inner_instructions: list[dict[str, Any]] = Field(default_factory=list)
    sol_balance_changes: list[SolBalanceChange] = Field(default_factory=list)
    spl_balance_changes: list[SplBalanceChange] = Field(default_factory=list)
    log_messages: list[str] = Field(default_factory=list)
    fee_lamports: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

    def sol_delta_for(self, account: str) -> int:
        return sum(c.delta_lamports for c in self.sol_balance_changes if c.account == account)

    def spl_deltas_for_owner(self, owner: str) -> list[SplBalanceChange]:
        return [c for c in self.spl_balance_changes if c.owner == owner]

    def all_instructions(self) -> list[dict[str, Any]]:
        return [*self.instructions, *self.inner_instructions]


class NormalizedTrade(BaseModel):
    """spec section 11."""

    wallet: str
    token_in: str | None
    token_out: str | None
    amount_in: Decimal | None
    amount_out: Decimal | None
    side: EventType
    program: str | None
    slot: int
    block_time: datetime | None
    signature: str
    confidence: str


class NormalizedActivity(BaseModel):
    """Generic non-trade business event (TRANSFER/MINT/BURN/APPROVAL/STAKE/
    UNSTAKE/ACCOUNT_CREATE/ACCOUNT_CLOSE/TOKEN_CREATE) for a subject wallet."""

    wallet: str
    event_type: EventType
    mint: str | None = None
    amount: Decimal | None = None
    counterparty: str | None = None
    slot: int
    block_time: datetime | None
    signature: str
    details: dict[str, Any] = Field(default_factory=dict)
