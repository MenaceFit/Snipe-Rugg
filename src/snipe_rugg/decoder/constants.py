"""Well-known Solana program IDs and mints, used to label which DEX/program a
transaction touched.

These are for display/classification only — this codebase never builds or signs
a transaction, so a stale address here means a mislabeled alert, not a lost
transaction. Still, re-verify against each project's current docs before
leaning on these for anything higher-stakes than that (spec section 101).
Sources checked at write time: pump-fun/pump-public-docs (Pump.fun, PumpSwap),
Raydium docs (AMM v4), Jupiter docs (aggregator v6).
"""
from __future__ import annotations

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
STAKE_PROGRAM = "Stake11111111111111111111111111111111111"

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_AMM_V4_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
JUPITER_V6_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

DEX_PROGRAM_LABELS: dict[str, str] = {
    PUMP_FUN_PROGRAM: "Pump.fun",
    PUMPSWAP_PROGRAM: "PumpSwap",
    RAYDIUM_AMM_V4_PROGRAM: "Raydium",
    JUPITER_V6_PROGRAM: "Jupiter",
}

WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# "SOL-equivalent" counter-assets for buy/sell classification (spec section 12/13):
# spending one of these to receive some other token is a BUY, and the reverse is
# a SELL. Native SOL (lamports, not an SPL mint at all) is handled separately in
# the classifier via the SOL balance deltas.
QUOTE_MINTS: frozenset[str] = frozenset({WRAPPED_SOL_MINT, USDC_MINT})

SOL_DECIMALS = 9
USDC_DECIMALS = 6
LAMPORTS_PER_SOL = 10**SOL_DECIMALS

# Native wallet-management programs. A transaction touching only these never
# constitutes a trade by itself (e.g. minting tokens to yourself pays SOL rent
# and receives tokens - the same balance-delta shape as a BUY, but it isn't
# one). The classifier requires at least one program outside this set before
# it will call something a trade.
INFRASTRUCTURE_PROGRAMS: frozenset[str] = frozenset(
    {SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022_PROGRAM, ASSOCIATED_TOKEN_PROGRAM, STAKE_PROGRAM}
)


def dex_label(program_ids: list[str]) -> str | None:
    for program_id in program_ids:
        label = DEX_PROGRAM_LABELS.get(program_id)
        if label is not None:
            return label
    return None


def has_external_program(program_ids: list[str]) -> bool:
    return any(p not in INFRASTRUCTURE_PROGRAMS for p in program_ids)
    return None
