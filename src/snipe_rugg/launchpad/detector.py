"""Launchpad detection: which platform (if any) a transaction created a new
token on (spec section 16-18). Pump.fun is priority 1 (spec section 18); other
launchpads plug into the same LaunchpadDetector interface without touching the
tracker.

Pump.fun's own `create` instruction is a custom Anchor instruction with opaque
(unparsed) data — decoding it would mean hand-rolling an Anchor/Borsh decoder
against a specific discriminator and byte layout, which this project avoids
without a verified IDL (spec section 101's "verify before implementing," taken
seriously: getting a byte offset wrong wouldn't fail loudly, it would silently
produce a wrong mint or a corrupted name). Instead: `create` always invokes SPL
Token's initializeMint2 as an inner instruction to actually create the mint,
and *that* instruction the RPC parses for us — so the mint address comes from
there, not from decoding Pump.fun's own instruction. Token name/symbol/uri only
exist in Pump.fun's opaque instruction data (or a Metaplex metadata account
this project doesn't yet fetch), so LaunchEvent has no name/ticker field — a
documented gap, not a silent omission.
"""
from __future__ import annotations

from typing import Any, Protocol

from snipe_rugg.decoder.constants import PUMP_FUN_PROGRAM
from snipe_rugg.decoder.models import DecodedTransaction
from snipe_rugg.launchpad.models import LaunchEvent

_INITIALIZE_MINT_TYPES = ("initializeMint", "initializeMint2")


def _find_initialize_mint(tx: DecodedTransaction) -> dict[str, Any] | None:
    for instr in tx.all_instructions():
        parsed = instr.get("parsed")
        if not isinstance(parsed, dict):
            continue
        if instr.get("program") in ("spl-token", "spl-token-2022") and parsed.get("type") in _INITIALIZE_MINT_TYPES:
            info = parsed.get("info")
            return info if isinstance(info, dict) else {}
    return None


class LaunchpadDetector(Protocol):
    def detect(self, tx: DecodedTransaction) -> LaunchEvent | None: ...


class PumpFunLaunchDetector:
    """Priority 1 (spec section 18)."""

    def detect(self, tx: DecodedTransaction) -> LaunchEvent | None:
        if PUMP_FUN_PROGRAM not in tx.programs:
            return None
        mint_info = _find_initialize_mint(tx)
        if not mint_info or not mint_info.get("mint") or tx.signer is None:
            return None
        # Pump.fun also supports a USDC-paired flow (spec section 16) that
        # isn't distinguished here without decoding its opaque instruction
        # data — defaults to SOL, the overwhelmingly common case.
        return LaunchEvent(
            mint=mint_info["mint"],
            creator=tx.signer,
            launchpad="Pump.fun",
            pair="SOL",
            slot=tx.slot,
            block_time=tx.block_time,
            signature=tx.signature,
        )


class GenericTokenCreationDetector:
    """Fallback for a mint created outside any known launchpad (spec section 18)."""

    def detect(self, tx: DecodedTransaction) -> LaunchEvent | None:
        for instr in tx.all_instructions():
            parsed = instr.get("parsed")
            if not isinstance(parsed, dict) or instr.get("program") not in ("spl-token", "spl-token-2022"):
                continue
            if parsed.get("type") not in _INITIALIZE_MINT_TYPES:
                continue
            info = parsed.get("info") or {}
            mint_authority, mint = info.get("mintAuthority"), info.get("mint")
            if not mint_authority or not mint:
                continue
            return LaunchEvent(
                mint=mint,
                creator=mint_authority,
                launchpad="Generic SPL",
                pair=None,
                slot=tx.slot,
                block_time=tx.block_time,
                signature=tx.signature,
            )
        return None


_DETECTORS: tuple[LaunchpadDetector, ...] = (PumpFunLaunchDetector(), GenericTokenCreationDetector())


def detect_launch(tx: DecodedTransaction) -> LaunchEvent | None:
    for detector in _DETECTORS:
        event = detector.detect(tx)
        if event is not None:
            return event
    return None
