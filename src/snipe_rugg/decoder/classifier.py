"""DecodedTransaction + a subject wallet -> business-level events (spec section 9).

Trade detection (BUY/SELL/SWAP) is balance-delta based: spending a "quote" asset
(native SOL above a dust threshold, wrapped SOL, or USDC) to receive some other
token is a BUY, the reverse a SELL. A shape that doesn't clearly fit (more than
one non-quote token moved, or no clean quote leg) becomes a lower-confidence
SWAP rather than a forced BUY/SELL — spec section 62's "never a single-signal
verdict" principle applies here too: don't overclaim from an ambiguous shape.

Any of the wallet's balance movement not claimed by the trade is reported
separately as TRANSFER, so nothing silently disappears. MINT/BURN/APPROVAL/
TOKEN_CREATE/ACCOUNT_CREATE/ACCOUNT_CLOSE/STAKE/UNSTAKE come from the RPC's own
parsed System/Token/Stake instructions.

LIQUIDITY_ADD/LIQUIDITY_REMOVE are NOT implemented here — they need each DEX's
specific instruction layout (there's no generic "parsed" form for Pump.fun /
PumpSwap / Raydium the way there is for System/Token/Stake), and are Phase 3
work alongside graduation detection.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from snipe_rugg.decoder.constants import (
    LAMPORTS_PER_SOL,
    QUOTE_MINTS,
    dex_label,
    has_external_program,
)
from snipe_rugg.decoder.models import (
    DecodedTransaction,
    EventType,
    NormalizedActivity,
    NormalizedTrade,
    SplBalanceChange,
)

# Below this many lamports, a SOL balance delta is assumed to be network/priority
# fee noise rather than a deliberate transfer - keeps "received a token, paid
# nothing for it" from registering as a dust-sized BUY.
DUST_LAMPORTS_THRESHOLD = 100_000  # 0.0001 SOL

Event = NormalizedTrade | NormalizedActivity


def classify(tx: DecodedTransaction, wallet: str) -> list[Event]:
    events: list[Event] = []
    if not tx.success:
        return events

    trade = _classify_trade(tx, wallet)
    consumed_sol = False
    consumed_mints: set[str] = set()
    if trade is not None:
        events.append(trade)
        consumed_sol = trade.token_in == "SOL" or trade.token_out == "SOL"
        consumed_mints |= {m for m in (trade.token_in, trade.token_out) if m and m != "SOL"}

    # Instruction-based activities (MINT/BURN/...) run before the balance-transfer
    # fallback so a mint/burn's own balance movement isn't *also* reported as a
    # generic TRANSFER of the same mint.
    instruction_activities = _classify_instruction_activities(tx, wallet)
    events.extend(instruction_activities)
    consumed_mints |= {
        a.mint for a in instruction_activities if a.event_type in (EventType.MINT, EventType.BURN) and a.mint
    }

    events.extend(_classify_balance_transfers(tx, wallet, consumed_sol=consumed_sol, consumed_mints=consumed_mints))
    return events


def _classify_trade(tx: DecodedTransaction, wallet: str) -> NormalizedTrade | None:
    if not has_external_program(tx.programs):
        # No DEX/AMM/aggregator was invoked, so this can't be a trade - e.g.
        # minting tokens to yourself pays SOL rent and receives tokens, which is
        # the same balance-delta shape as a BUY but isn't one.
        return None

    spl_deltas = tx.spl_deltas_for_owner(wallet)
    base_deltas = [d for d in spl_deltas if d.mint not in QUOTE_MINTS]
    quote_spl_deltas = [d for d in spl_deltas if d.mint in QUOTE_MINTS]

    sol_delta_lamports = tx.sol_delta_for(wallet)
    quote_legs: list[tuple[str, Decimal]] = []
    if abs(sol_delta_lamports) > DUST_LAMPORTS_THRESHOLD:
        quote_legs.append(("SOL", Decimal(sol_delta_lamports) / LAMPORTS_PER_SOL))
    quote_legs.extend((d.mint, d.delta_ui_amount) for d in quote_spl_deltas)

    if len(base_deltas) != 1 or len(quote_legs) != 1:
        return _classify_ambiguous_swap(tx, wallet, base_deltas, quote_legs)

    base = base_deltas[0]
    quote_mint, quote_amount = quote_legs[0]

    if quote_amount < 0 and base.delta_ui_amount > 0:
        side = EventType.BUY
        token_in, amount_in = quote_mint, -quote_amount
        token_out, amount_out = base.mint, base.delta_ui_amount
    elif quote_amount > 0 and base.delta_ui_amount < 0:
        side = EventType.SELL
        token_in, amount_in = base.mint, -base.delta_ui_amount
        token_out, amount_out = quote_mint, quote_amount
    else:
        return _classify_ambiguous_swap(tx, wallet, base_deltas, quote_legs)

    return NormalizedTrade(
        wallet=wallet,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=amount_out,
        side=side,
        program=dex_label(tx.programs),
        slot=tx.slot,
        block_time=tx.block_time,
        signature=tx.signature,
        confidence="high",
    )


def _classify_ambiguous_swap(
    tx: DecodedTransaction,
    wallet: str,
    base_deltas: list[SplBalanceChange],
    quote_legs: list[tuple[str, Decimal]],
) -> NormalizedTrade | None:
    if not base_deltas or not quote_legs:
        return None
    largest_base = max(base_deltas, key=lambda d: abs(d.delta_ui_amount))
    largest_quote_mint, largest_quote_amount = max(quote_legs, key=lambda leg: abs(leg[1]))
    if largest_base.delta_ui_amount > 0:
        token_in, amount_in = largest_quote_mint, abs(largest_quote_amount)
        token_out, amount_out = largest_base.mint, largest_base.delta_ui_amount
    else:
        token_in, amount_in = largest_base.mint, abs(largest_base.delta_ui_amount)
        token_out, amount_out = largest_quote_mint, abs(largest_quote_amount)
    return NormalizedTrade(
        wallet=wallet,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=amount_out,
        side=EventType.SWAP,
        program=dex_label(tx.programs),
        slot=tx.slot,
        block_time=tx.block_time,
        signature=tx.signature,
        confidence="low",
    )


def _classify_balance_transfers(
    tx: DecodedTransaction, wallet: str, *, consumed_sol: bool, consumed_mints: set[str]
) -> list[NormalizedActivity]:
    events: list[NormalizedActivity] = []

    sol_delta = tx.sol_delta_for(wallet)
    if not consumed_sol and abs(sol_delta) > DUST_LAMPORTS_THRESHOLD:
        events.append(
            _activity(
                tx,
                wallet,
                EventType.TRANSFER,
                mint="SOL",
                amount=Decimal(sol_delta) / LAMPORTS_PER_SOL,
                counterparty=_find_transfer_counterparty(tx, wallet, mint="SOL"),
            )
        )

    for delta in tx.spl_deltas_for_owner(wallet):
        if delta.mint in consumed_mints:
            continue
        events.append(
            _activity(
                tx,
                wallet,
                EventType.TRANSFER,
                mint=delta.mint,
                amount=delta.delta_ui_amount,
                counterparty=_find_transfer_counterparty(tx, wallet, mint=delta.mint),
            )
        )

    return events


def _account_owner(tx: DecodedTransaction, account: str | None) -> str | None:
    if account is None:
        return None
    for change in tx.spl_balance_changes:
        if change.account == account:
            return change.owner
    return None


def _find_transfer_counterparty(tx: DecodedTransaction, wallet: str, *, mint: str) -> str | None:
    """The other side of a balance-delta-detected transfer, read from the
    RPC's own already-parsed transfer instruction(s) rather than inferred
    from deltas alone (see "Why balance deltas" in ARCHITECTURE.md — this is
    the same "read what's already parsed" idea, applied to counterparty
    resolution). Returns None, not a guess, when no single parsed
    instruction explains the movement (e.g. a complex multi-hop transaction)
    — found via testing traders/backfill.py, which is the first caller to
    actually depend on this field being populated from a real transaction
    rather than a hand-built test fixture."""
    for instr in tx.all_instructions():
        parsed = instr.get("parsed")
        if not isinstance(parsed, dict):
            continue
        program, itype = instr.get("program"), parsed.get("type")
        info = parsed.get("info") or {}

        if mint == "SOL" and program == "system" and itype in ("transfer", "transferWithSeed"):
            source, destination = info.get("source"), info.get("destination")
            if wallet == source:
                return destination
            if wallet == destination:
                return source
        elif mint != "SOL" and program in ("spl-token", "spl-token-2022") and itype in ("transfer", "transferChecked"):
            source_owner = _account_owner(tx, info.get("source"))
            dest_owner = _account_owner(tx, info.get("destination"))
            if wallet == source_owner:
                return dest_owner
            if wallet == dest_owner:
                return source_owner
    return None


def _classify_instruction_activities(tx: DecodedTransaction, wallet: str) -> list[NormalizedActivity]:
    events: list[NormalizedActivity] = []
    for instr in tx.all_instructions():
        parsed = instr.get("parsed")
        if not isinstance(parsed, dict):
            continue
        itype = parsed.get("type")
        info = parsed.get("info") or {}
        program = instr.get("program")

        if program in ("spl-token", "spl-token-2022"):
            events.extend(_classify_token_instruction(tx, wallet, itype, info))
        elif program == "system" and itype == "createAccount":
            new_account, source = info.get("newAccount"), info.get("source")
            if wallet in (new_account, source):
                events.append(
                    _activity(tx, wallet, EventType.ACCOUNT_CREATE, counterparty=new_account, details={"funded_by": source})
                )
        elif program == "stake":
            events.extend(_classify_stake_instruction(tx, wallet, itype, info))
    return events


def _classify_token_instruction(
    tx: DecodedTransaction, wallet: str, itype: str | None, info: dict[str, Any]
) -> list[NormalizedActivity]:
    amount = _decimal_amount(info)

    if itype in ("mintTo", "mintToChecked") and info.get("mintAuthority") == wallet:
        return [_activity(tx, wallet, EventType.MINT, mint=info.get("mint"), amount=amount)]
    if itype in ("burn", "burnChecked") and info.get("authority") == wallet:
        return [_activity(tx, wallet, EventType.BURN, mint=info.get("mint"), amount=amount)]
    if itype in ("approve", "approveChecked") and info.get("owner") == wallet:
        return [_activity(tx, wallet, EventType.APPROVAL, mint=info.get("mint"), amount=amount, counterparty=info.get("delegate"))]
    if itype in ("initializeMint", "initializeMint2") and info.get("mintAuthority") == wallet:
        return [_activity(tx, wallet, EventType.TOKEN_CREATE, mint=info.get("mint"))]
    if itype in ("initializeAccount", "initializeAccount2", "initializeAccount3") and info.get("owner") == wallet:
        return [_activity(tx, wallet, EventType.ACCOUNT_CREATE, mint=info.get("mint"), counterparty=info.get("account"))]
    if itype == "closeAccount" and info.get("owner") == wallet:
        return [_activity(tx, wallet, EventType.ACCOUNT_CLOSE, counterparty=info.get("account"))]
    return []


def _classify_stake_instruction(
    tx: DecodedTransaction, wallet: str, itype: str | None, info: dict[str, Any]
) -> list[NormalizedActivity]:
    if itype == "delegate" and info.get("stakeAuthority") == wallet:
        return [_activity(tx, wallet, EventType.STAKE, counterparty=info.get("voteAccount"))]
    if itype == "deactivate" and info.get("stakeAuthority") == wallet:
        return [_activity(tx, wallet, EventType.UNSTAKE)]
    return []


def _decimal_amount(info: dict[str, Any]) -> Decimal | None:
    token_amount = info.get("tokenAmount")
    if isinstance(token_amount, dict) and token_amount.get("amount") is not None:
        return Decimal(token_amount["amount"]) / (Decimal(10) ** token_amount.get("decimals", 0))
    if info.get("amount") is not None:
        return Decimal(info["amount"])
    return None


def _activity(
    tx: DecodedTransaction,
    wallet: str,
    event_type: EventType,
    *,
    mint: str | None = None,
    amount: Decimal | None = None,
    counterparty: str | None = None,
    details: dict[str, Any] | None = None,
) -> NormalizedActivity:
    return NormalizedActivity(
        wallet=wallet,
        event_type=event_type,
        mint=mint,
        amount=amount,
        counterparty=counterparty,
        slot=tx.slot,
        block_time=tx.block_time,
        signature=tx.signature,
        details=details or {},
    )
